"""운영 리포트 archive index 생성 ([실전-21]). 정적 인덱스 전용."""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ReportIndexItem:
    path: str
    name: str
    category: str
    date: str | None
    modified_at: str
    size_bytes: int
    status: str | None
    summary: dict[str, Any]


@dataclass
class ReportIndexResult:
    generated_at: str
    output_dir: str
    archive_dir: str | None
    items: list[ReportIndexItem]
    by_date: dict[str, Any]
    by_category: dict[str, Any]
    warnings: list[str]


TIMESTAMP_RE = re.compile(r"_(\d{8})_(\d{6})")

PREFIX_CATEGORIES = {
    "daily_ops_summary": "daily_summary",
    "ops_dashboard": "ops_dashboard",
    "risk_alert": "risk",
    "sell_plan": "sell_plan",
    "reconcile_live_account": "reconcile",
    "live_account_snapshot": "account",
    "notification_audit": "notification",
    "post_trade_runbook": "post_trade_runbook",
    "pre_trade_runbook": "pre_trade_runbook",
    "live_fill_summary": "fills",
    "report_cleanup_audit": "cleanup",
    "safety_audit": "safety_audit",
    "ai_daily_trade_plan": "ai_daily_trade_plan",
    "ai_daily_trade_report": "ai_daily_trade_report",
    "ai_daily_status": "ai_daily_status",
}

STATIC_CATEGORIES = {
    "OPS_DASHBOARD.html": "html_dashboard",
    "DAILY_OPS_SUMMARY.md": "daily_summary",
    "RISK_ALERT.md": "risk",
    "SELL_PLAN.md": "sell_plan",
    "OPS_DASHBOARD.md": "ops_dashboard",
    "LIVE_ACCOUNT_SNAPSHOT.md": "account",
    "RECONCILE_LIVE_ACCOUNT.md": "reconcile",
    "SAFETY_AUDIT.md": "safety_audit",
    "AI_DAILY_TRADE_PLAN.md": "ai_daily_trade_plan",
    "AI_DAILY_TRADE_REPORT.md": "ai_daily_trade_report",
    "AI_DAILY_STATUS.md": "ai_daily_status",
    "live_order_plan_ai_latest.json": "ai_live_order_plan_latest",
}

SEVERITY_ORDER = {
    "RISK_ALERT": 5,
    "STOP_LOSS_ALERT": 5,
    "TAKE_PROFIT_ALERT": 5,
    "MIXED_ALERT": 5,
    "RECONCILE_MISMATCH": 5,
    "POST_TRADE_RISK_ALERT": 5,
    "POST_TRADE_BLOCKED": 5,
    "PRE_TRADE_BLOCKED": 5,
    "SAFETY_AUDIT_BLOCKED": 5,
    "success=False": 5,
    "WARNING": 3,
    "SAFETY_AUDIT_WARNING": 3,
    "POST_TRADE_WARNING": 3,
    "REVIEW": 3,
    "REDUCE": 3,
    "NO_DATA": 1,
    "OK": 0,
    "SAFETY_AUDIT_OK": 0,
    "HOLD": 0,
    "success=True": 0,
}


def _category_for(path: Path) -> str | None:
    name = path.name
    if name in STATIC_CATEGORIES:
        return STATIC_CATEGORIES[name]
    for prefix, category in PREFIX_CATEGORIES.items():
        if name.startswith(prefix + "_") and name.endswith(".json"):
            return category
    return None


