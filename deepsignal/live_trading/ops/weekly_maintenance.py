"""Weekly maintenance dry-run orchestration ([실전-26])."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


@dataclass
class WeeklyMaintenanceStep:
    name: str
    success: bool
    status: str
    message: str
    output_paths: dict[str, str]
    warnings: list[str]


@dataclass
class WeeklyMaintenanceResult:
    generated_at: str
    dry_run: bool
    success: bool
    final_status: str
    steps: list[WeeklyMaintenanceStep]
    next_actions: list[str]
    warnings: list[str]


WEEKLY_MAINTENANCE_OK = "WEEKLY_MAINTENANCE_OK"
WEEKLY_MAINTENANCE_WARNING = "WEEKLY_MAINTENANCE_WARNING"
WEEKLY_MAINTENANCE_CRITICAL = "WEEKLY_MAINTENANCE_CRITICAL"


def _step(name: str, fn: Callable[[], WeeklyMaintenanceStep]) -> WeeklyMaintenanceStep:
    try:
        return fn()
    except Exception as e:  # pragma: no cover - exercised through focused tests.
        return WeeklyMaintenanceStep(
            name=name,
            success=False,
            status="CRITICAL",
            message=f"{type(e).__name__}: {e}",
            output_paths={},
            warnings=[str(e)],
        )


def _read_cleanup_candidate_count(audit_path: str | None) -> int:
    if not audit_path:
        return 0
    try:
        data = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    candidates = data.get("candidates") if isinstance(data, dict) else None
    return len(candidates) if isinstance(candidates, list) else 0


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        s = str(value or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _decide_final_status(steps: list[WeeklyMaintenanceStep]) -> str:
    if any((not step.success) or step.status in {"CRITICAL", "HEALTH_CRITICAL"} for step in steps):
        return WEEKLY_MAINTENANCE_CRITICAL
    if any(step.status in {"WARNING", "HEALTH_WARNING", "HEALTH_NO_DATA"} or step.warnings for step in steps):
        return WEEKLY_MAINTENANCE_WARNING
    return WEEKLY_MAINTENANCE_OK


def _next_actions(steps: list[WeeklyMaintenanceStep], final_status: str) -> list[str]:
    actions = [
        "Review outputs/REPORT_HEALTH.md.",
        "Review outputs/REPORT_INDEX.html.",
        "Review outputs/RECOMMENDATION_PERFORMANCE.md for recent recommendation quality.",
        "Review outputs/CRYPTO_RECOMMENDATION_PERFORMANCE.md for Upbit recommendation outcomes.",
        "When tuned: review outputs/OUTCOME_THRESHOLD_TUNING.md and AI_VALIDATION_THRESHOLD_SUMMARY.json.",
    ]
    for step in steps:
        if step.name == "cleanup_reports_dry_run" and step.status == "WARNING":
            actions.append("Run cleanup-reports --apply manually only after reviewing the dry-run audit.")
        if step.status in {"HEALTH_WARNING", "HEALTH_NO_DATA"}:
            actions.append("Refresh operational reports with python main.py ops-dry-run --network --broker kis when network checks are intended.")
        if step.status == "HEALTH_CRITICAL":
            actions.append("Inspect DB health before relying on operational reports.")
    if final_status == WEEKLY_MAINTENANCE_OK:
        actions.append("No critical action. Continue weekly monitoring.")
    return _dedupe(actions)


def run_weekly_maintenance(
    *,
    output_dir: str | Path = "outputs",
    archive_dir: str | Path | None = "outputs/archive",
    db_path: str | Path = "data/deepsignal.db",
    keep_days: int = 14,
    keep_latest: int = 20,
    max_age_hours: float = 24.0,
    max_output_files: int = 500,
    tune_threshold_from_outcomes: bool = False,
    outcomes_db: str | Path | None = None,
    tune_lookback_days: int = 60,
    tune_min_samples: int = 10,
    tune_blend_with_validation: float = 0.5,
) -> WeeklyMaintenanceResult:
    """Run weekly maintenance checks. Dry-run only: no delete/archive/network/alerts/orders."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    steps: list[WeeklyMaintenanceStep] = []

    def report_health_step() -> WeeklyMaintenanceStep:
        from deepsignal.live_trading.report_health import run_report_health_check, write_report_health

        result = run_report_health_check(
            output_dir=out_dir,
            db_path=db_path,
            max_age_hours=max_age_hours,
            max_output_files=max_output_files,
        )
        jp, mp = write_report_health(result, output_dir=out_dir)
        warnings = [f"{i.severity} {i.category}: {i.message}" for i in result.issues if i.severity.upper() != "INFO"]
        return WeeklyMaintenanceStep(
            name="report_health_check",
            success=result.status != "HEALTH_CRITICAL",
            status=result.status,
            message=f"report health status={result.status}",
            output_paths={"json": jp.as_posix(), "markdown": mp.as_posix()},
            warnings=warnings,
        )

    steps.append(_step("report_health_check", report_health_step))

    def cleanup_step() -> WeeklyMaintenanceStep:
        from deepsignal.live_trading.report_cleanup import cleanup_reports

        result = cleanup_reports(
            output_dir=out_dir,
            keep_days=keep_days,
            keep_latest=keep_latest,
            archive=False,
            archive_dir=None,
            remove_appledouble=False,
            dry_run=True,
        )
        candidate_count = _read_cleanup_candidate_count(result.audit_path)
        warnings = list(result.warnings)
        if candidate_count > 0:
            warnings.append(f"cleanup dry-run found {candidate_count} candidate files")
        return WeeklyMaintenanceStep(
            name="cleanup_reports_dry_run",
            success=True,
            status="WARNING" if candidate_count > 0 or warnings else "OK",
            message=f"dry-run candidates={candidate_count}",
            output_paths={"audit": str(result.audit_path or "")},
            warnings=warnings,
        )

    steps.append(_step("cleanup_reports_dry_run", cleanup_step))

    def daily_step() -> WeeklyMaintenanceStep:
        from deepsignal.live_trading.daily_ops_summary import run_daily_ops_summary

        result, jp, mp = run_daily_ops_summary(output_dir=out_dir, notify_dry_run=False)
        warnings = list(result.warnings)
        status = "WARNING" if result.status in {"WARNING", "RISK_ALERT", "RECONCILE_MISMATCH", "NO_DATA"} or warnings else "OK"
        return WeeklyMaintenanceStep(
            name="daily_ops_summary",
            success=True,
            status=status,
            message=f"daily ops status={result.status}",
            output_paths={"json": jp.as_posix(), "markdown": mp.as_posix()},
            warnings=warnings,
        )

    steps.append(_step("daily_ops_summary", daily_step))

    def html_step() -> WeeklyMaintenanceStep:
        from deepsignal.live_trading.html_dashboard import write_html_dashboard

        result = write_html_dashboard(output_dir=out_dir)
        warnings = list(result.warnings)
        status = "WARNING" if warnings else "OK"
        return WeeklyMaintenanceStep(
            name="html_dashboard",
            success=True,
            status=status,
            message=f"html dashboard status={result.status}",
            output_paths={"html": result.html_path},
            warnings=warnings,
        )

    steps.append(_step("html_dashboard", html_step))

    def index_step() -> WeeklyMaintenanceStep:
        from deepsignal.live_trading.report_index import run_report_index

        result, hp, mp, jp = run_report_index(output_dir=out_dir, archive_dir=archive_dir)
        warnings = list(result.warnings)
        return WeeklyMaintenanceStep(
            name="report_index",
            success=True,
            status="WARNING" if warnings else "OK",
            message=f"indexed reports={len(result.items)}",
            output_paths={"html": hp.as_posix(), "markdown": mp.as_posix(), "json": jp.as_posix()},
            warnings=warnings,
        )

    steps.append(_step("report_index", index_step))

    def recommendation_performance_step() -> WeeklyMaintenanceStep:
        from deepsignal.live_trading.ai_recommendation.recommendation_outcomes import (
            generate_recommendation_performance_report,
            outcomes_db_path,
            refresh_recommendation_outcomes,
        )

        odb = outcomes_db_path(out_dir)
        warnings: list[str] = []
        if not odb.is_file():
            warnings.append("recommendation_outcomes.db not found yet; run daily-ai-trade-plan first")
        refresh_stats = refresh_recommendation_outcomes(str(db_path), odb)
        jp, mp, summary = generate_recommendation_performance_report(odb, output_dir=out_dir, days=7)
        status = "OK"
        if summary.total_rows == 0:
            status = "WARNING"
            warnings.append("no recommendation outcome rows in the lookback window")
        return WeeklyMaintenanceStep(
            name="recommendation_performance",
            success=True,
            status=status,
            message=(
                f"rows={summary.total_rows} allowed={summary.allowed_count} "
                f"executed={summary.executed_count} closed={summary.closed_count} "
                f"refresh={refresh_stats}"
            ),
            output_paths={"json": jp.as_posix(), "markdown": mp.as_posix(), "outcomes_db": odb.as_posix()},
            warnings=warnings,
        )

    steps.append(_step("recommendation_performance", recommendation_performance_step))

    def crypto_recommendation_performance_step() -> WeeklyMaintenanceStep:
        from deepsignal.crypto_trading.crypto_recommendation_outcomes import (
            crypto_outcomes_db_path,
            generate_crypto_performance_report,
            init_crypto_outcomes_db,
        )

        odb = crypto_outcomes_db_path(out_dir)
        init_crypto_outcomes_db(odb)
        warnings: list[str] = []
        if not odb.is_file():
            warnings.append("crypto_recommendation_outcomes.db not found yet; run crypto-daily-plan or crypto-auto-runner")
        jp, mp, summary = generate_crypto_performance_report(odb, output_dir=out_dir, days=7)
        status = "OK"
        if summary.total_rows == 0:
            status = "WARNING"
            warnings.append("no crypto recommendation outcome rows in the lookback window")
        return WeeklyMaintenanceStep(
            name="crypto_recommendation_performance",
            success=True,
            status=status,
            message=(
                f"rows={summary.total_rows} buy={summary.buy_count} sell={summary.sell_count} "
                f"executed={summary.executed_count} closed={summary.closed_count}"
            ),
            output_paths={"json": jp.as_posix(), "markdown": mp.as_posix(), "outcomes_db": odb.as_posix()},
            warnings=warnings,
        )

    steps.append(_step("crypto_recommendation_performance", crypto_recommendation_performance_step))

    if tune_threshold_from_outcomes:

        def outcome_threshold_step() -> WeeklyMaintenanceStep:
            from deepsignal.live_trading.ai_recommendation.outcome_threshold_tuning import run_tune_threshold_from_outcomes
            from deepsignal.live_trading.ai_recommendation.recommendation_outcomes import outcomes_db_path

            odb = Path(outcomes_db) if outcomes_db else outcomes_db_path(out_dir)
            warnings: list[str] = []
            if not odb.is_file():
                return WeeklyMaintenanceStep(
                    name="tune_threshold_from_outcomes",
                    success=False,
                    status="WARNING",
                    message="recommendation_outcomes.db missing",
                    output_paths={},
                    warnings=["run daily-ai-trade-plan to populate outcomes first"],
                )
            result, jp, mp, sp = run_tune_threshold_from_outcomes(
                outcomes_db=odb,
                output_dir=out_dir,
                lookback_days=tune_lookback_days,
                min_samples=tune_min_samples,
                blend_with_validation=tune_blend_with_validation,
            )
            warnings.extend(result.warnings)
            g = result.global_block
            return WeeklyMaintenanceStep(
                name="tune_threshold_from_outcomes",
                success=True,
                status="WARNING" if warnings else "OK",
                message=(
                    f"outcome_thr={g.get('recommended_min_final_score')} "
                    f"merged_thr={g.get('merged_min_final_score')} samples={g.get('eligible_samples')}"
                ),
                output_paths={
                    "json": jp.as_posix(),
                    "markdown": mp.as_posix(),
                    "threshold_summary": sp.as_posix(),
                },
                warnings=warnings,
            )

        steps.append(_step("tune_threshold_from_outcomes", outcome_threshold_step))

    final_status = _decide_final_status(steps)
    warnings = _dedupe([warning for step in steps for warning in step.warnings])
    return WeeklyMaintenanceResult(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        dry_run=True,
        success=final_status != WEEKLY_MAINTENANCE_CRITICAL,
        final_status=final_status,
        steps=steps,
        next_actions=_next_actions(steps, final_status),
        warnings=warnings,
    )


