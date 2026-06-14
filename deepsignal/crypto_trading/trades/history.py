"""Fetch and normalize completed crypto orders (Upbit/Bithumb) for Web UI / reports."""

from __future__ import annotations

from typing import Any


def normalize_order_side_to_trade(side: str) -> str:
    s = str(side or "").lower()
    if s in ("bid", "buy"):
        return "buy"
    if s in ("ask", "sell"):
        return "sell"
    return s


def _float_val(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def order_id_from_row(row: dict[str, Any]) -> str:
    return str(row.get("uuid") or row.get("order_id") or "").strip()


def done_order_to_trade_item(
    row: dict[str, Any],
    *,
    broker_id: str,
    source: str,
) -> dict[str, Any] | None:
    """Map a single done-order API row to Web UI trade-history shape."""
    if not isinstance(row, dict):
        return None
    side_kr = normalize_order_side_to_trade(str(row.get("side") or ""))
    mkt = str(row.get("market") or "").strip().upper()
    if not mkt:
        return None
    sym = mkt.replace("KRW-", "")
    created_raw = str(row.get("created_at") or row.get("order_date") or row.get("timestamp") or "")
    price = _float_val(row.get("avg_price")) or _float_val(row.get("price"))
    vol = _float_val(row.get("executed_volume")) or _float_val(row.get("volume"))
    amount = _float_val(row.get("trades_price")) or round(price * vol, 0)
    paid_fee = _float_val(row.get("paid_fee"))
    return {
        "tab": "crypto",
        "executed_at": created_raw,
        "symbol": sym,
        "market": "KRW",
        "side": side_kr,
        "quantity": round(vol, 8),
        "unit_price": round(price, 1),
        "trade_amount": round(amount, 0),
        "fee": round(paid_fee, 2),
        "settlement": round(amount - paid_fee if side_kr == "sell" else amount + paid_fee, 0),
        "order_id": order_id_from_row(row),
        "slippage_bps": None,
        "source": source,
        "broker": broker_id,
    }


def fetch_done_orders_from_broker(broker: Any, *, max_pages: int = 10) -> list[dict[str, Any]]:
    """Paginated GET /orders?state=done for active broker."""
    exchange_id = str(getattr(broker, "exchange_id", "upbit") or "upbit").lower()
    source = f"{exchange_id}_api"
    raw_orders: list[dict[str, Any]] = []

    for page in range(1, max(1, int(max_pages)) + 1):
        params: dict[str, Any] = {"state": "done", "order_by": "desc", "limit": 100}
        if exchange_id == "upbit" or page > 1:
            params["page"] = page
        try:
            if exchange_id == "bithumb":
                batch = broker._request("GET", "/orders", params=params, private=True)
            else:
                batch = broker._request("GET", "/orders", params=params)
        except Exception:
            if page == 1:
                raise
            break
        if not isinstance(batch, list) or not batch:
            break
        for row in batch:
            if isinstance(row, dict):
                raw_orders.append(row)
        if len(batch) < 100:
            break

    out: list[dict[str, Any]] = []
    for row in raw_orders:
        item = done_order_to_trade_item(row, broker_id=exchange_id, source=source)
        if item:
            out.append(item)
    return out


def trades_from_local_audits(
    audit_files: list[str],
    *,
    slip_map: dict,
    start_dt: str,
    end_dt: str,
    type_filter: str = "all",
    symbol: str = "",
    broker_id: str = "",
) -> list[dict]:
    """Build trade rows from telegram/runner approval audit JSON files."""
    import json
    from pathlib import Path

    items: list[dict] = []
    for fpath in audit_files:
        try:
            raw = json.loads(Path(fpath).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not raw.get("executed", False):
            continue
        plan = raw.get("plan") or {}
        if not plan.get("market"):
            continue
        created_raw = str(plan.get("created_at") or "")
        ts_date = created_raw[:10]
        if ts_date and (ts_date < start_dt or ts_date > end_dt):
            continue

        mkt = str(plan.get("market") or "")
        sym = mkt.replace("KRW-", "")
        side = normalize_order_side_to_trade(str(plan.get("side") or ""))
        if symbol and symbol.upper() not in (mkt.upper(), sym.upper()):
            continue
        if type_filter != "all" and side != type_filter:
            continue

        price = _float_val(plan.get("limit_price"))
        amount = _float_val(plan.get("krw_amount"))
        slip_key = (mkt, side, round(price, 1))
        slip = slip_map.get(slip_key) or {}
        fill_p = _float_val(slip.get("fill_price")) or price
        slip_bps = slip.get("slippage_bps")
        fee = round(amount * 0.0005, 2)
        row_broker = str(plan.get("broker") or broker_id or "upbit").lower()
        result = raw.get("result") or {}
        order_id = str(result.get("uuid") or result.get("order_id") or "")

        items.append({
            "tab": "crypto",
            "executed_at": created_raw,
            "symbol": sym,
            "market": "KRW",
            "side": side,
            "quantity": round(amount / price, 8) if price > 0 else 0,
            "unit_price": round(fill_p, 1),
            "trade_amount": round(amount, 0),
            "fee": fee,
            "settlement": round(amount - fee if side == "sell" else amount + fee, 0),
            "order_id": order_id,
            "slippage_bps": slip_bps,
            "source": "local_audit",
            "broker": row_broker,
        })
    return items