def _date_from_name(name: str) -> str | None:
    m = TIMESTAMP_RE.search(name)
    if not m:
        return None
    token = m.group(1)
    return f"{token[:4]}-{token[4:6]}-{token[6:8]}"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json_summary(path: Path, warnings: list[str]) -> tuple[str | None, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        warnings.append(f"Failed to parse {path.name}: {e}")
        return None, {"parse_error": True}
    if not isinstance(data, dict):
        warnings.append(f"Non-object JSON skipped for summary: {path.name}")
        return None, {"non_object_json": True}

    status = _extract_status(data)
    summary: dict[str, Any] = {}
    for key in ("status", "final_status", "success", "mode", "date", "generated_at"):
        if key in data:
            summary[key] = data.get(key)
    nested_summary = data.get("summary")
    if isinstance(nested_summary, dict):
        for key in ("status", "risk_status", "reconcile_success"):
            if key in nested_summary:
                summary[f"summary_{key}"] = nested_summary.get(key)
    for key in ("warnings", "alerts", "items", "messages", "results", "steps"):
        value = data.get(key)
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)
    return status, summary


def _extract_status(data: dict[str, Any]) -> str | None:
    for key in ("status", "final_status"):
        if data.get(key) is not None:
            return str(data.get(key))
    if data.get("success") is not None:
        return f"success={data.get('success')}"
    summary = data.get("summary")
    if isinstance(summary, dict) and summary.get("status") is not None:
        return str(summary.get("status"))
    risk = data.get("risk")
    if isinstance(risk, dict):
        risk_status = risk.get("status") or risk.get("risk_status")
        if risk_status is not None:
            return str(risk_status)
    reconcile = data.get("reconcile")
    if isinstance(reconcile, dict) and reconcile.get("success") is not None:
        return f"reconcile.success={reconcile.get('success')}"
    if data.get("mode") is not None:
        return str(data.get("mode"))
    return None


def _severity(status: str | None) -> int:
    if status is None:
        return -1
    s = str(status)
    if s.startswith("reconcile.success=False"):
        return 5
    if s.startswith("reconcile.success=True"):
        return 0
    return SEVERITY_ORDER.get(s, 2)


def _highest_status(statuses: list[str | None]) -> str | None:
    filtered = [s for s in statuses if s]
    if not filtered:
        return None
    return sorted(filtered, key=_severity, reverse=True)[0]


def _scan_paths(root: Path, archive_dir: Path | None) -> list[Path]:
    paths: list[Path] = []
    if root.exists():
        for p in root.iterdir():
            if p.is_file() and _category_for(p):
                paths.append(p)
    if archive_dir and archive_dir.exists():
        for p in archive_dir.rglob("*"):
            if p.is_file() and _category_for(p):
                paths.append(p)
    return paths


def build_report_index(
    *,
    output_dir: str | Path = "outputs",
    archive_dir: str | Path | None = None,
    max_items: int = 200,
) -> ReportIndexResult:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive_root = Path(archive_dir).expanduser().resolve() if archive_dir else None
    warnings: list[str] = []
    items: list[ReportIndexItem] = []
    for path in _scan_paths(root, archive_root):
        category = _category_for(path)
        if not category:
            continue
        st = path.stat()
        status: str | None = None
        summary: dict[str, Any] = {}
        if path.suffix.lower() == ".json":
            status, summary = _read_json_summary(path, warnings)
        item = ReportIndexItem(
            path=_rel(path, root),
            name=path.name,
            category=category,
            date=_date_from_name(path.name),
            modified_at=datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            size_bytes=int(st.st_size),
            status=status,
            summary=summary,
        )
        items.append(item)
    items.sort(key=lambda x: (x.modified_at, x.name), reverse=True)
    if max_items > 0:
        items = items[: int(max_items)]
    by_date = _group_by_date(items)
    by_category = _group_by_category(items)
    return ReportIndexResult(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        output_dir=root.as_posix(),
        archive_dir=archive_root.as_posix() if archive_root else None,
        items=items,
        by_date=by_date,
        by_category=by_category,
        warnings=warnings,
    )


