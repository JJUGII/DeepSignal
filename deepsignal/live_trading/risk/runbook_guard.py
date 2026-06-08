"""pre-trade runbook 리포트 검증 — `live-approve --execute` 직전 강제 ([실전-11])."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_PRICE_EPS = 0.0001


@dataclass
class RunbookGuardResult:
    passed: bool
    status: str
    message: str
    report_path: str | None
    report_age_seconds: float | None
    matched_fields: dict[str, Any] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _norm_symbol(sym: str | None) -> str:
    s = (sym or "").strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def _norm_plan_path(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).resolve().as_posix()
    except OSError:
        return Path(path).as_posix()


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    s = str(ts).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _same_price(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= _PRICE_EPS
    except (TypeError, ValueError):
        return False


def find_latest_pre_trade_runbook(output_dir: str | Path = "outputs") -> Path | None:
    """`outputs/pre_trade_runbook_*.json` 중 수정 시각 최신 파일."""
    root = Path(output_dir)
    if not root.is_dir():
        return None
    candidates = list(root.glob("pre_trade_runbook_*.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_runbook_report(path: str | Path) -> dict[str, Any]:
    """runbook JSON 로드."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("runbook report root must be a JSON object")
    return data


def _report_timestamp(report: dict[str, Any]) -> datetime | None:
    for key in ("finished_at", "started_at", "generated_at"):
        dt = _parse_ts(str(report.get(key) or ""))
        if dt is not None:
            return dt
    summary = report.get("summary")
    if isinstance(summary, dict):
        dt = _parse_ts(str(summary.get("generated_at") or summary.get("finished_at") or ""))
        if dt is not None:
            return dt
    return None


def _summary_field(report: dict[str, Any], key: str) -> Any:
    summary = report.get("summary")
    if isinstance(summary, dict) and key in summary:
        return summary.get(key)
    return report.get(key)


