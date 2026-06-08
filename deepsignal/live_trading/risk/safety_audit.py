"""Read-only safety audit command ([실전-30]).

The audit reads local outputs and an optional SQLite DB in read-only mode, then
writes JSON/Markdown reports. It does not call networks, cleanup, live-approve,
order APIs, or scheduler tooling.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


SAFETY_AUDIT_OK = "SAFETY_AUDIT_OK"
SAFETY_AUDIT_WARNING = "SAFETY_AUDIT_WARNING"
SAFETY_AUDIT_BLOCKED = "SAFETY_AUDIT_BLOCKED"

CHECKLIST_FILES = (
    "DAILY_CHECKLIST.md",
    "PRE_MARKET_CHECKLIST.md",
    "POST_TRADE_CHECKLIST.md",
    "WEEKLY_MAINTENANCE_CHECKLIST.md",
    "SAFETY_RULES.md",
)

REQUIRED_SAFETY_PHRASES = (
    "live-approve --execute 자동화 금지",
    "--final-confirm 자동 주입 금지",
    ".env 커밋 금지",
    "SELL 자동화 금지",
    "시장가 금지",
    "KIS POST 직접 호출 금지",
)

STATIC_REPORTS = (
    "REPORT_HEALTH.md",
    "WEEKLY_MAINTENANCE.md",
    "REPORT_INDEX.html",
    "OPS_DASHBOARD.html",
    "RISK_ALERT.md",
    "SELL_PLAN.md",
)

RISK_PATTERNS = (
    "live_account_snapshot_*.json",
    "reconcile_live_account_*.json",
    "risk_alert_*.json",
    "live_fill_summary_*.json",
    "live_approval_audit_*.json",
)

SAFETY_AUDIT_NOT_AVAILABLE = "NOT_AVAILABLE"

BLOCKED_KEYWORDS = (
    "RECONCILE_MISMATCH",
    "LIVE_EXECUTION_BLOCKED_BY_GUARD",
    "duplicate order blocked",
    "partial_fill_open",
    "partial fill open",
    "stale account snapshot",
)


@dataclass
class SafetyAuditIssue:
    severity: str
    category: str
    message: str
    recommended_action: str


@dataclass
class SafetyAuditResult:
    status: str
    generated_at: str
    output_dir: str
    db_path: str | None
    strict: bool
    issues: list[SafetyAuditIssue]
    checks: dict[str, Any]
    next_actions: list[str]


@dataclass
class SafetyAuditLinkInfo:
    status: str
    markdown_path: str | None
    json_path: str | None
    markdown_rel: str | None
    json_rel: str | None
    updated_at: str | None
    warning_count: int
    blocked_count: int
    message: str


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _age_hours(path: Path, now: datetime) -> float:
    return max(0.0, (now - _mtime_utc(path)).total_seconds() / 3600.0)


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _latest(root: Path, pattern: str) -> Path | None:
    matches = [p for p in root.glob(pattern) if p.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_safety_audit_link_info(output_dir: str | Path = "outputs") -> SafetyAuditLinkInfo:
    """Return optional local link/status info for dashboard/index rendering."""
    root = Path(output_dir).expanduser().resolve()
    md = root / "SAFETY_AUDIT.md"
    latest_json = _latest(root, "safety_audit_*.json")
    status = SAFETY_AUDIT_NOT_AVAILABLE
    updated_at: str | None = None
    warning_count = 0
    blocked_count = 0
    message = "Safety audit has not been generated yet"

    if latest_json is not None:
        data = _load_json(latest_json) or {}
        status = str(data.get("status") or SAFETY_AUDIT_NOT_AVAILABLE)
        updated_at = str(data.get("generated_at") or datetime.fromtimestamp(latest_json.stat().st_mtime).isoformat(timespec="seconds"))
        issues = data.get("issues") if isinstance(data.get("issues"), list) else []
        warning_count = sum(1 for issue in issues if isinstance(issue, dict) and str(issue.get("severity") or "").upper() == "WARNING")
        blocked_count = sum(1 for issue in issues if isinstance(issue, dict) and str(issue.get("severity") or "").upper() == "BLOCKED")
        message = "Safety audit generated"
    elif md.is_file():
        updated_at = datetime.fromtimestamp(md.stat().st_mtime).isoformat(timespec="seconds")
        message = "Safety audit Markdown exists but latest JSON was not found"

    return SafetyAuditLinkInfo(
        status=status,
        markdown_path=md.as_posix() if md.is_file() else None,
        json_path=latest_json.as_posix() if latest_json else None,
        markdown_rel=_rel(md, root) if md.is_file() else None,
        json_rel=_rel(latest_json, root) if latest_json else None,
        updated_at=updated_at,
        warning_count=warning_count,
        blocked_count=blocked_count,
        message=message,
    )


def _issue(severity: str, category: str, message: str, action: str) -> SafetyAuditIssue:
    return SafetyAuditIssue(severity=severity, category=category, message=message, recommended_action=action)


def _dedupe_actions(issues: list[SafetyAuditIssue]) -> list[str]:
    actions: list[str] = []
    for issue in issues:
        action = issue.recommended_action.strip()
        if action and action not in actions:
            actions.append(action)
    return actions or ["No action required."]


def _status_from(issues: list[SafetyAuditIssue], *, strict: bool) -> str:
    severities = {issue.severity.upper() for issue in issues}
    if "BLOCKED" in severities:
        return SAFETY_AUDIT_BLOCKED
    if strict and ("WARNING" in severities):
        return SAFETY_AUDIT_BLOCKED
    if "WARNING" in severities:
        return SAFETY_AUDIT_WARNING
    return SAFETY_AUDIT_OK


def _scan_text(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(data)


def _safe_snippet(text: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    return compact[:limit]


def _looks_stale_json(data: dict[str, Any], now: datetime, max_age_hours: float) -> tuple[bool, str | None]:
    candidates: list[Any] = [
        data.get("snapshot_time"),
        data.get("timestamp"),
        data.get("generated_at"),
        data.get("created_at"),
    ]
    summary = data.get("summary")
    if isinstance(summary, dict):
        candidates.extend([summary.get("snapshot_time"), summary.get("generated_at"), summary.get("finished_at")])
    for raw in candidates:
        dt = _parse_dt(raw)
        if dt is None:
            continue
        age = (now - dt).total_seconds() / 3600.0
        if age > max_age_hours:
            return True, dt.isoformat()
        return False, dt.isoformat()
    return False, None


def _audit_checklists(root: Path, issues: list[SafetyAuditIssue], checks: dict[str, Any]) -> None:
    checklist_root = root / "checklists"
    entries: dict[str, Any] = {}
    for name in CHECKLIST_FILES:
        path = checklist_root / name
        exists = path.is_file()
        entries[name] = {"path": path.as_posix(), "exists": exists}
        if not exists:
            issues.append(_issue("WARNING", "checklists", f"missing checklist: {name}", "Run python main.py generate-checklists --output-dir outputs/checklists"))
    safety_path = checklist_root / "SAFETY_RULES.md"
    missing_phrases: list[str] = []
    if safety_path.is_file():
        text = safety_path.read_text(encoding="utf-8", errors="replace")
        for phrase in REQUIRED_SAFETY_PHRASES:
            if phrase not in text:
                missing_phrases.append(phrase)
    else:
        missing_phrases = list(REQUIRED_SAFETY_PHRASES)
    entries["SAFETY_RULES.md"]["required_phrases_missing"] = missing_phrases
    for phrase in missing_phrases:
        issues.append(_issue("BLOCKED", "safety_rules", f"SAFETY_RULES.md missing required phrase: {phrase}", "Regenerate and review checklists before live trading."))
    checks["checklists"] = entries


def _audit_static_reports(root: Path, now: datetime, max_age_hours: float, issues: list[SafetyAuditIssue], checks: dict[str, Any]) -> None:
    reports: dict[str, Any] = {}
    for name in STATIC_REPORTS:
        path = root / name
        exists = path.is_file()
        entry: dict[str, Any] = {"path": path.as_posix(), "exists": exists}
        if exists:
            age = _age_hours(path, now)
            entry["modified_at"] = _mtime_utc(path).isoformat()
            entry["age_hours"] = age
            if age > max_age_hours:
                issues.append(_issue("WARNING", "reports", f"static report is stale: {name}", "Run weekly-maintenance/report-index/html-dashboard manually before proceeding."))
        else:
            issues.append(_issue("WARNING", "reports", f"missing static report: {name}", "Run python main.py weekly-maintenance --output-dir outputs --archive-dir outputs/archive"))
        reports[name] = entry
    checks["static_reports"] = reports


def _audit_risk_files(root: Path, now: datetime, max_age_hours: float, issues: list[SafetyAuditIssue], checks: dict[str, Any]) -> None:
    latest_reports: dict[str, Any] = {}
    state = root / "LATEST_RECONCILE_STATE.json"
    if state.is_file():
        data = _load_json(state)
        latest_reports["LATEST_RECONCILE_STATE.json"] = {"path": state.as_posix(), "exists": True, "success": data.get("success") if data else None}
        if data is None:
            issues.append(_issue("WARNING", "reconcile", "LATEST_RECONCILE_STATE.json cannot be parsed", "Regenerate reconcile report manually."))
        elif data.get("success") is False:
            issues.append(_issue("BLOCKED", "reconcile", "reconcile mismatch detected in LATEST_RECONCILE_STATE.json", "Stop live order flow and reconcile against broker app."))
    else:
        latest_reports["LATEST_RECONCILE_STATE.json"] = {"path": state.as_posix(), "exists": False}
        issues.append(_issue("WARNING", "reconcile", "LATEST_RECONCILE_STATE.json is missing", "Run reconcile-live-account manually before live trading."))

    for pattern in RISK_PATTERNS:
        path = _latest(root, pattern)
        entry: dict[str, Any] = {"exists": path is not None, "latest_path": path.as_posix() if path else None}
        if path is None:
            issues.append(_issue("WARNING", "risk_files", f"no recent file matching {pattern}", "Run the relevant manual check command before proceeding."))
            latest_reports[pattern] = entry
            continue
        entry["modified_at"] = _mtime_utc(path).isoformat()
        entry["age_hours"] = _age_hours(path, now)
        data = _load_json(path)
        if data is not None:
            stale, timestamp = _looks_stale_json(data, now, max_age_hours)
            entry["timestamp"] = timestamp
            entry["status"] = data.get("status") or data.get("final_status")
            text = _scan_text(data)
            if pattern == "live_account_snapshot_*.json" and stale:
                issues.append(_issue("BLOCKED", "account_snapshot", f"stale account snapshot: {path.name}", "Refresh account snapshot manually and reconcile."))
            if pattern == "reconcile_live_account_*.json" and data.get("success") is False:
                issues.append(_issue("BLOCKED", "reconcile", f"reconcile mismatch detected: {path.name}", "Stop live order flow and reconcile against broker app."))
            if any(keyword.lower() in text.lower() for keyword in BLOCKED_KEYWORDS):
                issues.append(_issue("BLOCKED", "stop_condition", f"stop condition text detected in {path.name}", "Review the latest risk/reconcile/fill/audit reports before proceeding."))
            if "kis_env=live" in text.lower() or "production api host" in text.lower():
                issues.append(_issue("WARNING", "kis_env", f"KIS live environment warning text detected in {path.name}", "Confirm KIS_ENV=live is intended before any live-account operation."))
        else:
            issues.append(_issue("WARNING", "risk_files", f"cannot parse latest {pattern}: {path.name}", "Regenerate the report manually."))
        latest_reports[pattern] = entry
    checks["latest_risk_files"] = latest_reports


def _audit_pre_trade(root: Path, now: datetime, max_age_minutes: int, issues: list[SafetyAuditIssue], checks: dict[str, Any]) -> None:
    path = _latest(root, "pre_trade_runbook_*.json")
    entry: dict[str, Any] = {"exists": path is not None, "latest_path": path.as_posix() if path else None}
    if path is None:
        issues.append(_issue("WARNING", "pre_trade_runbook", "PRE_TRADE_READY runbook not found", "Run pre-trade-runbook manually before live-approve --execute."))
        checks["pre_trade_runbook"] = entry
        return
    data = _load_json(path)
    if data is None:
        issues.append(_issue("WARNING", "pre_trade_runbook", f"pre-trade runbook cannot be parsed: {path.name}", "Regenerate pre-trade runbook manually."))
        checks["pre_trade_runbook"] = entry
        return
    final_status = str(data.get("final_status") or "")
    entry["final_status"] = final_status
    if final_status != "PRE_TRADE_READY":
        issues.append(_issue("WARNING", "pre_trade_runbook", f"latest pre-trade runbook is not PRE_TRADE_READY: {final_status or '-'}", "Regenerate pre-trade runbook and confirm readiness."))
    dt = None
    for key in ("finished_at", "started_at", "generated_at"):
        dt = _parse_dt(data.get(key))
        if dt is not None:
            break
    if dt is not None:
        age_min = (now - dt).total_seconds() / 60.0
        entry["timestamp"] = dt.isoformat()
        entry["age_minutes"] = max(0.0, age_min)
        if age_min > max_age_minutes:
            issues.append(_issue("WARNING", "pre_trade_runbook", "PRE_TRADE_READY runbook appears expired", "Rerun pre-trade-runbook immediately before manual live approval."))
    else:
        issues.append(_issue("WARNING", "pre_trade_runbook", "pre-trade runbook timestamp missing", "Regenerate pre-trade runbook manually."))
    checks["pre_trade_runbook"] = entry


def _audit_db_readonly(db_path: str | Path | None, issues: list[SafetyAuditIssue], checks: dict[str, Any]) -> None:
    if not db_path:
        checks["db"] = {"path": None, "exists": False, "checked": False}
        return
    db = Path(db_path)
    entry: dict[str, Any] = {"path": db.as_posix(), "exists": db.is_file(), "checked": False}
    if not db.is_file():
        issues.append(_issue("WARNING", "db", f"DB file not found: {db.as_posix()}", "Pass --db-path or run account sync before live trading."))
        checks["db"] = entry
        return
    try:
        uri = f"{db.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('real_order_history', 'real_fill_history', 'real_account_snapshots')"
                ).fetchall()
            }
            entry["tables"] = sorted(tables)
            entry["checked"] = True
            if "real_account_snapshots" in tables:
                row = conn.execute("SELECT snapshot_time FROM real_account_snapshots WHERE broker = ? ORDER BY snapshot_time DESC, id DESC LIMIT 1", ("kis",)).fetchone()
                entry["latest_snapshot_time"] = row["snapshot_time"] if row else None
            if {"real_order_history", "real_fill_history"}.issubset(tables):
                rows = conn.execute(
                    """
                    SELECT o.order_id, o.symbol, o.quantity AS ordered_quantity,
                           COALESCE(SUM(f.fill_quantity), 0) AS filled_quantity
                    FROM real_order_history o
                    LEFT JOIN real_fill_history f
                      ON f.broker = o.broker AND f.order_id = o.order_id
                    WHERE o.broker = ? AND o.order_id IS NOT NULL AND o.order_id != ''
                    GROUP BY o.order_id, o.symbol, o.quantity
                    HAVING filled_quantity > 0 AND filled_quantity < ordered_quantity
                    """,
                    ("kis",),
                ).fetchall()
                partials = [dict(row) for row in rows]
                entry["open_partial_fill_count"] = len(partials)
                entry["open_partial_fills"] = partials[:20]
                if partials:
                    issues.append(_issue("BLOCKED", "partial_fill", f"partial fill open in DB: {len(partials)} order(s)", "Check live-order-status/live-fill-summary and broker app before reordering."))
    except (sqlite3.Error, OSError, ValueError) as e:
        issues.append(_issue("WARNING", "db", f"read-only DB audit failed: {e}", "Inspect the local SQLite DB path."))
        entry["error"] = str(e)
    checks["db"] = entry


def _project_root_from_output(output_dir: Path) -> Path | None:
    """Locate repo root (main.py) from outputs path for project script scans."""
    resolved = output_dir.expanduser().resolve()
    for parent in [resolved, *resolved.parents]:
        if (parent / "main.py").is_file():
            return parent
    return None


def _is_safety_audit_self_artifact(path: Path) -> bool:
    name = path.name
    if name == "SAFETY_AUDIT.md":
        return True
    return name.startswith("safety_audit_") and name.endswith(".json")


def _is_output_report_artifact(path: Path, output_dir: Path) -> bool:
    """Generated reports under output_dir — not automation executables."""
    try:
        path.relative_to(output_dir.resolve())
    except ValueError:
        return False
    if _is_safety_audit_self_artifact(path):
        return True
    suffix = path.suffix.lower()
    if suffix in {".md", ".html"}:
        return True
    if suffix == ".json":
        return True
    if "checklists" in path.parts:
        return True
    return False


def _is_scheduler_like_filename(path: Path) -> bool:
    name = path.name.lower()
    if name.endswith((".plist", ".sh", ".bash")):
        return True
    return any(token in name for token in ("cron", "crontab", "launchd"))


def _should_scan_final_confirm_content(path: Path, *, output_dir: Path) -> bool:
    """Only scan real automation/config files, never output report documents."""
    if _is_safety_audit_self_artifact(path):
        return False
    if _is_output_report_artifact(path, output_dir):
        return False
    return _is_scheduler_like_filename(path)


def _text_suggests_final_confirm_automation(text: str) -> bool:
    lower = text.lower()
    if "live-approve" not in lower or "--final-confirm" not in lower:
        return False
    automation_markers = (
        "cron",
        "launchd",
        "crontab",
        "#!/bin/bash",
        "#!/bin/sh",
        "자동 주입",
        "automate",
        "automation",
        "auto_execute",
        "auto-execute",
    )
    return any(marker in lower for marker in automation_markers)


def _scan_final_confirm_automation(path: Path, suspicious: list[str]) -> None:
    if path.stat().st_size > 200_000:
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if _text_suggests_final_confirm_automation(text):
        suspicious.append(f"{path.as_posix()}: {_safe_snippet(text)}")


def _audit_scheduler_and_confirmation(root: Path, issues: list[SafetyAuditIssue], checks: dict[str, Any]) -> None:
    suspicious: list[str] = []
    scheduler_files: list[str] = []
    scanned_paths: list[str] = []

    def _consider(path: Path, *, scope: str) -> None:
        if not path.is_file() or path.name.startswith("._"):
            return
        try:
            if scope == "output_dir" and "checklists" in path.relative_to(root).parts:
                return
        except ValueError:
            pass
        if scope == "output_dir" and _is_scheduler_like_filename(path):
            scheduler_files.append(path.as_posix())
        if not _should_scan_final_confirm_content(path, output_dir=root):
            return
        scanned_paths.append(path.as_posix())
        _scan_final_confirm_automation(path, suspicious)

    if root.is_dir():
        for path in root.rglob("*"):
            _consider(path, scope="output_dir")

    project = _project_root_from_output(root)
    if project is not None:
        scripts_dir = project / "scripts"
        if scripts_dir.is_dir():
            for path in scripts_dir.rglob("*"):
                _consider(path, scope="project_scripts")

    if scheduler_files:
        issues.append(
            _issue(
                "BLOCKED",
                "automation",
                f"scheduler/script-like files found under output_dir: {len(scheduler_files)}",
                "Do not use cron/launchd/plist/shell automation for live trading.",
            )
        )
    if suspicious:
        issues.append(
            _issue(
                "BLOCKED",
                "final_confirm",
                f"final confirmation automation suspicion found: {len(suspicious)} file(s)",
                "Remove any automation around --final-confirm and live-approve --execute.",
            )
        )
    checks["automation_scan"] = {
        "scanned": root.is_dir() or project is not None,
        "project_root": project.as_posix() if project else None,
        "scanned_automation_paths": scanned_paths[:50],
        "scheduler_like_files": scheduler_files[:50],
        "suspicious_final_confirm": suspicious[:20],
    }


def _audit_daily_ai_workflow(
    root: Path,
    issues: list[SafetyAuditIssue],
    checks: dict[str, Any],
    *,
    freshness_date: str | date | None = None,
) -> None:
    from deepsignal.live_trading.daily_ai_freshness import (
        SEVERITY_BLOCKED,
        build_daily_ai_freshness,
        freshness_label_ko,
        freshness_results_to_dict,
    )
    from deepsignal.live_trading.daily_ai_status_reader import read_daily_ai_workflow_status

    status = read_daily_ai_workflow_status(root, freshness_date=freshness_date)
    payload = status.to_dict()
    freshness = freshness_results_to_dict(build_daily_ai_freshness(root, freshness_date=freshness_date))
    payload["freshness"] = freshness
    checks["daily_ai_workflow"] = payload
    checks["daily_ai_freshness"] = freshness

    for warning in status.warnings:
        issues.append(
            _issue(
                "WARNING",
                "daily_ai_workflow",
                warning,
                status.next_action,
            )
        )

    freshness_labels = {
        "plan": "계획 파일",
        "latest_order_plan": "최신 주문안",
        "approval": "승인 파일",
        "execution": "실행 감사",
        "report": "일일 리포트",
        "status": "상태 리포트",
    }
    for key, label in freshness_labels.items():
        entry = freshness.get(key) or {}
        entry_status = str(entry.get("status") or "MISSING")
        if entry_status == "STALE":
            severity = "BLOCKED" if str(entry.get("severity") or "") == SEVERITY_BLOCKED else "WARNING"
            issues.append(
                _issue(
                    severity,
                    "daily_ai_freshness",
                    f"{label}: {freshness_label_ko(entry_status)} ({entry.get('warning') or '오래된 파일'})",
                    status.next_action,
                )
            )
        elif entry_status == "MISSING" and key in {"plan", "latest_order_plan"}:
            issues.append(
                _issue(
                    "WARNING",
                    "daily_ai_freshness",
                    f"{label}: {freshness_label_ko(entry_status)}",
                    status.next_action,
                )
            )


def run_safety_audit(
    *,
    output_dir: str | Path = "outputs",
    db_path: str | Path | None = None,
    strict: bool = False,
    max_report_age_hours: float = 24.0,
    max_snapshot_age_hours: float = 24.0,
    max_pre_trade_age_minutes: int = 10,
    freshness_date: str | date | None = None,
) -> SafetyAuditResult:
    """Run a local read-only safety audit and return the result."""
    root = Path(output_dir)
    now = _now_utc()
    issues: list[SafetyAuditIssue] = []
    checks: dict[str, Any] = {"output_dir_exists": root.is_dir()}
    if not root.is_dir():
        issues.append(_issue("WARNING", "outputs", f"output_dir does not exist: {root.as_posix()}", "Run manual dry-run/report generation first."))
    _audit_checklists(root, issues, checks)
    _audit_static_reports(root, now, max_report_age_hours, issues, checks)
    _audit_risk_files(root, now, max_snapshot_age_hours, issues, checks)
    _audit_pre_trade(root, now, max_pre_trade_age_minutes, issues, checks)
    _audit_db_readonly(db_path, issues, checks)
    _audit_scheduler_and_confirmation(root, issues, checks)
    _audit_daily_ai_workflow(root, issues, checks, freshness_date=freshness_date)
    status = _status_from(issues, strict=strict)
    return SafetyAuditResult(
        status=status,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        output_dir=root.as_posix(),
        db_path=Path(db_path).as_posix() if db_path else None,
        strict=bool(strict),
        issues=issues,
        checks=checks,
        next_actions=_dedupe_actions(issues),
    )


def write_safety_audit(result: SafetyAuditResult, *, output_dir: str | Path = "outputs") -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    jp = root / f"safety_audit_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    mp = root / "SAFETY_AUDIT.md"
    body = asdict(result)
    body["network_called"] = False
    body["kis_post_called"] = False
    body["live_approve_called"] = False
    body["execute_called"] = False
    body["cleanup_apply_called"] = False
    body["archive_moved"] = False
    body["files_deleted"] = False
    body["actual_order_attempted"] = False
    body["modified_files"] = [jp.as_posix(), mp.as_posix()]
    body["daily_ai_workflow"] = result.checks.get("daily_ai_workflow", {})
    body["daily_ai_freshness"] = result.checks.get("daily_ai_freshness", {})
    jp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _SAFETY_STATUS_KO: dict[str, str] = {
        "SAFETY_AUDIT_OK": "✅ 정상",
        "SAFETY_AUDIT_WARNING": "⚠️ 경고",
        "SAFETY_AUDIT_CRITICAL": "🚨 위험",
    }
    _SEVERITY_KO: dict[str, str] = {
        "WARNING": "⚠️ 경고",
        "CRITICAL": "🚨 위험",
        "INFO": "ℹ️ 정보",
    }
    status_ko = _SAFETY_STATUS_KO.get(str(result.status), str(result.status))

    lines = [
        "# DeepSignal — 안전 점검 기록",
        "",
        "## 상태",
        "",
        f"- 상태: **{status_ko}**",
        f"- 생성 시각: {result.generated_at}",
        f"- 출력 폴더: `{result.output_dir}`",
        f"- DB 경로: `{result.db_path or '-'}`",
        f"- 엄격 모드: `{result.strict}`",
        "",
        "## 주의사항",
        "",
    ]
    if result.issues:
        for issue in result.issues:
            sev_ko = _SEVERITY_KO.get(str(issue.severity), str(issue.severity))
            lines.append(f"- {sev_ko} `{issue.category}`: {issue.message}")
            lines.append(f"  - 권장 조치: {issue.recommended_action}")
    else:
        lines.append("- (없음)")
    lines.extend(["", "## 다음 할 일", ""])
    for action in result.next_actions:
        lines.append(f"- {action}")
    daily_ai = result.checks.get("daily_ai_workflow", {})
    daily_freshness = result.checks.get("daily_ai_freshness", {})
    if isinstance(daily_ai, dict):
        lines.extend(
            [
                "",
                "## AI 일일 매매 운영 상태",
                "",
                f"- 계획 생성: {'완료' if daily_ai.get('checks', {}).get('plan_json_exists') else '없음'}",
                f"- Latest plan path: `{daily_ai.get('files', {}).get('live_order_plan_ai_latest.json') or '-'}`",
                f"- 승인 요청: {'완료' if daily_ai.get('checks', {}).get('approval_request_exists') else '없음'}",
                f"- 승인 상태: {daily_ai.get('approval_status') or 'NOT_AVAILABLE'}",
                f"- 실행 상태: {daily_ai.get('execution_status') or 'NOT_AVAILABLE'}",
                f"- 일일 리포트: {'완료' if daily_ai.get('checks', {}).get('report_json_exists') else '없음'}",
                f"- 다음 단계: `{daily_ai.get('next_action') or '-'}`",
            ]
        )
        warnings = daily_ai.get("warnings") if isinstance(daily_ai.get("warnings"), list) else []
        if warnings:
            lines.append("- 경고:")
            lines.extend(f"  - {w}" for w in warnings)
    if isinstance(daily_freshness, dict) and daily_freshness:
        from deepsignal.live_trading.daily_ai_freshness import freshness_label_ko, freshness_source_label_ko

        freshness_titles = {
            "plan": "계획 파일",
            "latest_order_plan": "최신 주문안",
            "approval": "승인 파일",
            "execution": "실행 감사",
            "report": "일일 리포트",
            "status": "상태 리포트",
        }
        lines.extend(["", "## 데이터 최신 여부", ""])
        for key, title in freshness_titles.items():
            entry = daily_freshness.get(key) if isinstance(daily_freshness.get(key), dict) else {}
            status_text = freshness_label_ko(str(entry.get("status") or "MISSING"))
            source_text = freshness_source_label_ko(str(entry.get("freshness_source") or ""))
            lines.append(f"- {title}: {status_text}")
            if entry.get("generated_at"):
                lines.append(f"  - 생성 시각: {entry.get('generated_at')}")
            if source_text:
                lines.append(f"  - 출처: {source_text}")
            if entry.get("warning"):
                lines.append(f"  - {entry.get('warning')}")
        ref = daily_ai.get("freshness_reference_date") if isinstance(daily_ai, dict) else None
        if ref:
            lines.append(f"- 기준 날짜: {ref}")
    lines.extend(
        [
            "",
            "## 점검 데이터 (JSON)",
            "",
            "```json",
            json.dumps(result.checks, ensure_ascii=False, indent=2),
            "```",
            "",
            "## 안전 제약 사항",
            "",
            "- 네트워크 호출 없음",
            "- KIS POST 없음",
            "- live-approve 호출 없음",
            "- --execute 호출 없음",
            "- 매도 자동화 없음",
            "- 시장가 주문 없음",
            "- cron/launchd/plist 등 스케줄 설정 없음",
            "- 아카이브 이동, 파일 삭제 없음",
            "- .env 값, 토큰, API 시크릿, 계좌번호 출력 없음",
        ]
    )
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp


def format_safety_audit_console(result: SafetyAuditResult, json_path: Path | None = None, md_path: Path | None = None) -> str:
    lines = ["DeepSignal safety audit", f"Status: {result.status}", "Issues:"]
    if result.issues:
        for issue in result.issues:
            lines.append(f"- {issue.severity} {issue.category}: {issue.message}")
    else:
        lines.append("- (none)")
    if json_path:
        lines.append(f"JSON: {json_path.as_posix()}")
    if md_path:
        lines.append(f"Markdown: {md_path.as_posix()}")
    lines.append("Note: safety-audit is read-only; no network, cleanup, live-approve, execute, order, SELL, or KIS POST.")
    return "\n".join(lines)
