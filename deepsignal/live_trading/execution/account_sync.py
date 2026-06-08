"""실계좌 스냅샷 파일·DB 저장 ([실전-5]~[실전-6]). `paper_*` DB와 분리."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from deepsignal.live_trading.kis_broker import KISBroker


def build_account_snapshot_payload(broker: KISBroker) -> dict[str, Any]:
    """잔고·포지션 조회 결과를 JSON용 dict로 묶는다."""
    cash = broker.get_cash_balance()
    positions = broker.get_positions()
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "kis_env": broker.config.env,
        "cash": asdict(cash),
        "positions": [asdict(p) for p in positions],
    }


def summarize_kis_balance_raw(raw: dict[str, Any] | None) -> dict[str, Any]:
    """KIS 잔고조회 raw 응답의 안전한 구조 요약. 값은 저장하지 않고 키·건수만 남긴다."""
    if not isinstance(raw, dict):
        return {"available": False}

    def _rows(name: str) -> dict[str, Any]:
        obj = raw.get(name) or raw.get(name.upper()) or raw.get(name.capitalize())
        rows = obj if isinstance(obj, list) else ([] if obj is None else [obj])
        key_set: set[str] = set()
        dict_rows = 0
        for row in rows:
            if isinstance(row, dict):
                dict_rows += 1
                key_set.update(str(k) for k in row.keys())
        return {
            "row_count": len(rows),
            "dict_row_count": dict_rows,
            "keys": sorted(key_set),
        }

    return {
        "available": True,
        "top_level_keys": sorted(str(k) for k in raw.keys()),
        "rt_cd": str(raw.get("rt_cd") or raw.get("RT_CD") or ""),
        "msg_cd": str(raw.get("msg_cd") or raw.get("MSG_CD") or ""),
        "output1": _rows("output1"),
        "output2": _rows("output2"),
    }


def write_kis_account_debug_summary(
    raw: dict[str, Any] | None,
    *,
    output_dir: str | Path = "outputs",
) -> Path:
    """마스킹된 KIS 잔고조회 구조 요약 저장."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = root / f"kis_debug_account_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    body = {
        "timestamp": now.isoformat(timespec="seconds"),
        "kind": "kis_inquire_balance_shape",
        "summary": summarize_kis_balance_raw(raw),
        "note": "No account number, token, app key, app secret, or raw row values are stored.",
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def persist_live_account_snapshot_to_db(
    db_path: str,
    payload: dict[str, Any],
    *,
    broker: str = "kis",
) -> tuple[int, int, str]:
    """
    `real_positions` / `real_account_snapshots` 에 동일 `snapshot_time`으로 저장.

    Returns:
        (삽입된 포지션 행 수, 계좌 스냅샷 행 수 0 또는 1, snapshot_time)
    """
    from deepsignal.storage.database import save_real_account_snapshot, save_real_positions

    ts = str(payload.get("timestamp") or "").strip() or datetime.now().isoformat(timespec="seconds")
    positions = list(payload.get("positions") or [])
    cash_d = payload.get("cash") or {}
    cash = cash_d.get("cash")
    wdr = cash_d.get("withdrawable_cash")
    mv = 0.0
    for p in positions:
        try:
            mv += float(p.get("market_value") or 0)
        except (TypeError, ValueError):
            pass
    cash_f = float(cash) if cash is not None else None
    wdr_f = float(wdr) if wdr is not None else None
    eq: float | None = None
    if cash_f is not None:
        eq = cash_f + mv

    from deepsignal.analysis.position_peak_tracker import update_position_peaks

    update_position_peaks(db_path, broker, positions, snapshot_time=ts)
    npos = save_real_positions(db_path, ts, broker, positions)
    nsnap = save_real_account_snapshot(
        db_path,
        ts,
        broker,
        cash=cash_f,
        withdrawable_cash=wdr_f,
        total_market_value=mv if mv else None,
        total_equity=eq,
        raw_payload=dict(payload),
    )
    return npos, nsnap, ts


def write_live_account_snapshot_paths(
    payload: dict[str, Any],
    *,
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    """이미 조회된 `payload`를 JSON/Markdown으로 저장."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ymd = now.strftime("%Y%m%d")
    hms = now.strftime("%H%M%S")
    json_path = root / f"live_account_snapshot_{ymd}_{hms}.json"
    md_path = root / "LIVE_ACCOUNT_SNAPSHOT.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cash = payload.get("cash") or {}
    pos_rows = payload.get("positions") or []
    c = cash.get("cash")
    w = cash.get("withdrawable_cash")
    def _fmt_krw(v: Any) -> str:
        try:
            return f"{float(v):,.0f}원"
        except (TypeError, ValueError):
            return str(v) if v is not None else "-"

    lines = [
        "# DeepSignal — 실시간 계좌 현황",
        "",
        f"- 생성 시각: {payload.get('timestamp')}",
        f"- 계좌 환경: `{payload.get('kis_env')}`",
        "",
        "## 현금 잔고",
        "",
        f"- 현금: {_fmt_krw(c)}",
        f"- 출금가능금액: {_fmt_krw(w)}",
        "",
        "## 보유 종목",
        "",
    ]
    for p in pos_rows:
        sym = p.get("symbol", "")
        qty = p.get("quantity", 0)
        avg = p.get("avg_price")
        val = p.get("market_value")
        avg_s = _fmt_krw(avg)
        val_s = _fmt_krw(val)
        lines.append(f"- `{sym}` 수량={qty} 평균단가={avg_s} 평가금액={val_s}")
    if not pos_rows:
        lines.append("- (없음)")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def write_live_account_snapshot(
    broker: KISBroker,
    *,
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    """`outputs/live_account_snapshot_*.json` 및 `LIVE_ACCOUNT_SNAPSHOT.md` 저장."""
    payload = build_account_snapshot_payload(broker)
    return write_live_account_snapshot_paths(payload, output_dir=output_dir)
