"""실전 운영 상태 요약 대시보드 ([실전-14]). 조회/리포트 전용."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from deepsignal.storage.database import (
    init_database,
    load_latest_real_account_snapshot,
    load_latest_real_positions,
)

STATUS_OK = "OK"
STATUS_WARNING = "WARNING"
STATUS_RISK_ALERT = "RISK_ALERT"
STATUS_RECONCILE_MISMATCH = "RECONCILE_MISMATCH"
STATUS_NO_DATA = "NO_DATA"

RISK_ALERT_STATUSES = {"STOP_LOSS_ALERT", "TAKE_PROFIT_ALERT", "MIXED_ALERT"}


@dataclass
class OpsDashboardResult:
    status: str
    generated_at: str
    account: dict[str, Any]
    positions: list[dict[str, Any]]
    reconcile: dict[str, Any]
    risk: dict[str, Any]
    fills: dict[str, Any]
    recent_orders: list[dict[str, Any]]
    warnings: list[str]


def _latest_json(output_dir: str | Path, pattern: str) -> tuple[Path | None, dict[str, Any]]:
    root = Path(output_dir)
    paths = sorted(root.glob(pattern))
    if not paths:
        return None, {}
    path = paths[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return path, {"_parse_error": True}
    if isinstance(data, dict):
        data["_path"] = path.as_posix()
        return path, data
    return path, {"_non_object_json": True, "_path": path.as_posix()}


def _load_recent_orders_by_limit(
    db_path: str,
    *,
    broker: str = "kis",
    limit: int = 10,
) -> list[dict[str, Any]]:
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT created_at, broker, symbol, side, quantity, limit_price,
                   estimated_order_value, status, order_id, audit_path, raw_json
            FROM real_order_history
            WHERE broker = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (broker, max(0, int(limit))),
        )
        for r in cur.fetchall():
            raw: Any = {}
            if r["raw_json"]:
                try:
                    raw = json.loads(r["raw_json"])
                except json.JSONDecodeError:
                    raw = {"_parse_error": True}
            out.append(
                {
                    "created_at": r["created_at"],
                    "broker": r["broker"],
                    "symbol": r["symbol"],
                    "side": r["side"],
                    "quantity": int(r["quantity"] or 0),
                    "limit_price": r["limit_price"],
                    "estimated_order_value": r["estimated_order_value"],
                    "status": r["status"],
                    "order_id": r["order_id"],
                    "audit_path": r["audit_path"],
                    "raw": raw,
                }
            )
    return out


def _risk_status(risk: dict[str, Any]) -> str:
    return str(risk.get("status") or risk.get("risk_status") or "")


def _decide_status(
    *,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    reconcile: dict[str, Any],
    risk: dict[str, Any],
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not account and not positions:
        warnings.append("No latest real account snapshot found. Run live-sync-account first.")
        return STATUS_NO_DATA, warnings

    if reconcile and reconcile.get("success") is False:
        warnings.append("Reconcile mismatch detected. Do not submit new buy orders before manual review.")
        return STATUS_RECONCILE_MISMATCH, warnings

    rs = _risk_status(risk)
    if rs in RISK_ALERT_STATUSES:
        warnings.append("Risk alert detected. Manual review required; ops-dashboard does not place SELL orders.")
        return STATUS_RISK_ALERT, warnings
    if rs == STATUS_WARNING:
        warnings.append("Risk warning detected. Review positions before additional buy approval.")
        return STATUS_WARNING, warnings

    if not reconcile:
        warnings.append("No reconcile report found. Run reconcile-live-account after account sync.")
        return STATUS_WARNING, warnings
    if not risk:
        warnings.append("No risk report found. Run risk-check after reconcile.")
        return STATUS_WARNING, warnings

    return STATUS_OK, warnings


def build_ops_dashboard(
    db_path: str,
    *,
    output_dir: str | Path = "outputs",
    broker: str = "kis",
    recent_orders: int = 10,
) -> OpsDashboardResult:
    """DB와 최신 outputs 리포트만 읽어 운영 상태를 요약한다."""
    init_database(db_path)
    account = load_latest_real_account_snapshot(db_path, broker=broker) or {}
    positions = load_latest_real_positions(db_path, broker=broker)
    _rec_path, reconcile = _latest_json(output_dir, "reconcile_live_account_*.json")
    _risk_path, risk = _latest_json(output_dir, "risk_alert_*.json")
    _fill_path, fills = _latest_json(output_dir, "live_fill_summary_*.json")
    orders = _load_recent_orders_by_limit(db_path, broker=broker, limit=recent_orders)
    status, warnings = _decide_status(account=account, positions=positions, reconcile=reconcile, risk=risk)
    return OpsDashboardResult(
        status=status,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        account=account,
        positions=positions,
        reconcile=reconcile,
        risk=risk,
        fills=fills,
        recent_orders=orders,
        warnings=warnings,
    )


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "-"


def _cell(value: Any) -> str:
    return _fmt(value).replace("|", "\\|")


def _risk_by_symbol(risk: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in risk.get("positions") or []:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").strip()
        if sym:
            out[sym] = row
    return out


def write_ops_dashboard_report(
    result: OpsDashboardResult,
    *,
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    """`outputs/ops_dashboard_*.json` 및 `outputs/OPS_DASHBOARD.md` 저장."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    json_path = root / f"ops_dashboard_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    md_path = root / "OPS_DASHBOARD.md"
    body = asdict(result)
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    account = result.account
    reconcile = result.reconcile
    risk = result.risk
    fills = result.fills
    risk_map = _risk_by_symbol(risk)

    lines = [
        "# DeepSignal Ops Dashboard",
        "",
        "## Status",
        "",
        f"- Overall: **{result.status}**",
        f"- Generated at: {result.generated_at}",
        "- Mode: read-only summary; no SELL, market order, cancel, or KIS order POST.",
        "",
        "## Account",
        "",
        f"- Cash: {_fmt(account.get('cash'))}",
        f"- Withdrawable cash: {_fmt(account.get('withdrawable_cash'))}",
        f"- Total equity: {_fmt(account.get('total_equity'))}",
        f"- Total market value: {_fmt(account.get('total_market_value'))}",
        f"- Snapshot time: {_fmt(account.get('snapshot_time'))}",
        "",
        "## Positions",
        "",
        "| Symbol | Qty | Avg | Current | Market Value | PnL % | Risk |",
        "|--------|-----|-----|---------|--------------|-------|------|",
    ]
    for p in result.positions:
        sym = str(p.get("symbol") or "")
        risk_row = risk_map.get(sym, {})
        pnl_pct = risk_row.get("unrealized_pnl_pct")
        risk_level = risk_row.get("risk_level")
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(sym),
                    _cell(p.get("quantity")),
                    _cell(p.get("avg_price")),
                    _cell(p.get("current_price")),
                    _cell(p.get("market_value")),
                    _pct(pnl_pct),
                    _cell(risk_level),
                ]
            )
            + " |"
        )
    if not result.positions:
        lines.append("| (none) | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Reconcile",
            "",
            f"- success: {_fmt(reconcile.get('success'))}",
            f"- matched: {len(reconcile.get('matched') or [])}",
            f"- missing_in_db: {len(reconcile.get('missing_in_db') or [])}",
            f"- missing_in_broker: {len(reconcile.get('missing_in_broker') or [])}",
            f"- quantity_mismatch: {len(reconcile.get('quantity_mismatch') or [])}",
            f"- report: `{_fmt(reconcile.get('_path'))}`",
            "",
            "## Risk",
            "",
            f"- status: {_fmt(_risk_status(risk))}",
            f"- alerts: {len(risk.get('alerts') or [])}",
            f"- warnings: {len(risk.get('warnings') or [])}",
            f"- report: `{_fmt(risk.get('_path'))}`",
        ]
    )
    for alert in risk.get("alerts") or []:
        lines.append(f"- alert: {alert}")

    summaries = fills.get("summaries") or []
    lines.extend(["", "## Fills", ""])
    lines.append(f"- latest fill summary rows: {len(summaries)}")
    lines.append(f"- report: `{_fmt(fills.get('_path'))}`")
    for s in summaries[:5]:
        if isinstance(s, dict):
            lines.append(
                f"- order `{_fmt(s.get('order_id'))}` `{_fmt(s.get('symbol'))}` "
                f"filled={_fmt(s.get('filled_quantity'))} remaining={_fmt(s.get('remaining_quantity'))} "
                f"status={_fmt(s.get('status_label') or s.get('status'))}"
            )

    lines.extend(["", "## Recent Orders", ""])
    if result.recent_orders:
        lines.extend(
            [
                "| Created | Symbol | Side | Qty | Limit | Status | Order ID |",
                "|---------|--------|------|-----|-------|--------|----------|",
            ]
        )
        for o in result.recent_orders:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(o.get("created_at")),
                        _cell(o.get("symbol")),
                        _cell(o.get("side")),
                        _cell(o.get("quantity")),
                        _cell(o.get("limit_price")),
                        _cell(o.get("status")),
                        _cell(o.get("order_id")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("- (none)")

    lines.extend(["", "## Warnings", ""])
    for warning in result.warnings:
        lines.append(f"- {warning}")
    if not result.warnings:
        lines.append("- (none)")
    lines.extend(
        [
            "",
            "## Important",
            "",
            "- ops-dashboard is read-only.",
            "- It does not place SELL orders, market orders, repeats, cancels, or KIS order POST requests.",
        ]
    )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def run_ops_dashboard(
    db_path: str,
    *,
    output_dir: str | Path = "outputs",
    broker: str = "kis",
    recent_orders: int = 10,
) -> tuple[OpsDashboardResult, Path, Path]:
    result = build_ops_dashboard(
        db_path,
        output_dir=output_dir,
        broker=broker,
        recent_orders=recent_orders,
    )
    json_path, md_path = write_ops_dashboard_report(result, output_dir=output_dir)
    return result, json_path, md_path