def validate_pre_trade_runbook(
    *,
    report_path: str | Path | None = None,
    output_dir: str | Path = "outputs",
    max_age_minutes: int = 10,
    expected_plan_path: str | None = None,
    expected_symbol: str | None = None,
    expected_quantity: int | None = None,
    expected_limit_price: float | None = None,
    now: datetime | None = None,
) -> RunbookGuardResult:
    """
    최근 `PRE_TRADE_READY` pre-trade runbook 리포트 검증.

    `report_path`가 없으면 `output_dir`에서 최신 `pre_trade_runbook_*.json` 사용.
    """
    now_dt = now or datetime.now()
    path: Path | None
    if report_path:
        path = Path(report_path)
        if not path.is_file():
            return RunbookGuardResult(
                passed=False,
                status="RUNBOOK_NOT_FOUND",
                message=f"pre-trade runbook file not found: {path}",
                report_path=str(path),
                report_age_seconds=None,
            )
    else:
        found = find_latest_pre_trade_runbook(output_dir)
        if found is None:
            return RunbookGuardResult(
                passed=False,
                status="RUNBOOK_NOT_FOUND",
                message="recent PRE_TRADE_READY runbook not found or expired",
                report_path=None,
                report_age_seconds=None,
            )
        path = found

    try:
        report = load_runbook_report(path)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        return RunbookGuardResult(
            passed=False,
            status="RUNBOOK_NOT_FOUND",
            message=f"invalid runbook report: {e}",
            report_path=path.as_posix(),
            report_age_seconds=None,
        )

    raw = dict(report)
    mismatches: list[str] = []
    matched: dict[str, Any] = {"report_path": path.as_posix()}

    mode = str(report.get("mode") or "")
    if mode != "pre_trade":
        mismatches.append(f"mode must be pre_trade, got {mode!r}")

    final_status = str(report.get("final_status") or "")
    if final_status != "PRE_TRADE_READY":
        return RunbookGuardResult(
            passed=False,
            status="RUNBOOK_NOT_READY",
            message=f"runbook final_status is {final_status!r}, expected PRE_TRADE_READY",
            report_path=path.as_posix(),
            report_age_seconds=None,
            matched_fields=matched,
            mismatches=mismatches or [f"final_status={final_status!r}"],
            raw=raw,
        )

    ts = _report_timestamp(report)
    age_sec: float | None = None
    if ts is None:
        mismatches.append("report timestamp missing (finished_at / started_at)")
    else:
        ts_cmp = ts.replace(tzinfo=None) if ts.tzinfo else ts
        now_cmp = now_dt.replace(tzinfo=None) if now_dt.tzinfo else now_dt
        age_sec = (now_cmp - ts_cmp).total_seconds()
        if age_sec < 0:
            age_sec = 0.0
        matched["report_age_seconds"] = age_sec
        if age_sec > float(max_age_minutes) * 60.0:
            return RunbookGuardResult(
                passed=False,
                status="RUNBOOK_EXPIRED",
                message=(
                    f"pre-trade runbook expired: age {age_sec:.0f}s "
                    f"> max_age_minutes={max_age_minutes}"
                ),
                report_path=path.as_posix(),
                report_age_seconds=age_sec,
                matched_fields=matched,
                mismatches=mismatches,
                raw=raw,
            )

    if expected_plan_path:
        exp_plan = _norm_plan_path(expected_plan_path)
        rep_plan = _norm_plan_path(str(_summary_field(report, "plan_path") or ""))
        matched["plan_path"] = rep_plan
        if exp_plan and rep_plan and exp_plan != rep_plan:
            mismatches.append(f"plan_path mismatch: report={rep_plan!r} expected={exp_plan!r}")

    if expected_symbol:
        exp_sym = _norm_symbol(expected_symbol)
        rep_sym = _norm_symbol(str(_summary_field(report, "symbol") or ""))
        matched["symbol"] = rep_sym
        if exp_sym != rep_sym:
            mismatches.append(f"symbol mismatch: report={rep_sym!r} expected={exp_sym!r}")

    if expected_quantity is not None:
        rep_qty_raw = _summary_field(report, "quantity")
        try:
            rep_qty = int(rep_qty_raw)
        except (TypeError, ValueError):
            rep_qty = -1
        matched["quantity"] = rep_qty
        if rep_qty != int(expected_quantity):
            mismatches.append(f"quantity mismatch: report={rep_qty} expected={expected_quantity}")

    if expected_limit_price is not None:
        rep_lp_raw = _summary_field(report, "limit_price")
        try:
            rep_lp = float(rep_lp_raw) if rep_lp_raw is not None else None
        except (TypeError, ValueError):
            rep_lp = None
        matched["limit_price"] = rep_lp
        if not _same_price(rep_lp, float(expected_limit_price)):
            mismatches.append(
                f"limit_price mismatch: report={rep_lp!r} expected={expected_limit_price!r}"
            )

    if mismatches:
        return RunbookGuardResult(
            passed=False,
            status="RUNBOOK_MISMATCH",
            message="; ".join(mismatches),
            report_path=path.as_posix(),
            report_age_seconds=age_sec,
            matched_fields=matched,
            mismatches=mismatches,
            raw=raw,
        )

    return RunbookGuardResult(
        passed=True,
        status="RUNBOOK_OK",
        message="pre-trade runbook validation passed",
        report_path=path.as_posix(),
        report_age_seconds=age_sec,
        matched_fields=matched,
        mismatches=[],
        raw=raw,
    )


def runbook_guard_result_to_audit_fields(result: RunbookGuardResult) -> dict[str, Any]:
    """`live_approval_audit`용 runbook guard 필드."""
    return {
        "require_pre_trade_runbook": True,
        "pre_trade_runbook_guard": {
            "passed": result.passed,
            "status": result.status,
            "message": result.message,
            "matched_fields": dict(result.matched_fields),
            "mismatches": list(result.mismatches),
            "warnings": list(result.warnings),
        },
        "pre_trade_runbook_passed": result.passed,
        "pre_trade_runbook_path": result.report_path,
        "pre_trade_runbook_age_seconds": result.report_age_seconds,
    }
