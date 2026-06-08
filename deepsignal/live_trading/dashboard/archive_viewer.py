"""Local archive viewer for operations reports ([실전-32]).

Read-only scanner/index generator for files under outputs/ and outputs/archive/.
No network, order, cleanup, archive move, delete, DB read, or source analysis.
"""

from __future__ import annotations

import html
import json
import re
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from deepsignal.live_trading.daily_ai_freshness import DailyAIFreshnessPolicy, check_file_freshness, resolve_reference_local_date
from deepsignal.live_trading.operator_labels import (
    label_freshness_source,
    label_freshness_status,
    label_report_type,
    label_severity,
    label_status,
    label_summary_key,
)
from deepsignal.live_trading.time_utils import DEFAULT_TZ, parse_datetime_with_default_tz, parse_markdown_generated_at


@dataclass
class ArchiveEntry:
    path: str
    relative_path: str
    report_type: str
    format: str
    created_at: str | None
    modified_at: str
    status: str | None
    severity: str
    size_bytes: int
    title: str
    generated_at: str | None = None
    generated_date: str | None = None
    timezone: str | None = None
    freshness_source: str = "unknown"
    freshness_status: str = "UNKNOWN"


@dataclass
class ArchiveViewerResult:
    generated_at: str
    output_dir: str
    archive_dir: str | None
    filters_available: dict[str, Any]
    entries: list[ArchiveEntry]
    needs_attention: list[dict[str, Any]]
    latest_by_type: dict[str, ArchiveEntry]
    summary: dict[str, Any]
    trend_analytics: dict[str, Any]
    warnings: list[str]


@dataclass
class ArchiveViewerLinkInfo:
    status: str
    html_path: str | None
    csv_path: str | None
    summary_md_path: str | None
    presets_path: str | None
    json_path: str | None
    html_rel: str | None
    csv_rel: str | None
    summary_md_rel: str | None
    presets_rel: str | None
    json_rel: str | None
    total_reports: int | None
    updated_at: str | None
    message: str
    freshness_source_summary: dict[str, int] | None = None


TIMESTAMP_RE = re.compile(r"_(\d{8})_(\d{6})")

JSON_PATTERNS: tuple[tuple[str, str], ...] = (
    ("safety_audit_*.json", "safety_audit"),
    ("weekly_maintenance_*.json", "weekly_maintenance"),
    ("report_health_*.json", "report_health"),
    ("reconcile_live_account_*.json", "reconcile"),
    ("live_account_snapshot_*.json", "live_account_snapshot"),
    ("live_approval_audit_*.json", "live_approval_audit"),
    ("live_fill_summary_*.json", "live_fill_summary"),
    ("risk_alert_*.json", "risk_alert"),
    ("report_cleanup_audit_*.json", "cleanup_audit"),
    ("ai_daily_trade_plan_*.json", "ai_daily_trade_plan"),
    ("ai_daily_trade_report_*.json", "ai_daily_trade_report"),
    ("ai_daily_status_*.json", "ai_daily_status"),
)

STATIC_FILES: tuple[tuple[str, str], ...] = (
    ("REPORT_INDEX.html", "report_index"),
    ("OPS_DASHBOARD.html", "dashboard"),
    ("SAFETY_AUDIT.md", "safety_audit"),
    ("WEEKLY_MAINTENANCE.md", "weekly_maintenance"),
    ("REPORT_HEALTH.md", "report_health"),
    ("RISK_ALERT.md", "risk_alert"),
    ("LIVE_FILL_SUMMARY.md", "live_fill_summary"),
    ("RECONCILE_LIVE_ACCOUNT.md", "reconcile"),
    ("LIVE_ACCOUNT_SNAPSHOT.md", "live_account_snapshot"),
    ("AI_DAILY_TRADE_PLAN.md", "ai_daily_trade_plan"),
    ("AI_DAILY_TRADE_REPORT.md", "ai_daily_trade_report"),
    ("AI_DAILY_STATUS.md", "ai_daily_status"),
    ("live_order_plan_ai_latest.json", "ai_live_order_plan_latest"),
)

EXCLUDED_NAMES = {".env", ".kis_token_cache.json"}
EXCLUDED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".py", ".pyc"}
JSON_TIMESTAMP_KEYS = ("generated_at", "created_at", "updated_at", "timestamp")
JSON_METADATA_KEYS = ("generated_at", "created_at", "updated_at", "timestamp", "generated_date", "timezone")

SAFE_JSON_KEYS = (
    "status",
    "final_status",
    "success",
    "created_at",
    "generated_at",
    "timestamp",
    "snapshot_time",
    "finished_at",
    "warnings",
    "issues",
    "alerts",
    "blocked_count",
    "warning_count",
    "partial_fill_open",
    "stale_snapshot",
    "reconcile_mismatch",
    "mismatch_count",
    "open_partial_fills",
    "is_stale",
)

STATUS_ATTENTION_TOKENS = (
    "WARNING",
    "BLOCKED",
    "ERROR",
    "FAILED",
    "FAIL",
    "CRITICAL",
    "MISMATCH",
    "RISK_ALERT",
    "STOP_LOSS",
)

DEFAULT_ARCHIVE_VIEWER_PRESETS: list[dict[str, Any]] = [
    {
        "id": "needs_attention",
        "label": "주의 필요 항목",
        "description": "경고/차단/오류 리포트만 표시합니다.",
        "filters": {"only_attention": True},
    },
    {
        "id": "latest_only",
        "label": "최신 리포트만",
        "description": "각 유형별 최신 리포트만 표시합니다.",
        "filters": {"latest_only": True},
    },
    {
        "id": "safety_audit",
        "label": "안전 점검만",
        "description": "안전 점검 리포트만 표시합니다.",
        "filters": {"report_type": "safety_audit"},
    },
    {
        "id": "risk_and_reconcile",
        "label": "리스크/정합성",
        "description": "리스크 경고와 계좌 정합성 리포트를 확인합니다.",
        "filters": {"report_types": ["risk_alert", "reconcile"]},
    },
    {
        "id": "live_order_audit",
        "label": "실거래 감사",
        "description": "실거래 승인/차단/실패 관련 감사 리포트만 표시합니다.",
        "filters": {"report_type": "live_approval_audit"},
    },
]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_safe_candidate(path: Path, output_root: Path) -> bool:
    if not path.is_file():
        return False
    if not _is_inside(path, output_root):
        return False
    if path.name in EXCLUDED_NAMES or path.name.startswith("._"):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def _format_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".html":
        return "html"
    if suffix == ".md":
        return "markdown"
    return suffix.lstrip(".") or "unknown"


