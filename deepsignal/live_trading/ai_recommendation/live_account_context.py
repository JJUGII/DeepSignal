"""Load live account context for AI recommendations.

Network mode performs KIS balance/position inquiry only through safe-mode broker.
It does not place, approve, or execute orders.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from deepsignal.live_trading.ai_recommendation.recommendation_model import AccountContext


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def mark_snapshot_staleness(context: AccountContext, *, stale_minutes: int = 30) -> AccountContext:
    snap_dt = _parse_dt(context.snapshot_time)
    if snap_dt is None:
        context.stale_snapshot = True
        context.snapshot_age_minutes = None
        return context
    age = max(0.0, (datetime.now() - snap_dt).total_seconds() / 60.0)
    context.snapshot_age_minutes = age
    context.stale_snapshot = age > max(1, int(stale_minutes))
    return context


def load_local_account_context(
    db_path: str,
    *,
    broker: str = "kis",
    stale_minutes: int = 30,
) -> AccountContext:
    from deepsignal.storage.database import load_latest_real_account_snapshot, load_latest_real_positions

    snap = load_latest_real_account_snapshot(db_path, broker=broker) or {}
    positions = []
    for row in load_latest_real_positions(db_path, broker=broker):
        positions.append(
            {
                "snapshot_time": row.get("snapshot_time"),
                "broker": row.get("broker"),
                "symbol": row.get("symbol"),
                "quantity": row.get("quantity"),
                "avg_price": row.get("avg_price"),
                "current_price": row.get("current_price"),
                "market_value": row.get("market_value"),
            }
        )
    ctx = AccountContext(
        broker=broker,
        snapshot_time=str(snap.get("snapshot_time") or "") or None,
        cash=_float_or_none(snap.get("cash")),
        withdrawable_cash=_float_or_none(snap.get("withdrawable_cash")),
        total_market_value=_float_or_none(snap.get("total_market_value")),
        total_equity=_float_or_none(snap.get("total_equity")),
        positions=list(positions),
        source="local_db",
    )
    if ctx.total_market_value is None:
        ctx.total_market_value = sum(_float_or_none(p.get("market_value")) or 0.0 for p in positions)
    if ctx.total_equity is None:
        cash = ctx.cash if ctx.cash is not None else 0.0
        ctx.total_equity = cash + (ctx.total_market_value or 0.0)
    return mark_snapshot_staleness(ctx, stale_minutes=stale_minutes)


def load_network_account_context(
    db_path: str,
    *,
    broker: str = "kis",
    output_dir: str | Path = "outputs",
    stale_minutes: int = 30,
) -> AccountContext:
    if broker != "kis":
        raise ValueError("network account context supports only broker='kis'")

    from deepsignal.live_trading.kis_broker import KISBroker
    from deepsignal.live_trading.kis_config import load_kis_config_from_env
    from deepsignal.live_trading.live_account_sync import (
        build_account_snapshot_payload,
        persist_live_account_snapshot_to_db,
        write_live_account_snapshot_paths,
    )

    br = KISBroker(load_kis_config_from_env(), safe_mode=True)
    payload = build_account_snapshot_payload(br)
    write_live_account_snapshot_paths(payload, output_dir=output_dir)
    persist_live_account_snapshot_to_db(db_path, payload, broker=broker)
    return load_local_account_context(db_path, broker=broker, stale_minutes=stale_minutes)
