"""실계좌 vs DB `real_*` 비교 ([실전-6]). `paper_*`와 무관."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ReconcileIssue:
    symbol: str
    issue_type: str
    broker_quantity: int | None
    db_quantity: int | None
    message: str


@dataclass
class ReconcileResult:
    matched: list[str]
    missing_in_db: list[ReconcileIssue] = field(default_factory=list)
    missing_in_broker: list[ReconcileIssue] = field(default_factory=list)
    quantity_mismatch: list[ReconcileIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    success: bool = True


def _norm_symbol(sym: str | None) -> str:
    s = (sym or "").strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def _qty(obj: Any) -> int:
    if isinstance(obj, Mapping):
        return int(obj.get("quantity") or 0)
    return int(getattr(obj, "quantity", 0) or 0)


def _sym(obj: Any) -> str:
    if isinstance(obj, Mapping):
        return _norm_symbol(str(obj.get("symbol") or ""))
    return _norm_symbol(str(getattr(obj, "symbol", "") or ""))


def reconcile_real_account(
    broker_positions: Sequence[Any],
    db_positions: Sequence[Any],
) -> ReconcileResult:
    """
    브로커 최신 조회 vs DB 최신 `real_positions` 비교.

    - `broker_positions` / `db_positions`: `symbol`·`quantity`를 갖는 dict 또는 `BrokerPosition`.
    """
    bmap: dict[str, int] = {}
    for p in broker_positions:
        s = _sym(p)
        if not s:
            continue
        bmap[s] = _qty(p)

    dmap: dict[str, int] = {}
    for p in db_positions:
        s = _sym(p)
        if not s:
            continue
        dmap[s] = _qty(p)

    matched: list[str] = []
    missing_in_db: list[ReconcileIssue] = []
    missing_in_broker: list[ReconcileIssue] = []
    quantity_mismatch: list[ReconcileIssue] = []

    for sym, bq in sorted(bmap.items()):
        if sym not in dmap:
            missing_in_db.append(
                ReconcileIssue(
                    symbol=sym,
                    issue_type="missing_in_db",
                    broker_quantity=bq,
                    db_quantity=None,
                    message=f"broker has {sym} qty={bq} but DB latest snapshot has no row",
                )
            )
        elif dmap[sym] != bq:
            quantity_mismatch.append(
                ReconcileIssue(
                    symbol=sym,
                    issue_type="quantity_mismatch",
                    broker_quantity=bq,
                    db_quantity=dmap[sym],
                    message=f"{sym}: broker qty={bq} vs db qty={dmap[sym]}",
                )
            )
        else:
            matched.append(sym)

    for sym, dq in sorted(dmap.items()):
        if sym not in bmap:
            missing_in_broker.append(
                ReconcileIssue(
                    symbol=sym,
                    issue_type="missing_in_broker",
                    broker_quantity=None,
                    db_quantity=dq,
                    message=f"DB has {sym} qty={dq} but broker inquiry shows no position",
                )
            )

    success = not missing_in_db and not missing_in_broker and not quantity_mismatch
    warnings: list[str] = []
    if not success:
        warnings.append(
            "WARNING: Real account state mismatch detected. "
            "Do not submit new automated orders before reconciliation."
        )
    if quantity_mismatch:
        syms = ", ".join(i.symbol for i in quantity_mismatch)
        warnings.append(
            f"WARNING: quantity mismatch on {syms} — duplicate or stale-order risk; verify with broker app."
        )

    return ReconcileResult(
        matched=matched,
        missing_in_db=missing_in_db,
        missing_in_broker=missing_in_broker,
        quantity_mismatch=quantity_mismatch,
        warnings=warnings,
        success=success,
    )


def write_reconcile_report_paths(
    result: ReconcileResult,
    *,
    output_dir: str | Path,
    extra: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """`outputs/reconcile_live_account_*.json` 및 `RECONCILE_LIVE_ACCOUNT.md` 저장."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ymd = now.strftime("%Y%m%d")
    hms = now.strftime("%H%M%S")
    json_path = root / f"reconcile_live_account_{ymd}_{hms}.json"
    md_path = root / "RECONCILE_LIVE_ACCOUNT.md"

    body: dict[str, Any] = {
        "timestamp": now.isoformat(timespec="seconds"),
        "success": result.success,
        "matched": result.matched,
        "missing_in_db": [asdict(x) for x in result.missing_in_db],
        "missing_in_broker": [asdict(x) for x in result.missing_in_broker],
        "quantity_mismatch": [asdict(x) for x in result.quantity_mismatch],
        "warnings": result.warnings,
    }
    if extra:
        body["extra"] = extra

    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    success_ko = "✅ 성공" if result.success else "❌ 불일치 있음"
    lines = [
        "# DeepSignal — 계좌 잔고 대사",
        "",
        f"- 생성 시각: {body['timestamp']}",
        f"- 결과: **{success_ko}**",
        "",
        "## 경고",
        "",
    ]
    for w in result.warnings:
        lines.append(f"- {w}")
    if not result.warnings:
        lines.append("- (없음)")
    lines.extend(["", "## 일치 종목", ""])
    for s in result.matched:
        lines.append(f"- `{s}`")
    if not result.matched:
        lines.append("- (없음)")
    lines.extend(["", "## DB에 없는 종목 (증권사에만 있음)", ""])
    for x in result.missing_in_db:
        lines.append(f"- `{x.symbol}` 증권사수량={x.broker_quantity} — {x.message}")
    if not result.missing_in_db:
        lines.append("- (없음)")
    lines.extend(["", "## 증권사에 없는 종목 (DB에만 있음)", ""])
    for x in result.missing_in_broker:
        lines.append(f"- `{x.symbol}` DB수량={x.db_quantity} — {x.message}")
    if not result.missing_in_broker:
        lines.append("- (없음)")
    lines.extend(["", "## 수량 불일치", ""])
    for x in result.quantity_mismatch:
        lines.append(f"- `{x.symbol}` 증권사={x.broker_quantity} DB={x.db_quantity}")
    if not result.quantity_mismatch:
        lines.append("- (없음)")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_latest_reconcile_state(json_path, result, output_dir=root)
    return json_path, md_path


