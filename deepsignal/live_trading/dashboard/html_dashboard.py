"""정적 HTML 운영 대시보드 ([실전-18]). 로컬 파일 생성 전용."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from deepsignal.live_trading.operator_labels import label_status


@dataclass
class HtmlDashboardResult:
    status: str
    generated_at: str
    html_path: str
    sources: dict[str, dict[str, Any]]
    warnings: list[str]


def _read_latest(output_dir: str | Path, pattern: str) -> tuple[dict[str, Any], str | None]:
    paths = sorted(Path(output_dir).glob(pattern))
    if not paths:
        return {}, f"No data for {pattern}"
    path = paths[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"_parse_error": True, "_path": path.as_posix()}, f"Failed to parse {path.name}"
    if isinstance(data, dict):
        data["_path"] = path.as_posix()
        return data, None
    return {"_non_object_json": True, "_path": path.as_posix()}, f"Non-object JSON in {path.name}"


def load_html_dashboard_sources(output_dir: str | Path = "outputs") -> tuple[dict[str, dict[str, Any]], list[str]]:
    specs = {
        "daily": "daily_ops_summary_*.json",
        "ops": "ops_dashboard_*.json",
        "risk": "risk_alert_*.json",
        "sell": "sell_plan_*.json",
        "reconcile": "reconcile_live_account_*.json",
        "account": "live_account_snapshot_*.json",
        "fills": "live_fill_summary_*.json",
        "notification": "notification_audit_*.json",
        "safety": "safety_audit_*.json",
    }
    sources: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for key, pattern in specs.items():
        data, warning = _read_latest(output_dir, pattern)
        sources[key] = data
        if warning and key != "safety":
            warnings.append(warning)
    return sources, warnings


def _e(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return html.escape(f"{value:.2f}")
    return html.escape(str(value))


def _pct(value: Any) -> str:
    try:
        return html.escape(f"{float(value) * 100:.2f}%")
    except (TypeError, ValueError):
        return "-"


def _risk_status(risk: dict[str, Any]) -> str:
    return str(risk.get("status") or risk.get("risk_status") or "")


def _overall_status(sources: dict[str, dict[str, Any]]) -> str:
    daily = sources.get("daily") or {}
    if daily.get("status"):
        return str(daily.get("status"))
    ops = sources.get("ops") or {}
    if ops.get("status"):
        return str(ops.get("status"))
    risk = _risk_status(sources.get("risk") or {})
    if risk:
        return risk
    if any(sources.values()):
        return "NO_DATA"
    return "NO_DATA"


def _status_class(status: Any) -> str:
    s = str(status or "NO_DATA").upper()
    if s in {"OK", "HOLD", "SAFETY_AUDIT_OK"}:
        return "status-ok"
    if s in {"WARNING", "REVIEW", "REDUCE", "SAFETY_AUDIT_WARNING"}:
        return "status-warning"
    if s in {"RISK_ALERT", "RECONCILE_MISMATCH", "STOP_LOSS_ALERT", "TAKE_PROFIT_ALERT", "MIXED_ALERT", "EXIT", "SAFETY_AUDIT_BLOCKED"}:
        return "status-danger"
    return "status-nodata"


def _risk_by_symbol(risk: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in risk.get("positions") or []:
        if isinstance(row, dict) and row.get("symbol"):
            out[str(row.get("symbol"))] = row
    return out


def _positions(sources: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ops_positions = (sources.get("ops") or {}).get("positions")
    if isinstance(ops_positions, list) and ops_positions:
        return [p for p in ops_positions if isinstance(p, dict)]
    account_positions = (sources.get("account") or {}).get("positions")
    if isinstance(account_positions, list):
        return [p for p in account_positions if isinstance(p, dict)]
    return []


def _card(title: str, value: Any) -> str:
    status_cls = _status_class(value)
    return f'<div class="card {status_cls}"><div class="card-title">{_e(title)}</div><div class="card-value">{_e(value)}</div></div>'


def _section(title: str, body: str) -> str:
    return f"<section><h2>{_e(title)}</h2>{body}</section>"


def _no_data() -> str:
    return '<p class="muted">No data</p>'


def render_html_dashboard(sources: dict[str, dict[str, Any]], *, warnings: list[str] | None = None) -> str:
    generated_at = datetime.now().isoformat(timespec="seconds")
    daily = sources.get("daily") or {}
    account = sources.get("account") or {}
    reconcile = sources.get("reconcile") or {}
    risk = sources.get("risk") or {}
    ops = sources.get("ops") or {}
    sell = sources.get("sell") or {}
    fills = sources.get("fills") or {}
    notification = sources.get("notification") or {}
    safety = sources.get("safety") or {}
    overall = _overall_status(sources)
    risk_status = _risk_status(risk) or ops.get("risk", {}).get("status") or "NO_DATA"
    rec_status = "success=True" if reconcile.get("success") is True else ("success=False" if reconcile.get("success") is False else "NO_DATA")
    sell_status = sell.get("status") or "NO_DATA"
    safety_status = safety.get("status") or "NOT_AVAILABLE"
    last_updated = daily.get("generated_at") or ops.get("generated_at") or generated_at

    cash_obj = account.get("cash") if isinstance(account.get("cash"), dict) else {}
    account_body = "".join(
        [
            "<dl>",
            f"<dt>Cash</dt><dd>{_e(cash_obj.get('cash') if cash_obj else account.get('cash'))}</dd>",
            f"<dt>Withdrawable cash</dt><dd>{_e(cash_obj.get('withdrawable_cash') if cash_obj else account.get('withdrawable_cash'))}</dd>",
            f"<dt>Total equity</dt><dd>{_e(account.get('total_equity'))}</dd>",
            f"<dt>Total market value</dt><dd>{_e(account.get('total_market_value'))}</dd>",
            f"<dt>Source</dt><dd>{_e(account.get('_path'))}</dd>",
            "</dl>",
        ]
    )

    risk_map = _risk_by_symbol(risk)
    pos_rows = []
    for p in _positions(sources):
        sym = str(p.get("symbol") or "")
        rr = risk_map.get(sym, {})
        pos_rows.append(
            "<tr>"
            f"<td>{_e(sym)}</td>"
            f"<td>{_e(p.get('quantity'))}</td>"
            f"<td>{_e(p.get('avg_price'))}</td>"
            f"<td>{_e(p.get('current_price'))}</td>"
            f"<td>{_e(p.get('market_value'))}</td>"
            f"<td>{_e(rr.get('unrealized_pnl'))}</td>"
            f"<td>{_pct(rr.get('unrealized_pnl_pct'))}</td>"
            f'<td><span class="badge {_status_class(rr.get("risk_level"))}">{_e(rr.get("risk_level"))}</span></td>'
            "</tr>"
        )
    positions_body = (
        '<table><thead><tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>Current</th><th>Market Value</th><th>PnL</th><th>PnL %</th><th>Risk</th></tr></thead><tbody>'
        + "".join(pos_rows)
        + "</tbody></table>"
        if pos_rows
        else _no_data()
    )

    reconcile_body = (
        "<dl>"
        f"<dt>Matched</dt><dd>{len(reconcile.get('matched') or [])}</dd>"
        f"<dt>Missing in DB</dt><dd>{len(reconcile.get('missing_in_db') or [])}</dd>"
        f"<dt>Missing in broker</dt><dd>{len(reconcile.get('missing_in_broker') or [])}</dd>"
        f"<dt>Quantity mismatch</dt><dd>{len(reconcile.get('quantity_mismatch') or [])}</dd>"
        f"<dt>Source</dt><dd>{_e(reconcile.get('_path'))}</dd>"
        "</dl>"
        if reconcile
        else _no_data()
    )

    risk_alerts = risk.get("alerts") or []
    risk_body = (
        f'<p>Status: <span class="badge {_status_class(risk_status)}">{_e(risk_status)}</span></p>'
        + ("<ul>" + "".join(f"<li>{_e(a)}</li>" for a in risk_alerts) + "</ul>" if risk_alerts else _no_data())
    )

    sell_items = sell.get("items") or []
    sell_body = (
        "<ul>"
        + "".join(
            f"<li>{_e(i.get('symbol'))}: {_e(i.get('suggested_action'))} suggested_sell_qty={_e(i.get('suggested_sell_quantity'))}</li>"
            for i in sell_items
            if isinstance(i, dict)
        )
        + "</ul>"
        if sell_items
        else _no_data()
    )

    recent_orders = (ops.get("recent_orders") or [])[:10]
    fill_summaries = fills.get("summaries") or []
    orders_body = ""
    if recent_orders:
        orders_body += "<h3>Recent Orders</h3><ul>" + "".join(
            f"<li>{_e(o.get('created_at'))} {_e(o.get('symbol'))} {_e(o.get('side'))} qty={_e(o.get('quantity'))} status={_e(o.get('status'))}</li>"
            for o in recent_orders
            if isinstance(o, dict)
        ) + "</ul>"
    if fill_summaries:
        orders_body += "<h3>Fills</h3><ul>" + "".join(
            f"<li>order {_e(f.get('order_id'))} {_e(f.get('symbol'))} filled={_e(f.get('filled_quantity'))} remaining={_e(f.get('remaining_quantity'))}</li>"
            for f in fill_summaries[:10]
            if isinstance(f, dict)
        ) + "</ul>"
    if not orders_body:
        orders_body = _no_data()

    notification_body = (
        "<dl>"
        f"<dt>Dry run</dt><dd>{_e(notification.get('dry_run'))}</dd>"
        f"<dt>Channel</dt><dd>{_e(notification.get('channel'))}</dd>"
        f"<dt>Messages</dt><dd>{len(notification.get('messages') or [])}</dd>"
        f"<dt>Results</dt><dd>{len(notification.get('results') or [])}</dd>"
        f"<dt>Source</dt><dd>{_e(notification.get('_path'))}</dd>"
        "</dl>"
        if notification
        else _no_data()
    )

    safety_path = Path(str(safety.get("_path") or "")) if safety.get("_path") else None
    safety_json_name = safety_path.name if safety_path else ""
    safety_md = Path("SAFETY_AUDIT.md")
    issues = safety.get("issues") if isinstance(safety.get("issues"), list) else []
    warning_count = sum(1 for issue in issues if isinstance(issue, dict) and str(issue.get("severity") or "").upper() == "WARNING")
    blocked_count = sum(1 for issue in issues if isinstance(issue, dict) and str(issue.get("severity") or "").upper() == "BLOCKED")
    safety_body = (
        "<dl>"
        f"<dt>안전 점검 상태</dt><dd><span class=\"badge {_status_class(safety_status)}\">{_e(label_status(safety_status))}</span> ({_e(safety_status)})</dd>"
        f"<dt>최근 점검 시간</dt><dd>{_e(safety.get('generated_at'))}</dd>"
        f"<dt>Markdown 리포트</dt><dd><a href=\"SAFETY_AUDIT.md\">SAFETY_AUDIT.md</a></dd>"
        f"<dt>JSON 리포트</dt><dd><a href=\"{_e(safety_json_name)}\">{_e(safety_json_name)}</a></dd>"
        f"<dt>경고</dt><dd>{_e(warning_count)}</dd>"
        f"<dt>차단</dt><dd>{_e(blocked_count)}</dd>"
        "</dl>"
        if safety
        else '<p class="muted">안전 점검 리포트가 아직 생성되지 않았습니다.</p>'
    )

    next_actions = daily.get("next_actions") or []
    next_body = "<ul>" + "".join(f"<li>{_e(a)}</li>" for a in next_actions) + "</ul>" if next_actions else _no_data()
    all_warnings = list(warnings or []) + list(daily.get("warnings") or [])
    warnings_body = "<ul>" + "".join(f"<li>{_e(w)}</li>" for w in all_warnings) + "</ul>" if all_warnings else '<p class="muted">(none)</p>'

    css = """
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f6f7f9; color: #20242a; }
    header { padding: 24px; background: #111827; color: white; }
    main { padding: 24px; max-width: 1200px; margin: 0 auto; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 18px 0; }
    .card { border-radius: 12px; padding: 16px; background: white; border-left: 8px solid #9ca3af; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
    .card-title { color: #4b5563; font-size: 13px; text-transform: uppercase; letter-spacing: .06em; }
    .card-value { margin-top: 8px; font-size: 20px; font-weight: 700; }
    section { background: white; border-radius: 12px; padding: 18px; margin: 16px 0; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid #e5e7eb; }
    th { background: #f3f4f6; }
    dl { display: grid; grid-template-columns: 180px 1fr; gap: 8px 14px; }
    dt { font-weight: 700; color: #374151; }
    .badge { border-radius: 999px; padding: 3px 9px; font-weight: 700; display: inline-block; }
    .status-ok { border-color: #16a34a; background: #dcfce7; color: #166534; }
    .status-warning { border-color: #f59e0b; background: #fef3c7; color: #92400e; }
    .status-danger { border-color: #dc2626; background: #fee2e2; color: #991b1b; }
    .status-nodata { border-color: #9ca3af; background: #f3f4f6; color: #374151; }
    .muted { color: #6b7280; }
    """
    body = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>DeepSignal Operations Dashboard</title><style>{css}</style></head><body>"
        "<header><h1>DeepSignal Operations Dashboard</h1><p>Static local HTML. No web server, network call, or order execution.</p></header><main>"
        '<div class="cards">'
        + _card("Overall Status", overall)
        + _card("Risk Status", risk_status)
        + _card("Reconcile Status", rec_status)
        + _card("Sell Plan Status", sell_status)
        + _card("안전 점검", label_status(safety_status))
        + _card("Last Updated", last_updated)
        + "</div>"
        + _section("Account", account_body)
        + _section("Positions", positions_body)
        + _section("Reconcile", reconcile_body)
        + _section("Risk Alerts", risk_body)
        + _section("Sell Plan", sell_body)
        + _section("Recent Orders / Fills", orders_body)
        + _section("Notifications", notification_body)
        + _section("안전 점검", safety_body)
        + _section("Next Actions", next_body)
        + _section("Warnings", warnings_body)
        + "</main></body></html>"
    )
    return body


def write_html_dashboard(
    *,
    output_dir: str | Path = "outputs",
) -> HtmlDashboardResult:
    sources, warnings = load_html_dashboard_sources(output_dir)
    html_text = render_html_dashboard(sources, warnings=warnings)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "OPS_DASHBOARD.html"
    path.write_text(html_text, encoding="utf-8")
    return HtmlDashboardResult(
        status=_overall_status(sources),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        html_path=path.as_posix(),
        sources=sources,
        warnings=warnings,
    )