def _created_from_name(name: str) -> str | None:
    match = TIMESTAMP_RE.search(name)
    if not match:
        return None
    date, time = match.groups()
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}T{time[:2]}:{time[2:4]}:{time[4:6]}"


def _extract_status(data: dict[str, Any]) -> str | None:
    for key in ("status", "final_status"):
        if data.get(key) is not None:
            return str(data.get(key))
    if data.get("success") is not None:
        return f"success={data.get('success')}"
    return None


def _count_list(data: dict[str, Any], key: str, severity: str | None = None) -> int:
    value = data.get(key)
    if not isinstance(value, list):
        return 0
    if severity is None:
        return len(value)
    return sum(1 for item in value if isinstance(item, dict) and str(item.get("severity") or "").upper() == severity.upper())


def _safe_json_summary(path: Path, warnings: list[str]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Failed to parse {path.name}: {exc}")
        return {"parse_error": True}
    if not isinstance(data, dict):
        warnings.append(f"Non-object JSON skipped: {path.name}")
        return {"non_object_json": True}
    summary: dict[str, Any] = {key: data.get(key) for key in SAFE_JSON_KEYS if key in data and key not in {"warnings", "issues", "alerts"}}
    summary["status"] = _extract_status(data)
    summary["warning_count"] = int(data.get("warning_count") or _count_list(data, "warnings") + _count_list(data, "issues", "WARNING"))
    summary["blocked_count"] = int(data.get("blocked_count") or _count_list(data, "issues", "BLOCKED"))
    summary["alert_count"] = _count_list(data, "alerts")
    return summary


def _read_entry_summary(entry: ArchiveEntry, output_root: Path, warnings: list[str]) -> dict[str, Any]:
    if entry.format != "json":
        return {}
    path = output_root / entry.relative_path
    if not _is_safe_candidate(path, output_root):
        return {}
    return _safe_json_summary(path, warnings)


def _severity(status: str | None, summary: dict[str, Any]) -> str:
    status_u = str(status or "").upper()
    if summary.get("parse_error"):
        return "warning"
    if int(summary.get("blocked_count") or 0) > 0:
        return "blocked"
    if any(token in status_u for token in ("BLOCKED", "CRITICAL", "MISMATCH", "RISK_ALERT", "STOP_LOSS", "ERROR")):
        return "blocked"
    if int(summary.get("warning_count") or 0) > 0 or int(summary.get("alert_count") or 0) > 0:
        return "warning"
    if any(token in status_u for token in ("WARNING", "NO_DATA", "REVIEW", "REDUCE")):
        return "warning"
    return "ok" if status else "unknown"


def _attention_reasons(entry: ArchiveEntry, summary: dict[str, Any]) -> list[str]:
    status_u = str(entry.status or "").upper()
    reasons: list[str] = []
    if entry.severity in {"warning", "blocked"}:
        reasons.append(f"severity={entry.severity}")
    if any(token in status_u for token in STATUS_ATTENTION_TOKENS):
        reasons.append(f"status={entry.status}")
    if bool(summary.get("reconcile_mismatch")) or int(summary.get("mismatch_count") or 0) > 0 or "MISMATCH" in status_u:
        reasons.append("reconcile mismatch")
    if bool(summary.get("partial_fill_open")) or int(summary.get("open_partial_fills") or 0) > 0:
        reasons.append("partial fill open")
    if bool(summary.get("stale_snapshot")) or bool(summary.get("is_stale")) or "STALE" in status_u:
        reasons.append("stale snapshot")
    if entry.report_type == "safety_audit" and "BLOCKED" in status_u:
        reasons.append("safety audit blocked")
    return list(dict.fromkeys(reasons))


def _build_latest_by_type(entries: list[ArchiveEntry]) -> dict[str, ArchiveEntry]:
    latest: dict[str, ArchiveEntry] = {}
    for entry in entries:
        latest.setdefault(entry.report_type, entry)
    return latest


def _filters_available(entries: list[ArchiveEntry]) -> dict[str, Any]:
    return {
        "report_type": sorted({e.report_type for e in entries}),
        "status": sorted({e.status for e in entries if e.status}),
        "severity": sorted({e.severity for e in entries}),
        "text_search": True,
        "date_range": True,
        "only_warnings_errors": True,
        "latest_only": True,
        "sortable_columns": ["modified_at", "generated_at", "report_type", "status", "severity", "size_bytes", "freshness_source"],
    }


def _entry_day(entry: ArchiveEntry) -> str:
    if entry.generated_date:
        return entry.generated_date
    from_name = _created_from_name(Path(entry.relative_path).name)
    if from_name:
        return from_name[:10]
    raw = entry.modified_at or entry.created_at
    return str(raw)[:10] if raw else "unknown"


def _blank_day() -> dict[str, int]:
    return {"total": 0, "warning": 0, "blocked": 0}


def _build_trend_analytics(entries: list[ArchiveEntry], *, trend_days: int = 7) -> dict[str, Any]:
    by_day: dict[str, dict[str, int]] = {}
    by_report_type: dict[str, dict[str, int]] = {}
    by_severity: dict[str, int] = {}
    by_status: dict[str, int] = {}
    needs_attention_by_type: dict[str, int] = {}

    for entry in entries:
        day = _entry_day(entry)
        day_bucket = by_day.setdefault(day, _blank_day())
        day_bucket["total"] += 1
        if entry.severity == "warning":
            day_bucket["warning"] += 1
        if entry.severity in {"blocked", "error"}:
            day_bucket["blocked"] += 1

        type_bucket = by_report_type.setdefault(entry.report_type, _blank_day())
        type_bucket["total"] += 1
        if entry.severity == "warning":
            type_bucket["warning"] += 1
        if entry.severity in {"blocked", "error"}:
            type_bucket["blocked"] += 1

        by_severity[entry.severity] = int(by_severity.get(entry.severity, 0)) + 1
        status_key = entry.status or "-"
        by_status[status_key] = int(by_status.get(status_key, 0)) + 1
        if entry.severity in {"warning", "blocked", "error"}:
            needs_attention_by_type[entry.report_type] = int(needs_attention_by_type.get(entry.report_type, 0)) + 1

    window = max(int(trend_days or 7), 1)
    today = date.today()
    day_keys = [(today - timedelta(days=offset)).isoformat() for offset in range(window - 1, -1, -1)]
    warning_trend = [{"date": key, "count": int(by_day.get(key, {}).get("warning", 0))} for key in day_keys]
    blocked_trend = [{"date": key, "count": int(by_day.get(key, {}).get("blocked", 0))} for key in day_keys]

    window_start = today - timedelta(days=window - 1)
    problem_counts: dict[str, int] = {}
    for entry in entries:
        try:
            entry_date = date.fromisoformat(_entry_day(entry))
        except ValueError:
            continue
        if entry_date < window_start or entry_date > today:
            continue
        if entry.severity in {"warning", "blocked", "error"}:
            problem_counts[entry.report_type] = int(problem_counts.get(entry.report_type, 0)) + 1
    repeated = [
        {"report_type": report_type, "count": count}
        for report_type, count in sorted(problem_counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= 2
    ]

    return {
        "trend_days": window,
        "total_reports": len(entries),
        "total_warning": sum(1 for entry in entries if entry.severity == "warning"),
        "total_blocked_or_error": sum(1 for entry in entries if entry.severity in {"blocked", "error"}),
        "by_day": dict(sorted(by_day.items())),
        "by_report_type": dict(sorted(by_report_type.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "by_status": dict(sorted(by_status.items())),
        "warning_trend_7d": warning_trend,
        "blocked_trend_7d": blocked_trend,
        "needs_attention_by_type": dict(sorted(needs_attention_by_type.items())),
        "repeated_problem_types": repeated,
    }


def _title(report_type: str, path: Path) -> str:
    return f"{report_type.replace('_', ' ').title()} - {path.name}"


def _read_markdown_head(path: Path, *, max_lines: int = 40) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readline() for _ in range(max_lines))
    except OSError:
        return ""


def _read_json_metadata(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: data.get(key) for key in JSON_METADATA_KEYS if data.get(key) is not None}


def _extract_freshness_metadata(path: Path) -> tuple[str | None, str | None, str, str, str]:
    """Return generated_at, generated_date, timezone, freshness_source, freshness_status."""
    st = path.stat()
    modified_iso = datetime.fromtimestamp(st.st_mtime, tz=ZoneInfo("UTC")).isoformat()
    generated_at: str | None = None
    generated_date: str | None = None
    timezone = DEFAULT_TZ
    source = "unknown"

    if path.suffix.lower() == ".json":
        meta = _read_json_metadata(path)
        if meta.get("timezone"):
            timezone = str(meta.get("timezone"))
        if meta.get("generated_date"):
            generated_date = str(meta.get("generated_date"))
        for key in JSON_TIMESTAMP_KEYS:
            dt = parse_datetime_with_default_tz(meta.get(key), default_tz=timezone)
            if dt is not None:
                local = dt.astimezone(ZoneInfo(timezone))
                generated_at = local.isoformat(timespec="seconds")
                if not generated_date:
                    generated_date = local.date().isoformat()
                source = "generated_at"
                break
        if source == "unknown":
            source = "mtime_fallback"
            generated_at = modified_iso
    elif path.suffix.lower() == ".md":
        dt = parse_markdown_generated_at(_read_markdown_head(path), default_tz=timezone)
        if dt is not None:
            local = dt.astimezone(ZoneInfo(timezone))
            generated_at = local.isoformat(timespec="seconds")
            generated_date = local.date().isoformat()
            source = "markdown_header"
        else:
            source = "mtime_fallback"
            generated_at = modified_iso
    else:
        source = "mtime_fallback"
        generated_at = modified_iso

    freshness_status = check_file_freshness(
        path,
        target_name="archive_entry",
        policy=DailyAIFreshnessPolicy(),
        reference_local_date=resolve_reference_local_date(),
    ).status

    return generated_at, generated_date, timezone, source, freshness_status


def _build_freshness_source_summary(entries: list[ArchiveEntry]) -> dict[str, int]:
    summary = {
        "generated_at": 0,
        "markdown_header": 0,
        "mtime_fallback": 0,
        "unknown": 0,
    }
    for entry in entries:
        key = entry.freshness_source if entry.freshness_source in summary else "unknown"
        summary[key] = int(summary.get(key, 0)) + 1
    return summary


def _entry(path: Path, output_root: Path, report_type: str, warnings: list[str]) -> ArchiveEntry:
    st = path.stat()
    summary = _safe_json_summary(path, warnings) if path.suffix.lower() == ".json" else {}
    status = str(summary.get("status")) if summary.get("status") is not None else None
    generated_at, generated_date, timezone, freshness_source, freshness_status = _extract_freshness_metadata(path)
    created_at = generated_at or _created_from_name(path.name)
    return ArchiveEntry(
        path=path.as_posix(),
        relative_path=_rel(path, output_root),
        report_type=report_type,
        format=_format_for(path),
        created_at=created_at,
        modified_at=datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        status=status,
        severity=_severity(status, summary),
        size_bytes=int(st.st_size),
        title=_title(report_type, path),
        generated_at=generated_at,
        generated_date=generated_date,
        timezone=timezone,
        freshness_source=freshness_source,
        freshness_status=freshness_status,
    )


def _collect_candidates(output_root: Path, archive_root: Path | None) -> list[tuple[Path, str]]:
    roots = [output_root]
    if archive_root is not None and archive_root.exists() and _is_inside(archive_root, output_root):
        roots.append(archive_root)
    candidates: dict[Path, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for pattern, report_type in JSON_PATTERNS:
            for path in root.rglob(pattern):
                if _is_safe_candidate(path, output_root):
                    candidates[path.resolve()] = report_type
        for filename, report_type in STATIC_FILES:
            for path in root.rglob(filename):
                if _is_safe_candidate(path, output_root):
                    candidates[path.resolve()] = report_type
        for path in root.rglob("weekly_bundle_*/BUNDLE_INDEX.html"):
            if _is_safe_candidate(path, output_root):
                candidates[path.resolve()] = "bundle"
    return [(path, report_type) for path, report_type in candidates.items()]


def build_archive_viewer(
    *,
    output_dir: str | Path = "outputs",
    archive_dir: str | Path | None = "outputs/archive",
    limit: int = 200,
    trend_days: int = 7,
) -> ArchiveViewerResult:
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    archive_root = Path(archive_dir).expanduser().resolve() if archive_dir else None
    warnings: list[str] = []
    if archive_root is not None and archive_root.exists() and not _is_inside(archive_root, output_root):
        warnings.append("archive_dir is outside output_dir and was not scanned")
        archive_root = None

    entries = [_entry(path, output_root, report_type, warnings) for path, report_type in _collect_candidates(output_root, archive_root)]
    entries.sort(key=lambda e: (e.modified_at, e.relative_path), reverse=True)
    if limit > 0:
        entries = entries[: int(limit)]

    latest_by_type = _build_latest_by_type(entries)
    summary_warnings: list[str] = []
    needs_attention: list[dict[str, Any]] = []
    for entry in entries:
        entry_summary = _read_entry_summary(entry, output_root, summary_warnings)
        reasons = _attention_reasons(entry, entry_summary)
        if reasons:
            needs_attention.append(
                {
                    "relative_path": entry.relative_path,
                    "report_type": entry.report_type,
                    "status": entry.status,
                    "severity": entry.severity,
                    "modified_at": entry.modified_at,
                    "title": entry.title,
                    "reasons": reasons,
                }
            )
    warnings.extend(summary_warnings)

    latest_safety = next((e for e in entries if e.report_type == "safety_audit" and e.format == "json"), None)
    latest_weekly = next((e for e in entries if e.report_type == "weekly_maintenance" and e.format == "json"), None)
    latest_risk = next((e for e in entries if e.report_type == "risk_alert" and e.format == "json"), None)
    latest_reconcile = next((e for e in entries if e.report_type == "reconcile" and e.format == "json"), None)
    latest_approval = next((e for e in entries if e.report_type == "live_approval_audit" and e.format == "json"), None)
    total_warning = sum(1 for e in entries if e.severity == "warning")
    total_blocked_or_error = sum(1 for e in entries if e.severity == "blocked")
    summary = {
        "total_reports": len(entries),
        "warning_count": total_warning,
        "blocked_error_count": total_blocked_or_error,
        "total_warning": total_warning,
        "total_blocked_or_error": total_blocked_or_error,
        "latest_safety_audit_status": latest_safety.status if latest_safety else "NOT_AVAILABLE",
        "latest_weekly_maintenance_status": latest_weekly.status if latest_weekly else "NOT_AVAILABLE",
        "latest_risk_alert_status": latest_risk.status if latest_risk else "NOT_AVAILABLE",
        "latest_reconcile_status": latest_reconcile.status if latest_reconcile else "NOT_AVAILABLE",
        "latest_live_approval_status": latest_approval.status if latest_approval else "NOT_AVAILABLE",
        "needs_attention_count": len(needs_attention),
        "by_type": {},
    }
    for entry in entries:
        by_type = summary["by_type"]
        by_type[entry.report_type] = int(by_type.get(entry.report_type, 0)) + 1
    trend_analytics = _build_trend_analytics(entries, trend_days=trend_days)
    freshness_source_summary = _build_freshness_source_summary(entries)
    summary["freshness_source_summary"] = freshness_source_summary

    return ArchiveViewerResult(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        output_dir=output_root.as_posix(),
        archive_dir=archive_root.as_posix() if archive_root else None,
        filters_available=_filters_available(entries),
        entries=entries,
        needs_attention=needs_attention,
        latest_by_type=latest_by_type,
        summary=summary,
        trend_analytics=trend_analytics,
        warnings=warnings,
    )


def _e(value: Any) -> str:
    return html.escape("-" if value is None or value == "" else str(value))


def _size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _badge(kind: str, label: str) -> str:
    safe_kind = re.sub(r"[^a-z0-9_-]", "unknown", str(kind or "unknown").lower())
    return f'<span class="badge badge-{_e(safe_kind)}">{_e(label)}</span>'


def _bar_rows(points: list[dict[str, Any]], *, cls: str) -> str:
    max_count = max((int(point.get("count") or 0) for point in points), default=0) or 1
    rows = []
    for point in points:
        count = int(point.get("count") or 0)
        width = max(int(count / max_count * 100), 4) if count else 0
        rows.append(
            "<tr>"
            f"<td>{_e(point.get('date'))}</td>"
            f'<td><div class="bar-wrap"><span class="bar {cls}" style="width:{width}%"></span></div></td>'
            f"<td>{_e(count)}</td>"
            "</tr>"
        )
    return "".join(rows)


def _trend_section(result: ArchiveViewerResult) -> str:
    trend = result.trend_analytics
    warning_rows = _bar_rows(trend.get("warning_trend_7d", []), cls="bar-warning")
    blocked_rows = _bar_rows(trend.get("blocked_trend_7d", []), cls="bar-blocked")
    type_rows = []
    for report_type, count in sorted(trend.get("needs_attention_by_type", {}).items(), key=lambda item: (-int(item[1]), item[0])):
        type_rows.append(f"<tr><td>{_e(label_report_type(report_type))}</td><td>{_e(report_type)}</td><td>{_e(count)}</td></tr>")
    repeated = trend.get("repeated_problem_types", [])
    repeated_items = "".join(
        f"<li>{_e(label_report_type(item.get('report_type')))} ({_e(item.get('report_type'))}) - {_e(item.get('count'))}회</li>"
        for item in repeated
    ) or "<li>없음</li>"
    day_rows = []
    for day, bucket in sorted(trend.get("by_day", {}).items(), reverse=True):
        day_rows.append(
            f"<tr><td>{_e(day)}</td><td>{_e(bucket.get('total'))}</td>"
            f"<td>{_e(bucket.get('warning'))}</td><td>{_e(bucket.get('blocked'))}</td></tr>"
        )
    return (
        "<section><h2>운영 추세</h2>"
        "<div class=\"trend-grid\">"
        "<div><h3>최근 7일 경고 추세</h3><table><thead><tr><th>일자</th><th>막대</th><th>경고</th></tr></thead><tbody>"
        + warning_rows
        + "</tbody></table></div>"
        "<div><h3>최근 7일 차단/오류 추세</h3><table><thead><tr><th>일자</th><th>막대</th><th>차단/오류</th></tr></thead><tbody>"
        + blocked_rows
        + "</tbody></table></div>"
        "</div>"
        "<h3>반복 문제 유형</h3><ul>"
        + repeated_items
        + "</ul>"
        "<h3>유형별 주의 항목</h3><table><thead><tr><th>유형</th><th>Raw Type</th><th>주의 항목 수</th></tr></thead><tbody>"
        + "".join(type_rows)
        + "</tbody></table>"
        "<h3>일자별 요약</h3><table><thead><tr><th>일자</th><th>전체</th><th>경고</th><th>차단/오류</th></tr></thead><tbody>"
        + "".join(day_rows)
        + "</tbody></table></section>"
    )


def render_archive_viewer_html(result: ArchiveViewerResult) -> str:
    rows = []
    latest_paths = {entry.relative_path for entry in result.latest_by_type.values()}
    for entry in result.entries:
        latest = "true" if entry.relative_path in latest_paths else "false"
        rows.append(
            f'<tr data-type="{_e(entry.report_type)}" data-status="{_e(entry.status)}" data-severity="{_e(entry.severity)}" '
            f'data-modified="{_e(entry.modified_at)}" data-generated="{_e(entry.generated_at or "")}" '
            f'data-source="{_e(entry.freshness_source)}" data-size="{entry.size_bytes}" data-latest="{latest}">'
            f"<td>{_e(label_report_type(entry.report_type))}</td>"
            f"<td>{_badge(entry.severity if entry.status else 'unknown', label_status(entry.status))}</td>"
            f"<td>{_badge(entry.severity, label_severity(entry.severity))}</td>"
            f"<td>{_e(entry.generated_at or '-')}</td>"
            f"<td>{_e(label_freshness_source(entry.freshness_source))}</td>"
            f"<td>{_e(entry.modified_at)}</td>"
            f"<td>{_e(_size(entry.size_bytes))}</td>"
            f'<td><a href="{_e(entry.relative_path)}">{_e(entry.relative_path)}</a></td>'
            "</tr>"
        )
    attention = "".join(
        "<li>"
        f'<a href="{_e(item["relative_path"])}">{_e(item["relative_path"])}</a> '
        f'{_badge(str(item["severity"]), label_severity(str(item["severity"])))} '
        f'{_e(label_status(item.get("status")))} - {_e(", ".join(item.get("reasons", [])))}'
        "</li>"
        for item in result.needs_attention
    ) or "<li>(없음)</li>"
    warnings = "".join(f"<li>{_e(w)}</li>" for w in result.warnings) or "<li>(없음)</li>"
    filters = result.filters_available
    type_options = "".join(f'<option value="{_e(v)}">{_e(label_report_type(v))}</option>' for v in filters.get("report_type", []))
    status_options = "".join(f'<option value="{_e(v)}">{_e(label_status(v))}</option>' for v in filters.get("status", []))
    severity_options = "".join(f'<option value="{_e(v)}">{_e(label_severity(v))}</option>' for v in filters.get("severity", []))
    preset_options = "".join(
        f'<option value="{_e(p.get("id"))}">{_e(p.get("label"))}</option>' for p in DEFAULT_ARCHIVE_VIEWER_PRESETS
    )
    presets_json = json.dumps(DEFAULT_ARCHIVE_VIEWER_PRESETS, ensure_ascii=False)
    css = """
    body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#f6f7f9;color:#20242a}
    header{padding:24px;background:#111827;color:white}
    main{padding:24px;max-width:1300px;margin:0 auto}
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:16px 0}
    .card{background:white;border-radius:12px;padding:16px;border-left:6px solid #6b7280;box-shadow:0 1px 4px rgba(0,0,0,.08)}
    .label{color:#6b7280;font-size:12px;text-transform:uppercase}.value{font-size:20px;font-weight:700}
    section{background:white;border-radius:12px;padding:18px;margin:16px 0;box-shadow:0 1px 4px rgba(0,0,0,.08)}
    table{width:100%;border-collapse:collapse}th,td{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}
    th{background:#f3f4f6}th button{border:0;background:transparent;font-weight:700;cursor:pointer;padding:0}
    a{color:#2563eb;text-decoration:none}input,select{padding:8px;width:100%;box-sizing:border-box}
    .filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:12px 0}
    .preset-actions{display:flex;gap:8px;align-items:end}.preset-actions button{padding:8px 10px;border:1px solid #9ca3af;border-radius:8px;background:white;cursor:pointer}
    .check{display:flex;gap:8px;align-items:center}.check input{width:auto}
    .badge{display:inline-block;border-radius:999px;padding:2px 8px;font-size:12px;font-weight:700}
    .badge-ok{background:#dcfce7;color:#166534}.badge-warning{background:#fef3c7;color:#92400e}
    .badge-blocked,.badge-error{background:#fee2e2;color:#991b1b}.badge-unknown{background:#e5e7eb;color:#374151}
    .trend-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
    .bar-wrap{height:12px;background:#e5e7eb;border-radius:999px;overflow:hidden}.bar{display:block;height:12px}.bar-warning{background:#f59e0b}.bar-blocked{background:#dc2626}
    @media print{
      body{background:white;color:black;font-size:12px}
      header{background:white;color:black;border-bottom:2px solid #111;padding:12px 0}
      main{padding:0;max-width:none}
      section{box-shadow:none;border:1px solid #999;break-inside:avoid;page-break-inside:avoid;margin:12px 0}
      .filters,script,button{display:none!important}
      .cards{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
      .card{box-shadow:none;border:1px solid #777;border-left:4px solid #111}
      table{font-size:11px;page-break-inside:auto}
      tr{page-break-inside:avoid;page-break-after:auto}
      th{background:white;color:black;border-bottom:2px solid #111}
      .badge{background:white!important;color:black!important;border:1px solid #111}
      a{color:black;text-decoration:underline}
      a[href]::after{content:" (" attr(href) ")";font-size:10px;color:#333}
    }
    """
    script = """
    const ARCHIVE_VIEWER_PRESETS = __PRESETS_JSON__;
    let sortState={key:'modified',dir:'desc'};
    let activePresetTypes=[];
    function val(id){const el=document.getElementById(id);return el?el.value.toLowerCase():'';}
    function checked(id){const el=document.getElementById(id);return !!(el&&el.checked);}
    function rowMatches(r){
      const q=val('filterText'), type=val('filterType'), status=val('filterStatus'), severity=val('filterSeverity');
      const from=val('filterFrom'), to=val('filterTo'), day=(r.dataset.modified||'').slice(0,10);
      if(activePresetTypes.length&&!activePresetTypes.includes(r.dataset.type))return false;
      if(!activePresetTypes.length&&type&&r.dataset.type!==type)return false;
      if(status&&(r.dataset.status||'').toLowerCase()!==status)return false;
      if(severity&&r.dataset.severity!==severity)return false;
      if(from&&day<from)return false;
      if(to&&day>to)return false;
      if(checked('filterAttention')&&!['warning','blocked'].includes(r.dataset.severity))return false;
      if(checked('filterLatest')&&r.dataset.latest!=='true')return false;
      if(q&&!r.innerText.toLowerCase().includes(q))return false;
      return true;
    }
    function applyFilters(){document.querySelectorAll('#reports tbody tr').forEach(r=>{r.style.display=rowMatches(r)?'':'none';});}
    function setValue(id,value){const el=document.getElementById(id);if(el){el.value=value||'';}}
    function setChecked(id,value){const el=document.getElementById(id);if(el){el.checked=!!value;}}
    function resetFilters(){activePresetTypes=[];setValue('presetSelect','');setValue('filterType','');setValue('filterStatus','');setValue('filterSeverity','');setValue('filterText','');setValue('filterFrom','');setValue('filterTo','');setChecked('filterAttention',false);setChecked('filterLatest',false);applyFilters();}
    function applyPreset(){const id=document.getElementById('presetSelect').value;const preset=ARCHIVE_VIEWER_PRESETS.find(p=>p.id===id);if(!preset){resetFilters();return;}const f=preset.filters||{};activePresetTypes=Array.isArray(f.report_types)?f.report_types:[];setValue('filterType',f.report_type||'');setValue('filterStatus',f.status||'');setValue('filterSeverity',f.severity||'');setValue('filterText',f.text||'');setValue('filterFrom',f.from_date||'');setValue('filterTo',f.to_date||'');setChecked('filterAttention',!!f.only_attention);setChecked('filterLatest',!!f.latest_only);applyFilters();}
    function sortTable(key){
      sortState.dir=(sortState.key===key&&sortState.dir==='desc')?'asc':'desc';sortState.key=key;
      const tbody=document.querySelector('#reports tbody');const rows=Array.from(tbody.querySelectorAll('tr'));
      const sev={blocked:3,warning:2,ok:1,unknown:0};
      rows.sort((a,b)=>{let av=a.dataset[key]||'',bv=b.dataset[key]||'';if(key==='size'){av=Number(av);bv=Number(bv);}if(key==='severity'){av=sev[av]||0;bv=sev[bv]||0;}if(av<bv)return sortState.dir==='asc'?-1:1;if(av>bv)return sortState.dir==='asc'?1:-1;return 0;});
      rows.forEach(r=>tbody.appendChild(r));applyFilters();
    }
    document.addEventListener('DOMContentLoaded',()=>{sortTable('modified');});
    """.replace("__PRESETS_JSON__", presets_json)
    s = result.summary
    fs = s.get("freshness_source_summary") if isinstance(s.get("freshness_source_summary"), dict) else {}
    freshness_cards = "".join(
        f'<div class="card"><div class="label">{_e(label_freshness_source(key))}</div>'
        f'<div class="value">{_e(fs.get(key, 0))}</div></div>'
        for key in ("generated_at", "markdown_header", "mtime_fallback", "unknown")
    )
    mtime_note = ""
    if int(fs.get("mtime_fallback", 0) or 0) > int(fs.get("generated_at", 0) or 0):
        mtime_note = (
            "<p><strong>안내:</strong> mtime fallback 비중이 높습니다. "
            "구버전 산출물이거나 복사된 파일일 수 있으니 Daily AI workflow는 JSON <code>generated_at</code>을 확인하세요.</p>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>DeepSignal 리포트 보관함</title><style>{css}</style></head><body>"
        "<header><h1>DeepSignal 리포트 보관함</h1><p>읽기 전용 로컬 리포트 보관함입니다. 네트워크 호출, 주문, 정리 실행, 보관함 이동, 파일 삭제를 수행하지 않습니다.</p></header><main>"
        "<section><h2>운영 요약</h2><div class=\"cards\">"
        f"<div class=\"card\"><div class=\"label\">{_e(label_summary_key('total_reports'))}</div><div class=\"value\">{_e(s.get('total_reports'))}</div></div>"
        f"<div class=\"card\"><div class=\"label\">{_e(label_summary_key('warning_count'))}</div><div class=\"value\">{_e(s.get('warning_count'))}</div></div>"
        f"<div class=\"card\"><div class=\"label\">{_e(label_summary_key('blocked_error_count'))}</div><div class=\"value\">{_e(s.get('blocked_error_count'))}</div></div>"
        f"<div class=\"card\"><div class=\"label\">{_e(label_summary_key('latest_safety_audit_status'))}</div><div class=\"value\">{_e(label_status(str(s.get('latest_safety_audit_status'))))}</div></div>"
        f"<div class=\"card\"><div class=\"label\">{_e(label_summary_key('latest_weekly_maintenance_status'))}</div><div class=\"value\">{_e(label_status(str(s.get('latest_weekly_maintenance_status'))))}</div></div>"
        f"<div class=\"card\"><div class=\"label\">{_e(label_summary_key('latest_risk_alert_status'))}</div><div class=\"value\">{_e(label_status(str(s.get('latest_risk_alert_status'))))}</div></div>"
        f"<div class=\"card\"><div class=\"label\">{_e(label_summary_key('latest_reconcile_status'))}</div><div class=\"value\">{_e(label_status(str(s.get('latest_reconcile_status'))))}</div></div>"
        f"<div class=\"card\"><div class=\"label\">{_e(label_summary_key('latest_live_approval_status'))}</div><div class=\"value\">{_e(label_status(str(s.get('latest_live_approval_status'))))}</div></div>"
        "</div></section>"
        "<section><h2>Freshness 기준 요약</h2>"
        + mtime_note
        + "<div class=\"cards\">"
        + freshness_cards
        + "</div></section>"
        "<section><h2>주의 필요 항목</h2><p>경고, 차단/오류, 정합성 실패, 미체결/부분체결, 오래된 스냅샷, 안전 점검 차단 리포트를 빠르게 확인합니다.</p><ul>"
        + attention
        + "</ul></section>"
        + _trend_section(result)
        + "<section><h2>리포트 목록</h2><div class=\"filters\">"
        f"<label>필터 프리셋<select id=\"presetSelect\"><option value=\"\">전체</option>{preset_options}</select></label>"
        "<div class=\"preset-actions\"><button type=\"button\" onclick=\"applyPreset()\">프리셋 적용</button><button type=\"button\" onclick=\"resetFilters()\">필터 초기화</button></div>"
        f"<label>리포트 유형<select id=\"filterType\" onchange=\"applyFilters()\"><option value=\"\">전체</option>{type_options}</select></label>"
        f"<label>상태<select id=\"filterStatus\" onchange=\"applyFilters()\"><option value=\"\">전체</option>{status_options}</select></label>"
        f"<label>심각도<select id=\"filterSeverity\" onchange=\"applyFilters()\"><option value=\"\">전체</option>{severity_options}</select></label>"
        "<label>텍스트 검색<input id=\"filterText\" oninput=\"applyFilters()\" placeholder=\"유형, 상태, 심각도, 경로 검색\"></label>"
        "<label>시작일<input id=\"filterFrom\" type=\"date\" onchange=\"applyFilters()\"></label>"
        "<label>종료일<input id=\"filterTo\" type=\"date\" onchange=\"applyFilters()\"></label>"
        "<label class=\"check\"><input id=\"filterAttention\" type=\"checkbox\" onchange=\"applyFilters()\"> 경고/오류만 보기</label>"
        "<label class=\"check\"><input id=\"filterLatest\" type=\"checkbox\" onchange=\"applyFilters()\"> 최신 항목만 보기</label>"
        "</div>"
        "<table id=\"reports\"><thead><tr>"
        "<th><button onclick=\"sortTable('type')\">리포트 유형</button></th>"
        "<th><button onclick=\"sortTable('status')\">상태</button></th>"
        "<th><button onclick=\"sortTable('severity')\">심각도</button></th>"
        "<th><button onclick=\"sortTable('generated')\">생성 시각</button></th>"
        "<th>기준 소스</th>"
        "<th><button onclick=\"sortTable('modified')\">수정 시간</button></th>"
        "<th><button onclick=\"sortTable('size')\">크기</button></th>"
        "<th>링크</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
        + "<section><h2>생성 경고</h2><ul>"
        + warnings
        + "</ul></section>"
        + f"<script>{script}</script>"
        + "</main></body></html>"
    )


def write_archive_viewer(
    result: ArchiveViewerResult,
    *,
    output_dir: str | Path = "outputs",
    create_csv: bool = True,
    create_summary_md: bool = True,
) -> tuple[Path, Path]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    html_path = root / "ARCHIVE_VIEWER.html"
    csv_path = root / "ARCHIVE_VIEWER.csv"
    summary_md_path = root / "ARCHIVE_VIEWER_SUMMARY.md"
    presets_path = root / "ARCHIVE_VIEWER_PRESETS.json"
    json_path = root / f"archive_viewer_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    write_archive_viewer_presets(presets_path)
    if create_csv:
        write_archive_viewer_csv(result, csv_path)
    if create_summary_md:
        summary_md_path.write_text(render_archive_viewer_summary_md(result), encoding="utf-8")
    body = asdict(result)
    body["export_files"] = {
        "html": html_path.name,
        "csv": csv_path.name if create_csv else None,
        "summary_md": summary_md_path.name if create_summary_md else None,
        "presets": presets_path.name,
    }
    body["presets"] = DEFAULT_ARCHIVE_VIEWER_PRESETS
    body["preset_file"] = presets_path.name
    body["network_called"] = False
    body["actual_order_attempted"] = False
    body["cleanup_apply_called"] = False
    body["archive_moved"] = False
    body["files_deleted"] = False
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_archive_viewer_html(result), encoding="utf-8")
    return html_path, json_path


def write_archive_viewer_presets(path: Path) -> Path:
    path.write_text(json.dumps(DEFAULT_ARCHIVE_VIEWER_PRESETS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_archive_viewer_csv(result: ArchiveViewerResult, path: Path) -> Path:
    fields = [
        "report_type",
        "report_type_label",
        "status",
        "status_label",
        "severity",
        "severity_label",
        "generated_at",
        "generated_date",
        "timezone",
        "freshness_source",
        "freshness_status",
        "modified_time",
        "size_bytes",
        "relative_path",
        "title",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for entry in result.entries:
            writer.writerow(
                {
                    "report_type": entry.report_type,
                    "report_type_label": label_report_type(entry.report_type),
                    "status": entry.status or "",
                    "status_label": label_status(entry.status),
                    "severity": entry.severity,
                    "severity_label": label_severity(entry.severity),
                    "generated_at": entry.generated_at or "",
                    "generated_date": entry.generated_date or "",
                    "timezone": entry.timezone or "",
                    "freshness_source": entry.freshness_source,
                    "freshness_status": entry.freshness_status,
                    "modified_time": entry.modified_at,
                    "size_bytes": entry.size_bytes,
                    "relative_path": entry.relative_path,
                    "title": entry.title,
                }
            )
    return path


def render_archive_viewer_summary_md(result: ArchiveViewerResult) -> str:
    s = result.summary
    lines = [
        "# DeepSignal 리포트 보관함 요약",
        "",
        "생성 시각:",
        result.generated_at,
        "",
        "## 운영 요약",
        "",
        f"- 전체 리포트: {s.get('total_reports')}",
        f"- 경고: {s.get('warning_count')}",
        f"- 차단/오류: {s.get('blocked_error_count')}",
        f"- 주의 필요 항목: {s.get('needs_attention_count')}",
        "",
        "## 최근 상태",
        "",
        f"- 최근 안전 점검: {label_status(str(s.get('latest_safety_audit_status')))} ({s.get('latest_safety_audit_status')})",
        f"- 최근 주간 점검: {label_status(str(s.get('latest_weekly_maintenance_status')))} ({s.get('latest_weekly_maintenance_status')})",
        f"- 최근 리스크 경고: {label_status(str(s.get('latest_risk_alert_status')))} ({s.get('latest_risk_alert_status')})",
        f"- 최근 계좌 정합성: {label_status(str(s.get('latest_reconcile_status')))} ({s.get('latest_reconcile_status')})",
        f"- 최근 실거래 승인: {label_status(str(s.get('latest_live_approval_status')))} ({s.get('latest_live_approval_status')})",
        "",
        "## 유형별 최신 리포트",
        "",
    ]
    for report_type, entry in sorted(result.latest_by_type.items()):
        lines.append(
            f"- {label_report_type(report_type)}: [{entry.relative_path}]({entry.relative_path})"
            f" - {label_status(entry.status)} / {label_severity(entry.severity)}"
        )
    lines.extend(["", "## 주의 필요 항목", ""])
    if result.needs_attention:
        for item in result.needs_attention:
            rel = str(item.get("relative_path") or "")
            status = str(item.get("status") or "")
            severity = str(item.get("severity") or "")
            reasons = ", ".join(str(r) for r in item.get("reasons", []))
            lines.append(f"- [{rel}]({rel}) - {label_status(status)} / {label_severity(severity)} - {reasons}")
    else:
        lines.append("- 없음")
    lines.extend(["", "## 주요 리포트 링크", ""])
    for entry in result.entries[:20]:
        lines.append(f"- [{entry.relative_path}]({entry.relative_path}) - {label_report_type(entry.report_type)}")
    lines.extend(["", "## 사용 가능한 필터 프리셋", ""])
    for preset in DEFAULT_ARCHIVE_VIEWER_PRESETS:
        lines.append(f"- {preset['label']}: {preset['description']}")
    trend = result.trend_analytics
    lines.extend(["", "## 운영 추세", ""])
    warning_total = sum(int(point.get("count") or 0) for point in trend.get("warning_trend_7d", []))
    blocked_total = sum(int(point.get("count") or 0) for point in trend.get("blocked_trend_7d", []))
    lines.append(f"- 최근 {trend.get('trend_days', 7)}일 경고 합계: {warning_total}")
    lines.append(f"- 최근 {trend.get('trend_days', 7)}일 차단/오류 합계: {blocked_total}")
    lines.extend(["", "### 반복 문제 유형", ""])
    repeated = trend.get("repeated_problem_types", [])
    if repeated:
        for item in repeated:
            lines.append(f"- {label_report_type(item.get('report_type'))} ({item.get('report_type')}): {item.get('count')}회")
    else:
        lines.append("- 없음")
    lines.extend(["", "### 유형별 주의 항목 Top 5", ""])
    top_attention = sorted(trend.get("needs_attention_by_type", {}).items(), key=lambda item: (-int(item[1]), item[0]))[:5]
    if top_attention:
        for report_type, count in top_attention:
            lines.append(f"- {label_report_type(report_type)} ({report_type}): {count}")
    else:
        lines.append("- 없음")
    lines.extend(["", "### 일자별 요약", "", "| 일자 | 전체 | 경고 | 차단/오류 |", "|------|------|------|-----------|"])
    for day, bucket in sorted(trend.get("by_day", {}).items(), reverse=True):
        lines.append(f"| {day} | {bucket.get('total')} | {bucket.get('warning')} | {bucket.get('blocked')} |")
    fs = s.get("freshness_source_summary") if isinstance(s.get("freshness_source_summary"), dict) else {}
    lines.extend(["", "## Freshness 기준 요약", ""])
    for key in ("generated_at", "markdown_header", "mtime_fallback", "unknown"):
        lines.append(f"- {label_freshness_source(key)}: {int(fs.get(key, 0) or 0)}")
    if int(fs.get("mtime_fallback", 0) or 0) > int(fs.get("generated_at", 0) or 0):
        lines.append("")
        lines.append(
            "> **안내:** mtime fallback 비중이 높습니다. 구버전 산출물이거나 복사된 파일일 수 있으니 "
            "Daily AI workflow는 JSON `generated_at`을 확인하세요."
        )
    lines.extend(
        [
            "",
            "## 안전 경계",
            "",
            "- 이 요약은 metadata 기반 read-only export입니다.",
            "- 리포트 원문 전체, DB 내용, token, app secret, account 원문은 포함하지 않습니다.",
            "- 실주문, 자동 복구, cleanup apply, archive 이동, 파일 삭제 기능이 아닙니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_archive_viewer(
    *,
    output_dir: str | Path = "outputs",
    archive_dir: str | Path | None = "outputs/archive",
    limit: int = 200,
    create_csv: bool = True,
    create_summary_md: bool = True,
    trend_days: int = 7,
) -> tuple[ArchiveViewerResult, Path, Path]:
    result = build_archive_viewer(output_dir=output_dir, archive_dir=archive_dir, limit=limit, trend_days=trend_days)
    html_path, json_path = write_archive_viewer(
        result,
        output_dir=output_dir,
        create_csv=create_csv,
        create_summary_md=create_summary_md,
    )
    return result, html_path, json_path


def load_archive_viewer_link_info(output_dir: str | Path = "outputs") -> ArchiveViewerLinkInfo:
    root = Path(output_dir).expanduser().resolve()
    html_path = root / "ARCHIVE_VIEWER.html"
    csv_path = root / "ARCHIVE_VIEWER.csv"
    summary_md_path = root / "ARCHIVE_VIEWER_SUMMARY.md"
    presets_path = root / "ARCHIVE_VIEWER_PRESETS.json"
    json_files = [p for p in root.glob("archive_viewer_*.json") if p.is_file()]
    latest_json = max(json_files, key=lambda p: p.stat().st_mtime) if json_files else None
    total_reports: int | None = None
    updated_at: str | None = None
    freshness_source_summary: dict[str, int] | None = None
    if latest_json:
        data = _safe_json_summary_for_link(latest_json)
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        total_reports = int(summary.get("total_reports") or 0)
        updated_at = str(data.get("generated_at") or datetime.fromtimestamp(latest_json.stat().st_mtime).isoformat(timespec="seconds"))
        raw_fs = summary.get("freshness_source_summary")
        if isinstance(raw_fs, dict):
            freshness_source_summary = {str(k): int(v or 0) for k, v in raw_fs.items()}
    elif html_path.is_file():
        updated_at = datetime.fromtimestamp(html_path.stat().st_mtime).isoformat(timespec="seconds")
    return ArchiveViewerLinkInfo(
        status="AVAILABLE" if html_path.is_file() or latest_json else "NOT_AVAILABLE",
        html_path=html_path.as_posix() if html_path.is_file() else None,
        csv_path=csv_path.as_posix() if csv_path.is_file() else None,
        summary_md_path=summary_md_path.as_posix() if summary_md_path.is_file() else None,
        presets_path=presets_path.as_posix() if presets_path.is_file() else None,
        json_path=latest_json.as_posix() if latest_json else None,
        html_rel=_rel(html_path, root) if html_path.is_file() else None,
        csv_rel=_rel(csv_path, root) if csv_path.is_file() else None,
        summary_md_rel=_rel(summary_md_path, root) if summary_md_path.is_file() else None,
        presets_rel=_rel(presets_path, root) if presets_path.is_file() else None,
        json_rel=_rel(latest_json, root) if latest_json else None,
        total_reports=total_reports,
        updated_at=updated_at,
        freshness_source_summary=freshness_source_summary,
        message="Archive viewer generated" if html_path.is_file() or latest_json else "Archive viewer has not been generated yet",
    )


def _safe_json_summary_for_link(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def format_archive_viewer_console(
    result: ArchiveViewerResult,
    html_path: Path,
    json_path: Path,
    *,
    generated_csv: bool = True,
    generated_summary_md: bool = True,
) -> str:
    csv_path = html_path.parent / "ARCHIVE_VIEWER.csv"
    summary_md_path = html_path.parent / "ARCHIVE_VIEWER_SUMMARY.md"
    lines = [
        "DeepSignal archive viewer created",
        f"Reports: {len(result.entries)}",
        f"Warnings: {result.summary.get('warning_count')}",
        f"Blocked/Error: {result.summary.get('blocked_error_count')}",
        f"HTML: {html_path.as_posix()}",
    ]
    if generated_csv:
        lines.append(f"CSV: {csv_path.as_posix()}")
    else:
        lines.append("CSV: (skipped)")
    if generated_summary_md:
        lines.append(f"Markdown Summary: {summary_md_path.as_posix()}")
    else:
        lines.append("Markdown Summary: (skipped)")
    fs = result.summary.get("freshness_source_summary")
    if isinstance(fs, dict) and fs:
        lines.append("Archive Viewer freshness summary:")
        for key in ("generated_at", "markdown_header", "mtime_fallback", "unknown"):
            lines.append(f"- {label_freshness_source(key)}: {int(fs.get(key, 0) or 0)}")
        if int(fs.get("mtime_fallback", 0) or 0) > int(fs.get("generated_at", 0) or 0):
            lines.append(
                "Note: mtime fallback exceeds generated_at count; verify JSON generated_at for Daily AI workflow."
            )
    lines.extend(
        [
            f"JSON: {json_path.as_posix()}",
            "Note: archive-viewer is read-only; no network, orders, cleanup, archive moves, or deletes.",
        ]
    )
    return "\n".join(lines)
