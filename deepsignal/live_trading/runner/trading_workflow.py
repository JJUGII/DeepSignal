"""Daily AI trading workflow reports.

The planning/reporting commands in this module never submit orders. Live order
submission remains isolated to the operator-run approved execution command.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import shutil
from pathlib import Path
from typing import Any, Callable

from deepsignal.live_trading.ai_recommendation.plan_order_diagnostics import (
    build_plan_order_diagnostic_report,
    format_plan_diagnostic_console,
    format_plan_diagnostic_markdown,
)
from deepsignal.live_trading.ai_recommendation.recommendation_engine import run_ai_live_recommendation
from deepsignal.live_trading.ai_recommendation.recommendation_model import RecommendationConfig
from deepsignal.live_trading.kis_stock_recommendation_config import load_stock_recommendation_config_from_env
from deepsignal.live_trading.time_utils import (
    markdown_timestamp_block,
    now_kst,
    now_kst_iso,
    parse_datetime_with_default_tz,
    stamp_daily_ai_payload,
)


@dataclass
class WorkflowStep:
    name: str
    status: str
    message: str
    output_path: str | None = None


@dataclass
class DailyAITradePlanResult:
    generated_at: str
    status: str
    steps: list[WorkflowStep]
    recommendation_status: str
    recommendation_count: int
    order_count: int
    total_order_value: float
    recommendation_json: str
    order_plan_json: str
    latest_order_plan_json: str
    markdown_path: str
    plan_diagnostics: dict[str, Any] | None = None
    diagnostic_console: str = ""
    safety_note: str = "Planning only. No live-approve, no execute-last-approved, no KIS order-cash POST."

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "steps": [asdict(s) for s in self.steps],
            "recommendation_status": self.recommendation_status,
            "recommendation_count": self.recommendation_count,
            "order_count": self.order_count,
            "total_order_value": self.total_order_value,
            "recommendation_json": self.recommendation_json,
            "order_plan_json": self.order_plan_json,
            "latest_order_plan_json": self.latest_order_plan_json,
            "markdown_path": self.markdown_path,
            "plan_diagnostics": self.plan_diagnostics,
            "safety_note": self.safety_note,
        }


@dataclass
class DailyAITradeReportResult:
    generated_at: str
    status: str
    summary: dict[str, Any]
    source_files: dict[str, str]
    markdown_path: str
    json_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "summary": dict(self.summary),
            "source_files": dict(self.source_files),
            "markdown_path": self.markdown_path,
            "json_path": self.json_path,
        }


@dataclass
class DailyAIStatusResult:
    generated_at: str
    status: str
    checks: dict[str, bool]
    latest_files: dict[str, str]
    next_command: str
    markdown_path: str
    json_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "checks": dict(self.checks),
            "latest_files": dict(self.latest_files),
            "next_command": self.next_command,
            "markdown_path": self.markdown_path,
            "json_path": self.json_path,
        }


def _root(output_dir: str | Path) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _latest(root: Path, pattern: str) -> Path | None:
    paths = sorted(root.glob(pattern))
    return paths[-1] if paths else None


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _safe_rel(path: Path | None) -> str:
    return path.as_posix() if path is not None else ""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(stamp_daily_ai_payload(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stamp_json_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict):
        _write_json(path, data)


def _md_timestamp_lines(generated_at: str) -> list[str]:
    dt = parse_datetime_with_default_tz(generated_at)
    return markdown_timestamp_block(dt)


_PLAN_STATUS_KO: dict[str, str] = {
    "AI_DAILY_TRADE_PLAN_READY": "✅ 매매계획 준비됨",
    "AI_DAILY_TRADE_PLAN_NO_ORDERS": "주문 없음 (매매 조건 미충족)",
    "AI_DAILY_TRADE_PLAN_FAILED": "❌ 계획 생성 실패",
}
_REC_STATUS_KO: dict[str, str] = {
    "AI_RECOMMENDATION_NO_PLAN_ORDERS": "추천 있으나 주문 조건 미충족",
    "AI_RECOMMENDATION_READY": "✅ 추천 준비됨",
    "AI_RECOMMENDATION_FAILED": "❌ 추천 생성 실패",
    "AI_RECOMMENDATION_BLOCKED": "차단됨",
}
_STEP_STATUS_KO: dict[str, str] = {
    "RECORDED": "완료",
    "SKIPPED": "건너뜀",
    "OK": "✅ 정상",
    "WARN": "⚠️ 경고",
    "FAIL": "❌ 실패",
    "ERROR": "❌ 오류",
}
_STEP_NAME_KO: dict[str, str] = {
    "trading-session-check": "매매 세션 확인",
    "kis-check": "KIS 설정 확인",
    "live-sync-account": "계좌 동기화",
    "reconcile-live-account": "잔고 대사",
    "safety-audit": "안전 점검",
    "ai-live-recommend": "AI 추천 생성",
}


def _render_plan_md(result: DailyAITradePlanResult, *, include_debug: bool = False) -> str:
    status_ko = _PLAN_STATUS_KO.get(str(result.status), str(result.status))
    rec_ko = _REC_STATUS_KO.get(str(result.recommendation_status), str(result.recommendation_status))
    lines = [
        "# DeepSignal — AI 일일 매매계획 (주식)",
        "",
        *_md_timestamp_lines(result.generated_at),
        "",
        f"- 상태: {status_ko}",
        f"- 생성 시각: {result.generated_at}",
        f"- 추천 상태: {rec_ko}",
        f"- 추천 종목 수: {result.recommendation_count}개",
        f"- 주문 수: {result.order_count}개",
        f"- 예상 총 주문금액: {result.total_order_value:,.0f}원",
        f"- 최신 주문안 파일: `{result.latest_order_plan_json}`",
        "",
        "## 실행 단계",
        "",
    ]
    for step in result.steps:
        name_ko = _STEP_NAME_KO.get(step.name, step.name)
        status_s = _STEP_STATUS_KO.get(str(step.status), str(step.status))
        lines.append(f"- {name_ko}: {status_s} — {step.message}")
    if result.plan_diagnostics and (result.order_count == 0 or include_debug):
        lines.append("")
        lines.append(format_plan_diagnostic_markdown(result.plan_diagnostics).rstrip())
    lines.extend(
        [
            "",
            "## 다음 실행 명령",
            "",
            "```bash",
            f"python main.py telegram-approval-request --plan {result.latest_order_plan_json} --send --output-dir {Path(result.latest_order_plan_json).parent.as_posix()}",
            "# 텔레그램 [승인] 클릭 시 실주문 자동 실행 + 결과 텔레그램 전송",
            "python main.py daily-ai-trade-report --broker kis --network --output-dir outputs",
            "```",
            "",
            "## 안전 안내",
            "",
            "- 이 명령은 live-approve를 직접 호출하지 않습니다.",
            "- 텔레그램 승인 후 실행은 telegram-approval-request --send 가 처리합니다.",
            "- KIS 실주문 및 현금 POST를 직접 전송하지 않습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


_REPORT_SOURCE_KEY_KO: dict[str, str] = {
    "ai_recommendation": "AI 추천 파일",
    "telegram_approval": "텔레그램 승인",
    "execute_approved": "실행 감사",
    "live_approval": "실거래 승인",
    "fill_summary": "체결 요약",
    "account_snapshot": "계좌 스냅샷",
    "reconcile": "잔고 대사",
    "risk": "위험 점검",
    "safety": "안전 점검",
    "archive": "아카이브",
}


def _render_report_md(result: DailyAITradeReportResult) -> str:
    summary = result.summary
    submitted = summary.get("order_submitted", False)
    submitted_ko = "✅ 제출됨" if submitted else "미제출"
    lines = [
        "# DeepSignal — AI 일일 매매 결과 (주식)",
        "",
        *_md_timestamp_lines(result.generated_at),
        "",
        f"- 상태: {result.status}",
        f"- 생성 시각: {result.generated_at}",
        f"- AI 추천 상태: {summary.get('ai_recommendation_status', '-')}",
        f"- 텔레그램 승인 상태: {summary.get('telegram_approval_status', '-')}",
        f"- 실행 상태: {summary.get('execution_status', '-')}",
        f"- 주문 제출: {submitted_ko}",
        f"- 체결 상태: {summary.get('fill_status', '-')}",
        f"- 위험 상태: {summary.get('risk_status', '-')}",
        "",
        "## 데이터 파일",
        "",
    ]
    for key, value in result.source_files.items():
        label = _REPORT_SOURCE_KEY_KO.get(key, key)
        lines.append(f"- {label}: `{value}`")
    lines.extend(
        [
            "",
            "## 내일 확인사항",
            "",
            f"- {summary.get('tomorrow_notes', '최신 리포트와 안전 감사 결과를 확인하세요.')}",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_status_md(result: DailyAIStatusResult) -> str:
    lines = [
        "# DeepSignal — AI 시스템 상태",
        "",
        *_md_timestamp_lines(result.generated_at),
        "",
        f"- 상태: {result.status}",
        f"- 생성 시각: {result.generated_at}",
        f"- 다음 실행 명령: `{result.next_command}`",
        "",
        "## 점검 항목",
        "",
    ]
    for key, value in result.checks.items():
        val_ko = "✅" if value is True else ("❌" if value is False else str(value))
        lines.append(f"- {key}: {val_ko}")
    lines.extend(["", "## 최신 파일", ""])
    for key, value in result.latest_files.items():
        lines.append(f"- {key}: `{value}`")
    return "\n".join(lines) + "\n"


def _step(name: str, status: str, message: str, output_path: str | None = None) -> WorkflowStep:
    return WorkflowStep(name=name, status=status, message=message, output_path=output_path)


def run_daily_ai_trade_plan(
    db_path: str,
    *,
    broker: str = "kis",
    network: bool = False,
    output_dir: str | Path = "outputs",
    max_order_value: float | None = None,
    allow_test_plan_order: bool = False,
    ignore_safety_block_for_test: bool = False,
    debug_plan: bool = False,
    recommendation_runner: Callable[..., tuple[Any, Path, Path, Path]] = run_ai_live_recommendation,
) -> DailyAITradePlanResult:
    root = _root(output_dir)
    steps = [
        _step("trading-session-check", "RECORDED", "세션 상태는 기존 guard에서 최종 재검증됩니다."),
        _step("kis-check", "RECORDED", "KIS 설정은 실행 단계 live guard에서 재검증됩니다."),
        _step("live-sync-account", "RECORDED" if network else "SKIPPED", "--network일 때 AI 추천 account context에서 조회됩니다."),
        _step("reconcile-live-account", "RECORDED" if network else "SKIPPED", "최신 reconcile 리포트가 있으면 AI 추천 risk context에 반영됩니다."),
        _step("safety-audit", "RECORDED", "최신 safety audit 리포트가 있으면 AI 추천 risk context에 반영됩니다."),
    ]
    cfg = load_stock_recommendation_config_from_env(
        RecommendationConfig(
            broker=broker,
            output_dir=str(root),
            capital_limit=float(max_order_value) if max_order_value is not None else None,
            allow_test_plan_order=bool(allow_test_plan_order),
            ignore_safety_block_for_test=bool(ignore_safety_block_for_test),
            debug_plan=bool(debug_plan),
        )
    )
    result, rec_json, plan_json, rec_md = recommendation_runner(db_path, config=cfg, network=network)
    plan_diag = getattr(result, "plan_diagnostics", None)
    if plan_diag is None and hasattr(result, "config"):
        plan_diag = build_plan_order_diagnostic_report(result)
    latest_plan = root / "live_order_plan_ai_latest.json"
    shutil.copyfile(plan_json, latest_plan)
    orders = list((getattr(result, "order_plan", {}) or {}).get("orders") or [])
    total = 0.0
    for order in orders:
        if isinstance(order, dict):
            try:
                total += float(order.get("estimated_order_value") or 0.0)
            except (TypeError, ValueError):
                pass
    generated_at = now_kst_iso()
    status = "AI_DAILY_TRADE_PLAN_READY" if orders else "AI_DAILY_TRADE_PLAN_NO_ORDERS"
    if not latest_plan.exists():
        status = "AI_DAILY_TRADE_PLAN_FAILED"
    json_path = root / f"ai_daily_trade_plan_{_ts()}.json"
    md_path = root / "AI_DAILY_TRADE_PLAN.md"
    plan_result = DailyAITradePlanResult(
        generated_at=generated_at,
        status=status,
        steps=steps + [_step("ai-live-recommend", "OK", "AI recommendation report and plan generated.", rec_json.as_posix())],
        recommendation_status=str(getattr(result, "status", "")),
        recommendation_count=len(list(getattr(result, "recommendations", []) or [])),
        order_count=len(orders),
        total_order_value=total,
        recommendation_json=rec_json.as_posix(),
        order_plan_json=plan_json.as_posix(),
        latest_order_plan_json=latest_plan.as_posix(),
        markdown_path=md_path.as_posix(),
        plan_diagnostics=plan_diag if isinstance(plan_diag, dict) else None,
        diagnostic_console=format_plan_diagnostic_console(plan_diag or {}, debug=debug_plan) if plan_diag else "",
    )
    _write_json(
        json_path,
        plan_result.to_dict() | {"json_path": json_path.as_posix(), "recommendation_markdown": rec_md.as_posix()},
    )
    _stamp_json_file(plan_json)
    _stamp_json_file(latest_plan)
    md_path.write_text(_render_plan_md(plan_result, include_debug=debug_plan), encoding="utf-8")
    return plan_result


def build_daily_ai_trade_report(
    *,
    output_dir: str | Path = "outputs",
    broker: str = "kis",
    network: bool = False,
) -> DailyAITradeReportResult:
    root = _root(output_dir)
    latest = {
        "ai_recommendation": _latest(root, "ai_live_trade_recommendation_*.json"),
        "telegram_approval": _latest(root, "telegram_approval_audit_*.json"),
        "execute_approved": _latest(root, "execute_approved_audit_*.json"),
        "live_approval": _latest(root, "live_approval_audit_*.json"),
        "fill_summary": _latest(root, "live_fill_summary_*.json"),
        "account_snapshot": _latest(root, "live_account_snapshot_*.json"),
        "reconcile": _latest(root, "reconcile_live_account_*.json"),
        "risk": _latest(root, "risk_alert_*.json"),
        "safety": _latest(root, "safety_audit_*.json"),
        "archive": _latest(root, "archive_viewer_*.json"),
    }
    rec = _read_json(latest["ai_recommendation"])
    approval = _read_json(latest["telegram_approval"])
    execution = _read_json(latest["execute_approved"])
    fill = _read_json(latest["fill_summary"])
    risk = _read_json(latest["risk"])
    account = _read_json(latest["account_snapshot"])
    summary = {
        "broker": broker,
        "network_requested": bool(network),
        "ai_recommendation_status": rec.get("status", "NOT_AVAILABLE"),
        "approval_status": approval.get("status", "NOT_AVAILABLE"),
        "telegram_approval_status": approval.get("status", "NOT_AVAILABLE"),
        "execution_status": execution.get("status", "NOT_AVAILABLE"),
        "order_submitted": bool((execution.get("execution_result") or {}).get("actual_order_attempted")),
        "fill_status": fill.get("status", "NOT_AVAILABLE"),
        "cash": account.get("cash") or (account.get("summary") or {}).get("cash"),
        "today_return_pct": "NOT_AVAILABLE",
        "cumulative_return_pct": "NOT_AVAILABLE",
        "risk_status": risk.get("status", "NOT_AVAILABLE"),
        "tomorrow_notes": "승인/실행/체결/리스크 리포트를 확인하고 다음 장 시작 전 daily-ai-status를 확인하세요.",
    }
    generated_at = now_kst_iso()
    status = "AI_DAILY_TRADE_REPORT_READY"
    json_path = root / f"ai_daily_trade_report_{_ts()}.json"
    md_path = root / "AI_DAILY_TRADE_REPORT.md"
    report = DailyAITradeReportResult(
        generated_at=generated_at,
        status=status,
        summary=summary,
        source_files={k: _safe_rel(v) for k, v in latest.items()},
        markdown_path=md_path.as_posix(),
        json_path=json_path.as_posix(),
    )
    _write_json(json_path, report.to_dict())
    md_path.write_text(_render_report_md(report), encoding="utf-8")
    return report


def build_daily_ai_status(
    *,
    output_dir: str | Path = "outputs",
    freshness_date: str | None = None,
) -> DailyAIStatusResult:
    from deepsignal.live_trading.daily_ai_status_reader import read_daily_ai_workflow_status

    root = _root(output_dir)
    workflow = read_daily_ai_workflow_status(root, freshness_date=freshness_date)
    latest = {
        "daily_plan": _latest(root, "ai_daily_trade_plan_*.json"),
        "latest_plan": root / "live_order_plan_ai_latest.json",
        "telegram_approval": _latest(root, "telegram_approval_audit_*.json"),
        "execute_approved": _latest(root, "execute_approved_audit_*.json"),
        "fill_summary": _latest(root, "live_fill_summary_*.json"),
        "daily_report": _latest(root, "ai_daily_trade_report_*.json"),
    }
    checks = {
        "plan_created": workflow.checks.get("plan_json_exists") and workflow.checks.get("latest_order_plan_exists"),
        "plan_fresh": workflow.freshness.get("plan", {}).get("status") == "FRESH",
        "latest_order_plan_fresh": workflow.freshness.get("latest_order_plan", {}).get("status") == "FRESH",
        "telegram_approved": workflow.checks.get("approval_audit_exists", False),
        "approval_fresh": workflow.freshness.get("approval", {}).get("status") == "FRESH",
        "executed": workflow.checks.get("execute_approved_exists", False),
        "execution_fresh": workflow.freshness.get("execution", {}).get("status") == "FRESH",
        "fill_checked": bool(latest["fill_summary"]),
        "report_created": workflow.checks.get("report_json_exists", False),
        "report_fresh": workflow.freshness.get("report", {}).get("status") == "FRESH",
    }
    next_command = workflow.next_action
    generated_at = now_kst_iso()
    json_path = root / f"ai_daily_status_{_ts()}.json"
    md_path = root / "AI_DAILY_STATUS.md"
    result = DailyAIStatusResult(
        generated_at=generated_at,
        status="AI_DAILY_STATUS_READY",
        checks=checks,
        latest_files={k: _safe_rel(v) for k, v in latest.items()},
        next_command=next_command,
        markdown_path=md_path.as_posix(),
        json_path=json_path.as_posix(),
    )
    payload = result.to_dict()
    payload["freshness"] = workflow.freshness
    payload["freshness_reference_date"] = workflow.freshness_reference_date
    payload["warnings"] = workflow.warnings
    _write_json(json_path, payload)
    md_lines = _render_status_md(result).splitlines()
    md_lines.extend(["", "## Freshness", ""])
    labels = workflow.freshness.get("labels") if isinstance(workflow.freshness, dict) else {}
    if isinstance(labels, dict):
        for key in ("plan", "latest_order_plan", "approval", "execution", "report", "status"):
            md_lines.append(f"- {key}: {labels.get(key, '-')}")
    if workflow.warnings:
        md_lines.extend(["", "## Warnings", ""])
        md_lines.extend(f"- {w}" for w in workflow.warnings)
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return result
