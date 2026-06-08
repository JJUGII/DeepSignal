"""운영 산출물/DB health check ([실전-25]). 진단 전용."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class HealthIssue:
    severity: str
    category: str
    message: str
    recommended_action: str


@dataclass
class ReportHealthResult:
    status: str
    generated_at: str
    output_dir: str
    db_path: str
    issues: list[HealthIssue]
    checks: dict[str, Any]
    next_actions: list[str]


HEALTH_OK = "HEALTH_OK"
HEALTH_WARNING = "HEALTH_WARNING"
HEALTH_CRITICAL = "HEALTH_CRITICAL"
HEALTH_NO_DATA = "HEALTH_NO_DATA"

STATIC_REPORTS = (
    "OPS_DASHBOARD.html",
    "REPORT_INDEX.html",
    "DAILY_OPS_SUMMARY.md",
    "RISK_ALERT.md",
    "SELL_PLAN.md",
    "OPS_DRY_RUN.md",
)

JSON_PATTERNS = (
    "live_account_snapshot_*.json",
    "reconcile_live_account_*.json",
    "risk_alert_*.json",
    "ops_dashboard_*.json",
    "sell_plan_*.json",
    "daily_ops_summary_*.json",
)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _age_hours(path: Path, now: datetime) -> float:
    return max(0.0, (now - _mtime_utc(path)).total_seconds() / 3600.0)


def _latest_match(root: Path, pattern: str) -> Path | None:
    files = [p for p in root.glob(pattern) if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _parse_dt(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _issue(severity: str, category: str, message: str, action: str) -> HealthIssue:
    return HealthIssue(severity=severity, category=category, message=message, recommended_action=action)


def _dedupe_actions(issues: list[HealthIssue]) -> list[str]:
    actions: list[str] = []
    for issue in issues:
        action = issue.recommended_action.strip()
        if action and action not in actions:
            actions.append(action)
    if not actions:
        actions.append("No action required.")
    return actions


def _status_from(issues: list[HealthIssue], checks: dict[str, Any]) -> str:
    severities = {i.severity.upper() for i in issues}
    if "CRITICAL" in severities:
        return HEALTH_CRITICAL
    if bool(checks.get("no_operational_data")):
        return HEALTH_NO_DATA
    if "WARNING" in severities:
        return HEALTH_WARNING
    return HEALTH_OK


def run_report_health_check(
    *,
    output_dir: str | Path = "outputs",
    db_path: str | Path = "data/deepsignal.db",
    max_age_hours: float = 24.0,
    max_output_files: int = 500,
    token_expiry_warning_minutes: int = 30,
) -> ReportHealthResult:
    """로컬 운영 산출물과 DB 상태를 진단한다. 수정/삭제/네트워크 호출 없음."""
    root = Path(output_dir)
    db = Path(db_path)
    now = _now_utc()
    issues: list[HealthIssue] = []
    checks: dict[str, Any] = {
        "static_reports": {},
        "latest_json_reports": {},
        "db": {},
        "appledouble": {},
        "token_cache": {},
        "dashboard": {},
        "outputs": {},
    }

    root_exists = root.exists()
    checks["outputs"]["exists"] = root_exists
    if not root_exists:
        issues.append(
            _issue(
                "WARNING",
                "outputs",
                f"output_dir does not exist: {root.as_posix()}",
                "Run python main.py ops-dry-run --output-dir outputs",
            )
        )

    any_report_data = False
    for name in STATIC_REPORTS:
        path = root / name
        exists = path.is_file()
        any_report_data = any_report_data or exists
        checks["static_reports"][name] = {
            "path": path.as_posix(),
            "exists": exists,
            "modified_at": _mtime_utc(path).isoformat() if exists else None,
        }
        if not exists:
            action = "Run python main.py html-dashboard --output-dir outputs"
            if name == "REPORT_INDEX.html":
                action = "Run python main.py report-index --output-dir outputs --archive-dir outputs/archive"
            elif name in {"OPS_DRY_RUN.md", "DAILY_OPS_SUMMARY.md"}:
                action = "Run python main.py ops-dry-run --output-dir outputs"
            elif name in {"RISK_ALERT.md", "SELL_PLAN.md"}:
                action = "Run python main.py ops-dry-run --network --broker kis"
            issues.append(_issue("WARNING", "outputs", f"missing report: {name}", action))

    for pattern in JSON_PATTERNS:
        latest = _latest_match(root, pattern)
        if latest is None:
            checks["latest_json_reports"][pattern] = {"exists": False, "latest_path": None}
            issues.append(
                _issue(
                    "WARNING",
                    "reports",
                    f"no report matching {pattern}",
                    "Run python main.py ops-dry-run --network --broker kis",
                )
            )
            continue
        any_report_data = True
        age = _age_hours(latest, now)
        checks["latest_json_reports"][pattern] = {
            "exists": True,
            "latest_path": latest.as_posix(),
            "modified_at": _mtime_utc(latest).isoformat(),
            "age_hours": age,
        }
        if age > float(max_age_hours):
            issues.append(
                _issue(
                    "WARNING",
                    "reports",
                    f"latest {pattern} is older than {max_age_hours:g}h: {latest.name}",
                    "Run python main.py ops-dry-run --network --broker kis",
                )
            )

    db_exists = db.is_file()
    checks["db"]["exists"] = db_exists
    checks["db"]["path"] = db.as_posix()
    if not db_exists:
        issues.append(
            _issue(
                "WARNING",
                "db",
                f"DB file not found: {db.as_posix()}",
                "Run python main.py init or python main.py ops-dry-run --output-dir outputs",
            )
        )
    else:
        any_report_data = True
        try:
            uri = f"file:{db.resolve().as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                conn.row_factory = sqlite3.Row
                tables = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('real_account_snapshots', 'real_positions')"
                    ).fetchall()
                }
                checks["db"]["tables"] = sorted(tables)
                snapshot = None
                positions: list[dict[str, Any]] = []
                if "real_account_snapshots" in tables:
                    row = conn.execute(
                        """
                        SELECT snapshot_time, broker, cash, withdrawable_cash,
                               total_market_value, total_equity
                        FROM real_account_snapshots
                        WHERE broker = ?
                        ORDER BY snapshot_time DESC, id DESC
                        LIMIT 1
                        """,
                        ("kis",),
                    ).fetchone()
                    snapshot = dict(row) if row else None
                if snapshot and "real_positions" in tables:
                    rows = conn.execute(
                        """
                        SELECT snapshot_time, broker, symbol, quantity, avg_price, current_price, market_value
                        FROM real_positions
                        WHERE broker = ? AND snapshot_time = ?
                        ORDER BY symbol
                        """,
                        ("kis", snapshot["snapshot_time"]),
                    ).fetchall()
                    positions = [dict(row) for row in rows]
            checks["db"]["latest_snapshot"] = snapshot
            checks["db"]["position_count"] = len(positions)
            checks["db"]["positions_loadable"] = True
            if "real_account_snapshots" not in checks["db"].get("tables", []):
                issues.append(
                    _issue(
                        "WARNING",
                        "db",
                        "real_account_snapshots table is missing",
                        "Run python main.py init and then live-sync-account --network when account refresh is needed.",
                    )
                )
            if "real_positions" not in checks["db"].get("tables", []):
                issues.append(
                    _issue(
                        "WARNING",
                        "db",
                        "real_positions table is missing",
                        "Run python main.py init and then live-sync-account --network when account refresh is needed.",
                    )
                )
            if snapshot is None:
                issues.append(
                    _issue(
                        "WARNING",
                        "db",
                        "latest real_account_snapshots row is missing",
                        "Run python main.py live-sync-account --broker kis --network --output-dir outputs",
                    )
                )
            if root_exists and not any(root.glob("live_account_snapshot_*.json")) and snapshot is not None:
                issues.append(
                    _issue(
                        "WARNING",
                        "outputs",
                        "DB has an account snapshot but outputs has no live_account_snapshot JSON",
                        "Run python main.py live-sync-account --broker kis --network --output-dir outputs",
                    )
                )
        except (sqlite3.Error, OSError, ValueError) as e:
            checks["db"]["error"] = str(e)
            issues.append(_issue("CRITICAL", "db", f"DB health check failed: {e}", "Inspect or restore the SQLite DB."))

    appledouble = [p.as_posix() for p in root.rglob("._*") if p.is_file()] if root_exists else []
    checks["appledouble"] = {"count": len(appledouble), "files": appledouble[:50]}
    if appledouble:
        issues.append(
            _issue(
                "WARNING",
                "outputs",
                f"AppleDouble files found: {len(appledouble)}",
                "Remove AppleDouble files with cleanup-reports --apply --remove-appledouble",
            )
        )

    token_path = root / ".kis_token_cache.json"
    checks["token_cache"] = {"path": token_path.as_posix(), "exists": token_path.is_file()}
    if not token_path.is_file():
        issues.append(
            _issue(
                "INFO",
                "token",
                "KIS token cache is missing",
                "Run python main.py kis-check --network only when a KIS OAuth check is needed.",
            )
        )
    else:
        try:
            raw = json.loads(token_path.read_text(encoding="utf-8"))
            expires = _parse_dt(raw.get("expires_at") if isinstance(raw, dict) else None)
            checks["token_cache"]["expires_at"] = expires.isoformat() if expires else None
            if expires is None:
                issues.append(_issue("WARNING", "token", "KIS token cache has invalid expires_at", "Run kis-check --network if network validation is needed."))
            else:
                seconds_left = (expires - now).total_seconds()
                checks["token_cache"]["seconds_until_expiry"] = seconds_left
                if seconds_left <= 0:
                    issues.append(_issue("WARNING", "token", "KIS token cache is expired", "Run python main.py kis-check --network before KIS query operations."))
                elif seconds_left <= token_expiry_warning_minutes * 60:
                    issues.append(_issue("WARNING", "token", f"KIS token cache expires within {token_expiry_warning_minutes} minutes", "Run python main.py kis-check --network before KIS query operations."))
        except (OSError, json.JSONDecodeError) as e:
            issues.append(_issue("WARNING", "token", f"KIS token cache cannot be parsed: {e}", "Run kis-check --network if network validation is needed."))

    ops_json = _latest_match(root, "ops_dashboard_*.json")
    html = root / "OPS_DASHBOARD.html"
    checks["dashboard"] = {
        "ops_dashboard_json": ops_json.as_posix() if ops_json else None,
        "html": html.as_posix(),
        "html_exists": html.is_file(),
    }
    if ops_json is not None and html.is_file():
        checks["dashboard"]["html_mtime"] = _mtime_utc(html).isoformat()
        checks["dashboard"]["ops_json_mtime"] = _mtime_utc(ops_json).isoformat()
        if html.stat().st_mtime + 1.0 < ops_json.stat().st_mtime:
            issues.append(
                _issue(
                    "WARNING",
                    "dashboard",
                    "OPS_DASHBOARD.html is older than latest ops_dashboard JSON",
                    "Run python main.py html-dashboard --output-dir outputs",
                )
            )

    file_count = sum(1 for p in root.rglob("*") if p.is_file()) if root_exists else 0
    checks["outputs"]["file_count"] = file_count
    checks["outputs"]["max_output_files"] = int(max_output_files)
    if file_count > int(max_output_files):
        issues.append(
            _issue(
                "WARNING",
                "outputs",
                f"output_dir has {file_count} files, above limit {max_output_files}",
                "Run python main.py cleanup-reports --output-dir outputs --dry-run",
            )
        )

    checks["no_operational_data"] = not any_report_data
    status = _status_from(issues, checks)
    return ReportHealthResult(
        status=status,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        output_dir=root.as_posix(),
        db_path=db.as_posix(),
        issues=issues,
        checks=checks,
        next_actions=_dedupe_actions(issues),
    )


def write_report_health(result: ReportHealthResult, *, output_dir: str | Path = "outputs") -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    jp = root / f"report_health_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    mp = root / "REPORT_HEALTH.md"
    body = asdict(result)
    body["network_called"] = False
    body["modified_files"] = [jp.as_posix(), mp.as_posix()]
    body["no_cleanup_performed"] = True
    body["actual_order_attempted"] = False
    jp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _HEALTH_STATUS_KO = {
        "HEALTH_OK": "✅ 정상",
        "HEALTH_WARNING": "⚠️ 경고",
        "HEALTH_CRITICAL": "🚨 위험 수준",
    }
    _SEVERITY_KO = {"WARNING": "⚠️ 경고", "CRITICAL": "🚨 위험", "INFO": "ℹ️ 정보"}

    status_ko = _HEALTH_STATUS_KO.get(str(result.status), str(result.status))

    lines = [
        "# DeepSignal — 리포트 시스템 상태",
        "",
        "## 상태",
        "",
        f"- 상태: **{status_ko}**",
        f"- 생성 시각: {result.generated_at}",
        f"- 출력 폴더: `{result.output_dir}`",
        f"- DB 경로: `{result.db_path}`",
        "",
        "## 점검 데이터 (JSON)",
        "",
        "```json",
        json.dumps(result.checks, ensure_ascii=False, indent=2),
        "```",
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
    lines.extend(
        [
            "",
            "## 안전 안내",
            "",
            "- 이 명령은 진단 전용입니다.",
            "- 파일 삭제, 네트워크 호출, 알림 발송, 주문 실행을 하지 않습니다.",
        ]
    )
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp


def format_report_health_console(result: ReportHealthResult, json_path: Path | None = None, md_path: Path | None = None) -> str:
    lines = ["DeepSignal report health check", f"Status: {result.status}", "Issues:"]
    if result.issues:
        for issue in result.issues:
            lines.append(f"- {issue.severity} {issue.category}: {issue.message}")
    else:
        lines.append("- (none)")
    if json_path:
        lines.append(f"JSON: {json_path.as_posix()}")
    if md_path:
        lines.append(f"Markdown: {md_path.as_posix()}")
    lines.append("Note: report-health-check is diagnostic only; no cleanup, network calls, alerts, or orders.")
    return "\n".join(lines)
