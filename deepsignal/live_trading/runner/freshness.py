"""Daily AI workflow file freshness validation ([실전-48], [실전-49]).

Reads local file metadata and JSON timestamps only. No network, KIS, Telegram,
or order execution calls.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import json

from deepsignal.live_trading.time_utils import (
    DEFAULT_TZ,
    local_date_string,
    parse_datetime_with_default_tz,
    parse_markdown_generated_at,
)

FRESHNESS_FRESH = "FRESH"
FRESHNESS_STALE = "STALE"
FRESHNESS_MISSING = "MISSING"
FRESHNESS_UNKNOWN = "UNKNOWN"

SOURCE_GENERATED_AT = "generated_at"
SOURCE_MTIME_FALLBACK = "mtime_fallback"
SOURCE_MISSING = "missing"

SEVERITY_OK = "ok"
SEVERITY_WARNING = "warning"
SEVERITY_BLOCKED = "blocked"

_STATUS_PRIORITY = {
    FRESHNESS_MISSING: 0,
    FRESHNESS_UNKNOWN: 1,
    FRESHNESS_FRESH: 2,
    FRESHNESS_STALE: 3,
}

_SEVERITY_PRIORITY = {
    SEVERITY_OK: 0,
    SEVERITY_WARNING: 1,
    SEVERITY_BLOCKED: 2,
}


@dataclass
class DailyAIFreshnessPolicy:
    timezone: str = DEFAULT_TZ
    max_plan_age_hours: int = 12
    max_report_age_hours: int = 36
    max_status_age_hours: int = 12
    require_same_local_date: bool = True
    stale_is_blocking_for_execution: bool = True

    def max_age_for_target(self, target_name: str) -> int:
        mapping = {
            "plan": self.max_plan_age_hours,
            "latest_order_plan": self.max_plan_age_hours,
            "approval": self.max_plan_age_hours,
            "execution": self.max_report_age_hours,
            "report": self.max_report_age_hours,
            "status": self.max_status_age_hours,
        }
        return mapping.get(target_name, self.max_plan_age_hours)


@dataclass
class FreshnessResult:
    target_name: str
    path: str | None
    generated_at: str | None
    modified_at: str | None
    age_hours: float | None
    same_local_date: bool | None
    status: str
    severity: str
    warning: str | None = None
    freshness_source: str = SOURCE_MISSING
    generated_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_reference_local_date(
    freshness_date: str | date | None = None,
    *,
    timezone: str = DEFAULT_TZ,
) -> date:
    if freshness_date is None:
        return datetime.now(ZoneInfo(timezone)).date()
    if isinstance(freshness_date, date):
        return freshness_date
    text = str(freshness_date).strip()
    return date.fromisoformat(text)


def freshness_label_ko(status: str) -> str:
    labels = {
        FRESHNESS_FRESH: "최신",
        FRESHNESS_STALE: "오래됨",
        FRESHNESS_MISSING: "없음",
        FRESHNESS_UNKNOWN: "알 수 없음",
    }
    return labels.get(status, status)


def freshness_source_label_ko(source: str) -> str:
    labels = {
        SOURCE_GENERATED_AT: "generated_at",
        SOURCE_MTIME_FALLBACK: "mtime fallback",
        SOURCE_MISSING: "없음",
    }
    return labels.get(source, source)


def _read_json_timestamp(
    path: Path,
    *,
    timezone: str = DEFAULT_TZ,
) -> tuple[datetime | None, str | None, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None, SOURCE_MISSING
    if not isinstance(data, dict):
        return None, None, SOURCE_MISSING

    generated_date = str(data.get("generated_date") or "").strip() or None
    for key in ("generated_at", "created_at", "timestamp", "executed_at"):
        dt = parse_datetime_with_default_tz(data.get(key), default_tz=timezone)
        if dt is not None:
            if not generated_date:
                generated_date = local_date_string(dt, tz=timezone)
            return dt.astimezone(UTC), generated_date, SOURCE_GENERATED_AT

    summary = data.get("summary")
    if isinstance(summary, dict):
        for key in ("generated_at", "finished_at", "snapshot_time"):
            dt = parse_datetime_with_default_tz(summary.get(key), default_tz=timezone)
            if dt is not None:
                if not generated_date:
                    generated_date = local_date_string(dt, tz=timezone)
                return dt.astimezone(UTC), generated_date, SOURCE_GENERATED_AT

    return None, generated_date, SOURCE_MISSING


def _read_file_timestamp(
    path: Path,
    *,
    timezone: str = DEFAULT_TZ,
) -> tuple[datetime | None, str | None, str]:
    if path.suffix.lower() == ".json":
        return _read_json_timestamp(path, timezone=timezone)

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, SOURCE_MISSING

    md_dt = parse_markdown_generated_at(text, default_tz=timezone)
    if md_dt is not None:
        return md_dt.astimezone(UTC), local_date_string(md_dt, tz=timezone), SOURCE_GENERATED_AT
    return None, None, SOURCE_MISSING


def _local_date(dt: datetime, tz_name: str) -> date:
    return dt.astimezone(ZoneInfo(tz_name)).date()


def _same_local_date(
    *,
    effective_dt: datetime,
    generated_date: str | None,
    reference_local_date: date,
    timezone: str,
) -> bool:
    if generated_date:
        try:
            if date.fromisoformat(generated_date) == reference_local_date:
                return True
        except ValueError:
            pass
    return _local_date(effective_dt, timezone) == reference_local_date


def _severity_for_target(target_name: str, status: str, policy: DailyAIFreshnessPolicy) -> str:
    if status == FRESHNESS_FRESH:
        return SEVERITY_OK
    if status in {FRESHNESS_MISSING, FRESHNESS_UNKNOWN}:
        return SEVERITY_WARNING
    if status == FRESHNESS_STALE:
        if policy.stale_is_blocking_for_execution and target_name in {"plan", "latest_order_plan"}:
            return SEVERITY_BLOCKED
        return SEVERITY_WARNING
    return SEVERITY_WARNING


def check_file_freshness(
    path: Path | None,
    *,
    target_name: str,
    policy: DailyAIFreshnessPolicy | None = None,
    reference_local_date: date,
    max_age_hours: int | None = None,
    now_utc: datetime | None = None,
) -> FreshnessResult:
    """Evaluate a single file against freshness policy."""
    policy = policy or DailyAIFreshnessPolicy()
    max_age = max_age_hours if max_age_hours is not None else policy.max_age_for_target(target_name)
    now = now_utc or datetime.now(UTC)

    if path is None or not path.is_file():
        return FreshnessResult(
            target_name=target_name,
            path=path.as_posix() if path is not None else None,
            generated_at=None,
            modified_at=None,
            age_hours=None,
            same_local_date=None,
            status=FRESHNESS_MISSING,
            severity=_severity_for_target(target_name, FRESHNESS_MISSING, policy),
            warning=f"{target_name} 파일이 없습니다.",
            freshness_source=SOURCE_MISSING,
        )

    generated_dt, generated_date, source = _read_file_timestamp(path, timezone=policy.timezone)
    modified_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    modified_at = modified_dt.isoformat()

    if generated_dt is None:
        generated_dt = modified_dt
        source = SOURCE_MTIME_FALLBACK

    generated_at = generated_dt.isoformat() if generated_dt else None
    effective_dt = generated_dt
    if effective_dt is None:
        return FreshnessResult(
            target_name=target_name,
            path=path.as_posix(),
            generated_at=generated_at,
            modified_at=modified_at,
            age_hours=None,
            same_local_date=None,
            status=FRESHNESS_UNKNOWN,
            severity=_severity_for_target(target_name, FRESHNESS_UNKNOWN, policy),
            warning=f"{path.name}의 생성 시각을 확인할 수 없습니다.",
            freshness_source=source,
            generated_date=generated_date,
        )
    local_match = _same_local_date(
        effective_dt=effective_dt,
        generated_date=generated_date,
        reference_local_date=reference_local_date,
        timezone=policy.timezone,
    )
    age_hours = max(0.0, (now - effective_dt).total_seconds() / 3600.0)
    stale_by_date = policy.require_same_local_date and not local_match
    stale_by_age = age_hours > float(max_age)
    is_stale = stale_by_date or stale_by_age

    warning: str | None = None
    if source == SOURCE_MTIME_FALLBACK:
        warning = f"{path.name}: mtime fallback 사용"

    if is_stale:
        reasons: list[str] = []
        if stale_by_date:
            reasons.append("오늘 날짜가 아닙니다")
        if stale_by_age:
            reasons.append(f"허용 시간({max_age}시간)을 초과했습니다")
        stale_warning = f"{path.name}: " + ", ".join(reasons)
        warning = f"{warning}; {stale_warning}" if warning else stale_warning
        status = FRESHNESS_STALE
    else:
        status = FRESHNESS_FRESH

    return FreshnessResult(
        target_name=target_name,
        path=path.as_posix(),
        generated_at=generated_at,
        modified_at=modified_at,
        age_hours=round(age_hours, 3),
        same_local_date=local_match,
        status=status,
        severity=_severity_for_target(target_name, status, policy),
        warning=warning,
        freshness_source=source,
        generated_date=generated_date,
    )


def _combine_results(target_name: str, results: list[FreshnessResult]) -> FreshnessResult:
    if not results:
        return FreshnessResult(
            target_name=target_name,
            path=None,
            generated_at=None,
            modified_at=None,
            age_hours=None,
            same_local_date=None,
            status=FRESHNESS_MISSING,
            severity=SEVERITY_WARNING,
            warning=f"{target_name} 파일이 없습니다.",
            freshness_source=SOURCE_MISSING,
        )
    worst = max(
        results,
        key=lambda item: (
            _STATUS_PRIORITY.get(item.status, 0),
            _SEVERITY_PRIORITY.get(item.severity, 0),
        ),
    )
    warnings = [item.warning for item in results if item.warning]
    return FreshnessResult(
        target_name=target_name,
        path=worst.path,
        generated_at=worst.generated_at,
        modified_at=worst.modified_at,
        age_hours=worst.age_hours,
        same_local_date=worst.same_local_date,
        status=worst.status,
        severity=worst.severity,
        warning="; ".join(warnings) if warnings else worst.warning,
        freshness_source=worst.freshness_source,
        generated_date=worst.generated_date,
    )


def _latest(root: Path, pattern: str) -> Path | None:
    matches = [p for p in root.glob(pattern) if p.is_file()]
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def build_daily_ai_freshness(
    output_dir: str | Path = "outputs",
    *,
    policy: DailyAIFreshnessPolicy | None = None,
    freshness_date: str | date | None = None,
    now_utc: datetime | None = None,
) -> dict[str, FreshnessResult]:
    """Build freshness results for daily AI workflow artifact groups."""
    policy = policy or DailyAIFreshnessPolicy()
    root = Path(output_dir)
    ref_date = resolve_reference_local_date(freshness_date, timezone=policy.timezone)
    now = now_utc or datetime.now(UTC)

    def _check_group(target: str, paths: list[Path | None]) -> FreshnessResult:
        max_age = policy.max_age_for_target(target)
        checked = [
            check_file_freshness(
                path,
                target_name=target,
                policy=policy,
                reference_local_date=ref_date,
                max_age_hours=max_age,
                now_utc=now,
            )
            for path in paths
        ]
        return _combine_results(target, checked)

    plan_md = root / "AI_DAILY_TRADE_PLAN.md"
    plan_json = _latest(root, "ai_daily_trade_plan_*.json")
    latest_order = root / "live_order_plan_ai_latest.json"
    approval_request = _latest(root, "telegram_approval_request_*.json")
    approval_audit = _latest(root, "telegram_approval_audit_*.json")
    execution = _latest(root, "execute_approved_audit_*.json")
    report_md = root / "AI_DAILY_TRADE_REPORT.md"
    report_json = _latest(root, "ai_daily_trade_report_*.json")
    status_md = root / "AI_DAILY_STATUS.md"
    status_json = _latest(root, "ai_daily_status_*.json")

    approval_paths: list[Path | None]
    if approval_audit is not None:
        approval_paths = [approval_audit]
    else:
        approval_paths = [approval_request]

    return {
        "plan": _check_group("plan", [plan_md, plan_json]),
        "latest_order_plan": check_file_freshness(
            latest_order if latest_order.is_file() else None,
            target_name="latest_order_plan",
            policy=policy,
            reference_local_date=ref_date,
            now_utc=now,
        ),
        "approval": _check_group("approval", approval_paths),
        "execution": _check_group("execution", [execution]),
        "report": _check_group("report", [report_md, report_json]),
        "status": _check_group("status", [status_md, status_json]),
    }


def freshness_results_to_dict(results: dict[str, FreshnessResult]) -> dict[str, dict[str, Any]]:
    return {key: value.to_dict() for key, value in results.items()}


def is_fresh(result: FreshnessResult) -> bool:
    return result.status == FRESHNESS_FRESH


def validate_execution_freshness(
    *,
    output_dir: str | Path,
    plan_path: str | Path,
    policy: DailyAIFreshnessPolicy | None = None,
    freshness_date: str | date | None = None,
    now_utc: datetime | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Return blocking errors when approved plan or latest AI plan is stale."""
    policy = policy or DailyAIFreshnessPolicy()
    if not policy.stale_is_blocking_for_execution:
        return [], {}

    root = Path(output_dir)
    ref_date = resolve_reference_local_date(freshness_date, timezone=policy.timezone)
    now = now_utc or datetime.now(UTC)
    errors: list[str] = []
    checks: dict[str, Any] = {}

    plan_result = check_file_freshness(
        Path(plan_path) if str(plan_path).strip() else None,
        target_name="plan",
        policy=policy,
        reference_local_date=ref_date,
        now_utc=now,
    )
    latest_result = check_file_freshness(
        root / "live_order_plan_ai_latest.json",
        target_name="latest_order_plan",
        policy=policy,
        reference_local_date=ref_date,
        now_utc=now,
    )
    checks["plan_freshness"] = plan_result.to_dict()
    checks["latest_order_plan_freshness"] = latest_result.to_dict()

    if plan_result.status == FRESHNESS_MISSING:
        errors.append("승인된 주문 plan 파일이 없습니다. daily-ai-trade-plan을 다시 실행하세요.")
    elif not is_fresh(plan_result):
        errors.append(
            "승인된 주문 plan이 오늘 기준으로 오래되었습니다. "
            "전일 plan으로 실주문하지 마세요. daily-ai-trade-plan을 다시 실행하세요."
        )

    if latest_result.status == FRESHNESS_MISSING:
        errors.append("live_order_plan_ai_latest.json이 없습니다. daily-ai-trade-plan을 다시 실행하세요.")
    elif not is_fresh(latest_result):
        errors.append(
            "live_order_plan_ai_latest.json이 오늘 기준으로 오래되었습니다. "
            "최신 AI 일일 plan을 생성한 뒤 Telegram 승인부터 다시 진행하세요."
        )

    return errors, checks
