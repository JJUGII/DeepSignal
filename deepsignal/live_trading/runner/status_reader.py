"""Read-only Daily AI workflow status reader.

This module only inspects local files under ``outputs``. It never calls KIS,
Telegram, live-approve, execute paths, or cleanup operations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
import json
from typing import Any

from deepsignal.live_trading.daily_ai_freshness import (
    DailyAIFreshnessPolicy,
    FRESHNESS_MISSING,
    FRESHNESS_STALE,
    FreshnessResult,
    build_daily_ai_freshness,
    freshness_label_ko,
    freshness_results_to_dict,
    freshness_source_label_ko,
    is_fresh,
    resolve_reference_local_date,
)


NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass
class DailyAIWorkflowStatus:
    plan_status: str = NOT_AVAILABLE
    approval_request_status: str = NOT_AVAILABLE
    approval_status: str = NOT_AVAILABLE
    execution_status: str = NOT_AVAILABLE
    report_status: str = NOT_AVAILABLE
    status_report_status: str = NOT_AVAILABLE
    next_action: str = "python main.py daily-ai-trade-plan --broker kis --network --output-dir outputs"
    warnings: list[str] = field(default_factory=list)
    files: dict[str, str | None] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    freshness: dict[str, dict[str, Any]] = field(default_factory=dict)
    freshness_reference_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _root(output_dir: str | Path) -> Path:
    return Path(output_dir)


def _latest(root: Path, pattern: str) -> Path | None:
    matches = [p for p in root.glob(pattern) if p.is_file()]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _status(path: Path | None, *keys: str) -> str:
    data = _read_json(path)
    for key in keys or ("status",):
        if data.get(key) is not None:
            return str(data.get(key))
    return NOT_AVAILABLE


def _posix(path: Path | None) -> str | None:
    return path.as_posix() if path is not None and path.exists() else None


def _freshness_warning(result: FreshnessResult) -> str | None:
    if result.status == FRESHNESS_MISSING:
        return f"{result.target_name} 파일이 없습니다."
    if result.status == FRESHNESS_STALE:
        return result.warning or f"{result.target_name} 파일이 오래되었습니다."
    return None


def _plan_usable(freshness: dict[str, FreshnessResult], checks: dict[str, bool]) -> bool:
    plan = freshness.get("plan")
    latest = freshness.get("latest_order_plan")
    return (
        checks["plan_markdown_exists"]
        and checks["plan_json_exists"]
        and checks["latest_order_plan_exists"]
        and plan is not None
        and latest is not None
        and is_fresh(plan)
        and is_fresh(latest)
    )


def _approval_usable(freshness: dict[str, FreshnessResult], checks: dict[str, bool]) -> bool:
    approval = freshness.get("approval")
    return checks["approval_audit_exists"] and approval is not None and is_fresh(approval)


def _approval_request_usable(freshness: dict[str, FreshnessResult], checks: dict[str, bool]) -> bool:
    if not checks["approval_request_exists"]:
        return False
    approval = freshness.get("approval")
    if approval is None:
        return True
    return approval.status != FRESHNESS_STALE


def read_daily_ai_workflow_status(
    output_dir: str | Path = "outputs",
    *,
    freshness_date: str | date | None = None,
    policy: DailyAIFreshnessPolicy | None = None,
) -> DailyAIWorkflowStatus:
    root = _root(output_dir)
    policy = policy or DailyAIFreshnessPolicy()
    freshness_map = build_daily_ai_freshness(
        root,
        policy=policy,
        freshness_date=freshness_date,
    )
    freshness = freshness_results_to_dict(freshness_map)
    reference_local_date = resolve_reference_local_date(freshness_date, timezone=policy.timezone)

    plan_md = root / "AI_DAILY_TRADE_PLAN.md"
    latest_plan_json = _latest(root, "ai_daily_trade_plan_*.json")
    latest_order_plan = root / "live_order_plan_ai_latest.json"
    latest_approval_request = _latest(root, "telegram_approval_request_*.json")
    latest_approval = _latest(root, "telegram_approval_audit_*.json")
    latest_execution = _latest(root, "execute_approved_audit_*.json")
    report_md = root / "AI_DAILY_TRADE_REPORT.md"
    latest_report_json = _latest(root, "ai_daily_trade_report_*.json")
    status_md = root / "AI_DAILY_STATUS.md"
    latest_status_json = _latest(root, "ai_daily_status_*.json")
    latest_fill = _latest(root, "live_fill_summary_*.json")

    checks = {
        "plan_markdown_exists": plan_md.is_file(),
        "plan_json_exists": latest_plan_json is not None,
        "latest_order_plan_exists": latest_order_plan.is_file(),
        "approval_request_exists": latest_approval_request is not None,
        "approval_audit_exists": latest_approval is not None,
        "execute_approved_exists": latest_execution is not None,
        "fill_summary_exists": latest_fill is not None,
        "report_markdown_exists": report_md.is_file(),
        "report_json_exists": latest_report_json is not None,
        "status_markdown_exists": status_md.is_file(),
        "status_json_exists": latest_status_json is not None,
    }

    plan_ready = _plan_usable(freshness_map, checks)
    approval_requested = _approval_request_usable(freshness_map, checks)
    approval_done = _approval_usable(freshness_map, checks)
    execution_fresh = freshness_map.get("execution")
    executed = checks["execute_approved_exists"] and execution_fresh is not None and is_fresh(execution_fresh)
    report_fresh = freshness_map.get("report")
    report_done = (
        checks["report_markdown_exists"]
        and checks["report_json_exists"]
        and report_fresh is not None
        and is_fresh(report_fresh)
    )

    warnings: list[str] = []
    for key in ("plan", "latest_order_plan", "approval", "execution", "report", "status"):
        msg = _freshness_warning(freshness_map[key])
        if msg:
            warnings.append(msg)

    if not plan_ready:
        if not checks["plan_markdown_exists"] or not checks["plan_json_exists"] or not checks["latest_order_plan_exists"]:
            warnings.append("AI 일일 매매 계획이 아직 생성되지 않았습니다.")
        else:
            warnings.append("AI 일일 매매 계획이 오래되었습니다. daily-ai-trade-plan을 다시 실행하세요.")
    elif not approval_requested:
        warnings.append("Telegram 승인 요청이 아직 생성되지 않았거나 오래되었습니다.")
    elif not approval_done:
        warnings.append("Telegram 승인 audit이 아직 없거나 오래되었습니다.")
    elif not executed:
        if checks["execute_approved_exists"] and execution_fresh is not None and not is_fresh(execution_fresh):
            warnings.append("execute-last-approved 실행 audit이 오래되었습니다.")
        else:
            warnings.append("execute-last-approved 실행 audit이 아직 없습니다.")
    elif not report_done:
        if checks["report_markdown_exists"] and checks["report_json_exists"] and report_fresh is not None and not is_fresh(report_fresh):
            warnings.append("AI 일일 매매 결과 리포트가 오래되었습니다.")
        else:
            warnings.append("AI 일일 매매 결과 리포트가 아직 생성되지 않았습니다.")

    if not plan_ready:
        next_action = "python main.py daily-ai-trade-plan --broker kis --network --output-dir outputs"
    elif not approval_requested:
        next_action = "python main.py telegram-approval-request --plan outputs/live_order_plan_ai_latest.json --send --output-dir outputs"
    elif not approval_done:
        next_action = "Telegram [승인] 클릭 (telegram-approval-request --send 가 승인·실주문까지 처리)"
    elif not executed:
        next_action = "Telegram [승인] 재시도 또는 python main.py execute-last-approved --output-dir outputs (legacy)"
    elif not report_done:
        next_action = "python main.py daily-ai-trade-report --broker kis --network --output-dir outputs"
    else:
        next_action = "python main.py daily-ai-status --output-dir outputs"

    return DailyAIWorkflowStatus(
        plan_status=_status(latest_plan_json) if checks["plan_json_exists"] else NOT_AVAILABLE,
        approval_request_status=_status(latest_approval_request) if checks["approval_request_exists"] else NOT_AVAILABLE,
        approval_status=_status(latest_approval) if checks["approval_audit_exists"] else NOT_AVAILABLE,
        execution_status=_status(latest_execution) if checks["execute_approved_exists"] else NOT_AVAILABLE,
        report_status=_status(latest_report_json) if checks["report_json_exists"] else NOT_AVAILABLE,
        status_report_status=_status(latest_status_json) if latest_status_json else NOT_AVAILABLE,
        next_action=next_action,
        warnings=warnings,
        files={
            "AI_DAILY_TRADE_PLAN.md": _posix(plan_md),
            "ai_daily_trade_plan_latest_json": _posix(latest_plan_json),
            "live_order_plan_ai_latest.json": _posix(latest_order_plan),
            "telegram_approval_request_latest_json": _posix(latest_approval_request),
            "telegram_approval_audit_latest_json": _posix(latest_approval),
            "execute_approved_audit_latest_json": _posix(latest_execution),
            "AI_DAILY_TRADE_REPORT.md": _posix(report_md),
            "ai_daily_trade_report_latest_json": _posix(latest_report_json),
            "AI_DAILY_STATUS.md": _posix(status_md),
            "ai_daily_status_latest_json": _posix(latest_status_json),
            "live_fill_summary_latest_json": _posix(latest_fill),
        },
        checks=checks,
        freshness={
            **freshness,
            "labels": {
                key: freshness_label_ko(value.get("status", ""))
                for key, value in freshness.items()
                if key != "labels" and key != "sources"
            },
            "sources": {
                key: freshness_source_label_ko(str(value.get("freshness_source") or ""))
                for key, value in freshness.items()
                if isinstance(value, dict) and "freshness_source" in value
            },
        },
        freshness_reference_date=reference_local_date.isoformat(),
    )