def write_weekly_maintenance_report(
    result: WeeklyMaintenanceResult,
    *,
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    jp = root / f"weekly_maintenance_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    mp = root / "WEEKLY_MAINTENANCE.md"
    body: dict[str, Any] = asdict(result)
    body["network_called"] = False
    body["dry_run_only"] = True
    body["cleanup_apply_used"] = False
    body["archive_move_used"] = False
    body["notifications_sent"] = False
    body["actual_order_attempted"] = False
    jp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _MAINT_STATUS_KO = {
        "WEEKLY_MAINTENANCE_OK": "✅ 정상",
        "WEEKLY_MAINTENANCE_WARNING": "⚠️ 경고",
        "WEEKLY_MAINTENANCE_CRITICAL": "🚨 위험",
    }
    _STEP_STATUS_KO = {"OK": "✅ 정상", "WARNING": "⚠️ 경고", "CRITICAL": "🚨 위험",
                       "NO_DATA": "데이터 없음", "HEALTH_CRITICAL": "위험 수준",
                       "HEALTH_WARNING": "경고 수준", "HEALTH_OK": "정상"}
    _STEP_NAME_KO = {
        "report_health_check": "리포트 상태 점검",
        "cleanup_reports_dry_run": "보관 파일 정리 (시뮬레이션)",
        "daily_ops_summary": "일일 운영 요약",
        "html_dashboard": "HTML 대시보드 생성",
        "report_index": "전체 리포트 목록 생성",
        "recommendation_performance": "추천 성과 분석",
        "crypto_recommendation_performance": "코인 추천 성과 분석",
    }

    final_ko = _MAINT_STATUS_KO.get(str(result.final_status), str(result.final_status))
    dry_run_ko = "시뮬레이션" if result.dry_run else "실제 실행"

    lines = [
        "# DeepSignal — 주간 시스템 점검",
        "",
        "## 요약",
        "",
        f"- 최종 상태: **{final_ko}**",
        f"- 실행 모드: {dry_run_ko}",
        f"- 생성 시각: {result.generated_at}",
        "- 모드: 시뮬레이션 전용 (삭제·아카이브 이동·네트워크 호출·알림·주문 없음)",
        "",
        "## 단계별 결과",
        "",
        "| 단계 | 성공 | 상태 | 내용 |",
        "|------|------|------|------|",
    ]
    for step in result.steps:
        name_ko = _STEP_NAME_KO.get(step.name, step.name)
        status_ko = _STEP_STATUS_KO.get(str(step.status), str(step.status))
        success_ko = "✅" if step.success else "❌"
        msg = str(step.message).replace("|", "\\|")
        lines.append(f"| {name_ko} | {success_ko} | {status_ko} | {msg} |")
    lines.extend(["", "## 출력 파일", ""])
    for step in result.steps:
        if not step.output_paths:
            continue
        name_ko = _STEP_NAME_KO.get(step.name, step.name)
        lines.append(f"- {name_ko}")
        for key, value in step.output_paths.items():
            lines.append(f"  - {key}: `{value}`")
    lines.extend(["", "## 다음 할 일", ""])
    for action in result.next_actions:
        lines.append(f"- {action}")
    lines.extend(["", "## 경고", ""])
    if result.warnings:
        for warning in result.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- (없음)")
    lines.extend(
        [
            "",
            "## 안전 안내",
            "",
            "- cleanup-reports --apply는 시뮬레이션 감사 결과 확인 후 수동으로 실행하세요.",
            "- 이 명령은 파일 삭제, 아카이브 이동, 네트워크 호출, 알림 발송, 주문 실행을 하지 않습니다.",
            "- SELL automation, market orders, repeated orders, cancels, and KIS POST are not part of this command.",
        ]
    )
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp


def format_weekly_maintenance_console(
    result: WeeklyMaintenanceResult,
    json_path: Path | None = None,
    markdown_path: Path | None = None,
) -> str:
    lines = [
        "DeepSignal weekly maintenance dry-run",
        f"Final Status: {result.final_status}",
        f"Dry Run: {str(result.dry_run).lower()}",
        "Steps:",
    ]
    for step in result.steps:
        lines.append(f"- {step.name}: {step.status} success={step.success} - {step.message}")
    if result.warnings:
        lines.append("Warnings:")
        for warning in result.warnings:
            lines.append(f"- {warning}")
    if json_path:
        lines.append(f"JSON: {json_path.as_posix()}")
    if markdown_path:
        lines.append(f"Markdown: {markdown_path.as_posix()}")
    lines.append("Note: weekly-maintenance is dry-run only; no cleanup apply, archive move, network calls, alerts, or orders.")
    return "\n".join(lines)