def _group_by_date(items: list[ReportIndexItem]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        key = item.date or "unknown"
        bucket = out.setdefault(key, {"count": 0, "categories": {}, "highest_severity": None, "links": []})
        bucket["count"] += 1
        bucket["categories"][item.category] = int(bucket["categories"].get(item.category, 0)) + 1
        bucket["links"].append(item.path)
        bucket["highest_severity"] = _highest_status([bucket.get("highest_severity"), item.status])
    return dict(sorted(out.items(), reverse=True))


def _group_by_category(items: list[ReportIndexItem]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items:
        bucket = out.setdefault(item.category, {"count": 0, "latest": None, "status": None})
        bucket["count"] += 1
        if bucket["latest"] is None or str(item.modified_at) > str(bucket["latest"]):
            bucket["latest"] = item.modified_at
            bucket["status"] = item.status
    return dict(sorted(out.items()))


def _e(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return html.escape(str(value))


def _size(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    return f"{value / 1024:.1f} KB"


def _summary_counts(result: ReportIndexResult) -> dict[str, Any]:
    dates = [item.date for item in result.items if item.date]
    risk_alerts = sum(1 for item in result.items if _severity(item.status) >= 5 and item.category == "risk")
    reconcile_mismatch = sum(
        1
        for item in result.items
        if item.category == "reconcile" and str(item.status or "").endswith("False")
    )
    return {
        "total": len(result.items),
        "latest_date": max(dates) if dates else "-",
        "risk_alerts": risk_alerts,
        "reconcile_mismatch": reconcile_mismatch,
    }


def _link(path: str | None) -> str:
    if not path:
        return "-"
    return f'<a href="{_e(path)}">{_e(Path(path).name)}</a>'


def _daily_freshness_label(daily: Any, key: str) -> str:
    labels = daily.freshness.get("labels") if isinstance(daily.freshness, dict) else {}
    sources = daily.freshness.get("sources") if isinstance(daily.freshness, dict) else {}
    if isinstance(labels, dict) and key in labels:
        label = str(labels[key])
        if isinstance(sources, dict) and key in sources:
            return f"{label} ({sources[key]})"
        return label
    entry = daily.freshness.get(key) if isinstance(daily.freshness, dict) else None
    if isinstance(entry, dict) and entry.get("status"):
        from deepsignal.live_trading.daily_ai_freshness import freshness_label_ko, freshness_source_label_ko

        label = freshness_label_ko(str(entry["status"]))
        source = freshness_source_label_ko(str(entry.get("freshness_source") or ""))
        return f"{label} ({source})" if source else label
    return "없음"


def _daily_freshness_generated_at(daily: Any, key: str) -> str:
    entry = daily.freshness.get(key) if isinstance(daily.freshness, dict) else None
    if isinstance(entry, dict) and entry.get("generated_at"):
        return str(entry["generated_at"])
    return "-"


def _daily_ai_section_html(daily: Any) -> str:
    files = daily.files
    return (
        "<section><h2>AI 일일 매매 운영</h2>"
        "<ul>"
        f"<li>오늘 계획: {_e(_daily_freshness_label(daily, 'plan'))} · 생성 시각: {_e(_daily_freshness_generated_at(daily, 'plan'))}</li>"
        f"<li>최신 주문안: {_e(_daily_freshness_label(daily, 'latest_order_plan'))} · 생성 시각: {_e(_daily_freshness_generated_at(daily, 'latest_order_plan'))}</li>"
        f"<li>일일 리포트: {_e(_daily_freshness_label(daily, 'report'))} · 생성 시각: {_e(_daily_freshness_generated_at(daily, 'report'))}</li>"
        f"<li>계획 생성: {_e('완료' if daily.checks.get('plan_json_exists') else '없음')}</li>"
        f"<li>Telegram 승인 요청: {_e('완료' if daily.checks.get('approval_request_exists') else '없음')}</li>"
        f"<li>승인 상태: {_e(daily.approval_status)}</li>"
        f"<li>execute-last-approved 실행: {_e('완료' if daily.checks.get('execute_approved_exists') else '없음')}</li>"
        f"<li>장 종료 리포트: {_e('완료' if daily.checks.get('report_json_exists') else '없음')}</li>"
        f"<li>최신 상태: {_e(daily.status_report_status)}</li>"
        f"<li>다음 실행 권장 명령: <code>{_e(daily.next_action)}</code></li>"
        "</ul>"
        "<p>"
        f"Plan: {_link(files.get('AI_DAILY_TRADE_PLAN.md'))} · "
        f"Plan JSON: {_link(files.get('ai_daily_trade_plan_latest_json'))} · "
        f"Latest Order Plan: {_link(files.get('live_order_plan_ai_latest.json'))} · "
        f"Report: {_link(files.get('AI_DAILY_TRADE_REPORT.md'))} · "
        f"Report JSON: {_link(files.get('ai_daily_trade_report_latest_json'))} · "
        f"Status: {_link(files.get('AI_DAILY_STATUS.md'))} · "
        f"Status JSON: {_link(files.get('ai_daily_status_latest_json'))}"
        "</p></section>"
    )


def _md_link(path: str | None) -> str:
    if not path:
        return "-"
    return f"[{Path(path).name}]({path})"


def render_report_index_html(result: ReportIndexResult) -> str:
    counts = _summary_counts(result)
    from deepsignal.live_trading.archive_viewer import load_archive_viewer_link_info
    from deepsignal.live_trading.daily_ai_status_reader import read_daily_ai_workflow_status
    from deepsignal.live_trading.operator_labels import label_freshness_source, label_report_type, label_status
    from deepsignal.live_trading.safety_audit import load_safety_audit_link_info

    archive = load_archive_viewer_link_info(result.output_dir)
    safety = load_safety_audit_link_info(result.output_dir)
    daily = read_daily_ai_workflow_status(result.output_dir)
    archive_html = f'<a href="{_e(archive.html_rel)}">{_e(Path(archive.html_rel).name)}</a>' if archive.html_rel else "-"
    archive_csv = f'<a href="{_e(archive.csv_rel)}">{_e(Path(archive.csv_rel).name)}</a>' if archive.csv_rel else "-"
    archive_summary = f'<a href="{_e(archive.summary_md_rel)}">{_e(Path(archive.summary_md_rel).name)}</a>' if archive.summary_md_rel else "-"
    archive_presets = f'<a href="{_e(archive.presets_rel)}">{_e(Path(archive.presets_rel).name)}</a>' if archive.presets_rel else "-"
    archive_json = f'<a href="{_e(archive.json_rel)}">{_e(Path(archive.json_rel).name)}</a>' if archive.json_rel else "-"
    safety_md = f'<a href="{_e(safety.markdown_rel)}">{_e(Path(safety.markdown_rel).name)}</a>' if safety.markdown_rel else "-"
    safety_json = f'<a href="{_e(safety.json_rel)}">{_e(Path(safety.json_rel).name)}</a>' if safety.json_rel else "-"
    date_rows = []
    for date, bucket in result.by_date.items():
        links = " ".join(f'<a href="{_e(p)}">{_e(Path(p).name)}</a>' for p in bucket.get("links", [])[:8])
        date_rows.append(
            f"<tr><td>{_e(date)}</td><td>{_e(bucket.get('count'))}</td>"
            f"<td>{_e(bucket.get('highest_severity'))}</td><td>{links}</td></tr>"
        )
    cat_rows = []
    for cat, bucket in result.by_category.items():
        cat_rows.append(
            f"<tr><td>{_e(label_report_type(cat))}</td><td>{_e(bucket.get('count'))}</td>"
            f"<td>{_e(bucket.get('latest'))}</td><td>{_e(label_status(bucket.get('status')))}</td></tr>"
        )
    recent_rows = []
    for item in result.items[:50]:
        recent_rows.append(
            f'<tr><td><a href="{_e(item.path)}">{_e(item.name)}</a></td><td>{_e(label_report_type(item.category))}</td>'
            f"<td>{_e(item.date)}</td><td>{_e(label_status(item.status))}</td><td>{_e(_size(item.size_bytes))}</td></tr>"
        )
    css = """
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f6f7f9; color: #20242a; }
    header { padding: 24px; background: #111827; color: white; }
    main { padding: 24px; max-width: 1200px; margin: 0 auto; }
    section { background: white; border-radius: 12px; padding: 18px; margin: 16px 0; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 9px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
    th { background: #f3f4f6; }
    a { color: #2563eb; text-decoration: none; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
    .card { background: white; border-radius: 12px; padding: 16px; border-left: 6px solid #6b7280; }
    .label { color: #6b7280; font-size: 12px; text-transform: uppercase; }
    .value { font-size: 22px; font-weight: 700; }
    """
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>DeepSignal Report Index</title><style>{css}</style></head><body>"
        "<header><h1>DeepSignal Report Index</h1><p>Static local report index. No web server, network call, or order execution.</p></header><main>"
        "<section><h2>Summary</h2><div class=\"cards\">"
        f"<div class=\"card\"><div class=\"label\">Total reports</div><div class=\"value\">{_e(counts['total'])}</div></div>"
        f"<div class=\"card\"><div class=\"label\">Latest report date</div><div class=\"value\">{_e(counts['latest_date'])}</div></div>"
        f"<div class=\"card\"><div class=\"label\">Risk alerts</div><div class=\"value\">{_e(counts['risk_alerts'])}</div></div>"
        f"<div class=\"card\"><div class=\"label\">Reconcile mismatch</div><div class=\"value\">{_e(counts['reconcile_mismatch'])}</div></div>"
        f"<div class=\"card\"><div class=\"label\">안전 점검</div><div class=\"value\">{_e(label_status(safety.status))}</div></div>"
        f"<div class=\"card\"><div class=\"label\">리포트 보관함</div><div class=\"value\">{_e(label_status(archive.status))}</div></div>"
        f"<div class=\"card\"><div class=\"label\">AI 일일 상태</div><div class=\"value\">{_e(label_status(daily.status_report_status))}</div></div>"
        "</div></section>"
        + _daily_ai_section_html(daily) +
        "<section><h2>리포트 보관함</h2>"
        f"<p>{_e(archive.message)}</p>"
        "<ul>"
        f"<li>상태: <strong>{_e(label_status(archive.status))}</strong> ({_e(archive.status)})</li>"
        f"<li>HTML Viewer: {archive_html}</li>"
        f"<li>CSV Export: {archive_csv}</li>"
        f"<li>Markdown Summary: {archive_summary}</li>"
        f"<li>Filter Presets: {archive_presets}</li>"
        f"<li>Latest JSON: {archive_json}</li>"
        f"<li>Total reports: {_e(archive.total_reports)}</li>"
        f"<li>Updated At: {_e(archive.updated_at)}</li>"
        + (
            "".join(
                f"<li>{_e(label_freshness_source(key))}: {_e(archive.freshness_source_summary.get(key, 0))}</li>"
                for key in ("generated_at", "markdown_header", "mtime_fallback", "unknown")
            )
            if isinstance(archive.freshness_source_summary, dict)
            else ""
        )
        + "</ul></section>"
        "<section><h2>안전 점검</h2>"
        f"<p>{_e(safety.message)}</p>"
        "<ul>"
        f"<li>상태: <strong>{_e(label_status(safety.status))}</strong> ({_e(safety.status)})</li>"
        f"<li>Markdown: {safety_md}</li>"
        f"<li>Latest JSON: {safety_json}</li>"
        f"<li>Updated At: {_e(safety.updated_at)}</li>"
        f"<li>Warnings: {_e(safety.warning_count)}</li>"
        f"<li>Blocked: {_e(safety.blocked_count)}</li>"
        "</ul></section>"
        "<section><h2>By Date</h2><table><thead><tr><th>Date</th><th>Reports</th><th>Highest Severity</th><th>Links</th></tr></thead><tbody>"
        + "".join(date_rows)
        + "</tbody></table></section>"
        "<section><h2>By Category</h2><table><thead><tr><th>Category</th><th>Count</th><th>Latest</th><th>Status</th></tr></thead><tbody>"
        + "".join(cat_rows)
        + "</tbody></table></section>"
        "<section><h2>Recent Reports</h2><table><thead><tr><th>Name</th><th>Category</th><th>Date</th><th>Status</th><th>Size</th></tr></thead><tbody>"
        + "".join(recent_rows)
        + "</tbody></table></section>"
        "</main></body></html>"
    )


def render_report_index_markdown(result: ReportIndexResult) -> str:
    counts = _summary_counts(result)
    from deepsignal.live_trading.archive_viewer import load_archive_viewer_link_info
    from deepsignal.live_trading.daily_ai_status_reader import read_daily_ai_workflow_status
    from deepsignal.live_trading.operator_labels import label_freshness_source, label_report_type, label_status
    from deepsignal.live_trading.safety_audit import load_safety_audit_link_info

    archive = load_archive_viewer_link_info(result.output_dir)
    safety = load_safety_audit_link_info(result.output_dir)
    daily = read_daily_ai_workflow_status(result.output_dir)
    lines = [
        "# DeepSignal Report Index",
        "",
        "## Summary",
        "",
        f"- Total reports: {counts['total']}",
        f"- Latest report date: {counts['latest_date']}",
        f"- Risk alerts count: {counts['risk_alerts']}",
        f"- Reconcile mismatch count: {counts['reconcile_mismatch']}",
        f"- Safety audit status: {label_status(safety.status)} ({safety.status})",
        f"- Archive viewer status: {label_status(archive.status)} ({archive.status})",
        f"- AI daily workflow status: {label_status(daily.status_report_status)} ({daily.status_report_status})",
        f"- Generated at: {result.generated_at}",
        "- Mode: static local index; no web server, network call, or orders.",
        "",
        "## 리포트 보관함",
        "",
        f"- Status: {label_status(archive.status)} ({archive.status})",
        f"- HTML Viewer: [{Path(archive.html_rel).name}]({archive.html_rel})" if archive.html_rel else "- HTML Viewer: -",
        f"- CSV Export: [{Path(archive.csv_rel).name}]({archive.csv_rel})" if archive.csv_rel else "- CSV Export: -",
        f"- Markdown Summary: [{Path(archive.summary_md_rel).name}]({archive.summary_md_rel})" if archive.summary_md_rel else "- Markdown Summary: -",
        f"- Filter Presets: [{Path(archive.presets_rel).name}]({archive.presets_rel})" if archive.presets_rel else "- Filter Presets: -",
        f"- Latest JSON: [{Path(archive.json_rel).name}]({archive.json_rel})" if archive.json_rel else "- Latest JSON: -",
        f"- Total reports: {archive.total_reports if archive.total_reports is not None else '-'}",
        f"- Updated At: {archive.updated_at or '-'}",
        *(
            [
                f"- {label_freshness_source(key)}: {archive.freshness_source_summary.get(key, 0)}"
                for key in ("generated_at", "markdown_header", "mtime_fallback", "unknown")
            ]
            if isinstance(archive.freshness_source_summary, dict)
            else []
        ),
        f"- Note: {archive.message}",
        "",
        "## 안전 점검",
        "",
        f"- Status: {label_status(safety.status)} ({safety.status})",
        f"- Markdown: [{Path(safety.markdown_rel).name}]({safety.markdown_rel})" if safety.markdown_rel else "- Markdown: -",
        f"- Latest JSON: [{Path(safety.json_rel).name}]({safety.json_rel})" if safety.json_rel else "- Latest JSON: -",
        f"- Updated At: {safety.updated_at or '-'}",
        f"- Warnings: {safety.warning_count}",
        f"- Blocked: {safety.blocked_count}",
        f"- Note: {safety.message}",
        "",
        "## AI 일일 매매 운영",
        "",
        f"- 오늘 계획: {_daily_freshness_label(daily, 'plan')} · 생성 시각: {_daily_freshness_generated_at(daily, 'plan')}",
        f"- 최신 주문안: {_daily_freshness_label(daily, 'latest_order_plan')} · 생성 시각: {_daily_freshness_generated_at(daily, 'latest_order_plan')}",
        f"- 일일 리포트: {_daily_freshness_label(daily, 'report')} · 생성 시각: {_daily_freshness_generated_at(daily, 'report')}",
        f"- 계획 생성: {'완료' if daily.checks.get('plan_json_exists') else '없음'}",
        f"- Telegram 승인 요청: {'완료' if daily.checks.get('approval_request_exists') else '없음'}",
        f"- 승인 상태: {daily.approval_status}",
        f"- execute-last-approved 실행: {'완료' if daily.checks.get('execute_approved_exists') else '없음'}",
        f"- 장 종료 리포트: {'완료' if daily.checks.get('report_json_exists') else '없음'}",
        f"- 최신 상태: {daily.status_report_status}",
        f"- 다음 실행 권장 명령: `{daily.next_action}`",
        f"- AI Daily Trade Plan: {_md_link(daily.files.get('AI_DAILY_TRADE_PLAN.md'))}",
        f"- AI Daily Trade Plan JSON: {_md_link(daily.files.get('ai_daily_trade_plan_latest_json'))}",
        f"- Latest AI Order Plan: {_md_link(daily.files.get('live_order_plan_ai_latest.json'))}",
        f"- AI Daily Trade Report: {_md_link(daily.files.get('AI_DAILY_TRADE_REPORT.md'))}",
        f"- AI Daily Trade Report JSON: {_md_link(daily.files.get('ai_daily_trade_report_latest_json'))}",
        f"- AI Daily Status: {_md_link(daily.files.get('AI_DAILY_STATUS.md'))}",
        f"- AI Daily Status JSON: {_md_link(daily.files.get('ai_daily_status_latest_json'))}",
        "",
        "## By Date",
        "",
        "| Date | Reports | Highest Severity |",
        "|------|---------|------------------|",
    ]
    for date, bucket in result.by_date.items():
        lines.append(f"| {date} | {bucket.get('count')} | {bucket.get('highest_severity') or '-'} |")
    lines.extend(["", "## By Category", "", "| Category | Count | Latest | Status |", "|----------|-------|--------|--------|"])
    for cat, bucket in result.by_category.items():
        lines.append(f"| {label_report_type(cat)} | {bucket.get('count')} | {bucket.get('latest') or '-'} | {label_status(bucket.get('status'))} |")
    lines.extend(["", "## Recent Reports", "", "| Name | Category | Date | Status | Size |", "|------|----------|------|--------|------|"])
    for item in result.items[:50]:
        lines.append(f"| [{item.name}]({item.path}) | {label_report_type(item.category)} | {item.date or '-'} | {label_status(item.status)} | {_size(item.size_bytes)} |")
    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines) + "\n"


def write_report_index(result: ReportIndexResult, *, output_dir: str | Path = "outputs") -> tuple[Path, Path, Path]:
    from deepsignal.live_trading.daily_ai_status_reader import read_daily_ai_workflow_status

    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    json_path = root / f"report_index_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    html_path = root / "REPORT_INDEX.html"
    md_path = root / "REPORT_INDEX.md"
    body = asdict(result)
    body["actual_order_attempted"] = False
    body["network_called"] = False
    body["실제_주문_없음"] = True
    body["daily_ai_workflow"] = read_daily_ai_workflow_status(root).to_dict()
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_report_index_html(result), encoding="utf-8")
    md_path.write_text(render_report_index_markdown(result), encoding="utf-8")
    return html_path, md_path, json_path


def run_report_index(
    *,
    output_dir: str | Path = "outputs",
    archive_dir: str | Path | None = None,
    max_items: int = 200,
) -> tuple[ReportIndexResult, Path, Path, Path]:
    result = build_report_index(output_dir=output_dir, archive_dir=archive_dir, max_items=max_items)
    html_path, md_path, json_path = write_report_index(result, output_dir=output_dir)
    return result, html_path, md_path, json_path