LATEST_RECONCILE_STATE_FILENAME = "LATEST_RECONCILE_STATE.json"


def reconcile_result_from_dict(data: dict[str, Any]) -> ReconcileResult:
    """저장된 reconcile JSON / state dict → `ReconcileResult`."""

    def _issues(key: str) -> list[ReconcileIssue]:
        out: list[ReconcileIssue] = []
        for row in data.get(key) or []:
            if not isinstance(row, dict):
                continue
            out.append(
                ReconcileIssue(
                    symbol=str(row.get("symbol") or ""),
                    issue_type=str(row.get("issue_type") or key),
                    broker_quantity=row.get("broker_quantity"),
                    db_quantity=row.get("db_quantity"),
                    message=str(row.get("message") or ""),
                )
            )
        return out

    success = bool(data.get("success", True))
    return ReconcileResult(
        matched=list(data.get("matched") or []),
        missing_in_db=_issues("missing_in_db"),
        missing_in_broker=_issues("missing_in_broker"),
        quantity_mismatch=_issues("quantity_mismatch"),
        warnings=list(data.get("warnings") or []),
        success=success,
    )


def write_latest_reconcile_state(
    report_json_path: Path,
    result: ReconcileResult,
    *,
    output_dir: str | Path,
) -> Path:
    """`outputs/LATEST_RECONCILE_STATE.json` — guard·운영이 최신 reconcile을 읽기 위함."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / LATEST_RECONCILE_STATE_FILENAME
    body: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "success": result.success,
        "report_path": report_json_path.as_posix(),
        "matched": result.matched,
        "missing_in_db": [asdict(x) for x in result.missing_in_db],
        "missing_in_broker": [asdict(x) for x in result.missing_in_broker],
        "quantity_mismatch": [asdict(x) for x in result.quantity_mismatch],
        "warnings": result.warnings,
    }
    state_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state_path


def load_latest_reconcile_state(
    output_dir: str | Path = "outputs",
) -> ReconcileResult | None:
    """최신 reconcile state 파일 또는 가장 최근 `reconcile_live_account_*.json`."""
    root = Path(output_dir)
    state_path = root / LATEST_RECONCILE_STATE_FILENAME
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return reconcile_result_from_dict(data)
        except (OSError, json.JSONDecodeError):
            pass
    reports = sorted(root.glob("reconcile_live_account_*.json"))
    if not reports:
        return None
    try:
        data = json.loads(reports[-1].read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return reconcile_result_from_dict(data)
    except (OSError, json.JSONDecodeError):
        return None
    return None
