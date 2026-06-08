"""Parse Binance WebSocket JSON payloads."""

from __future__ import annotations

from typing import Any

from deepsignal.market_data.binance_stream.models import (
    FundingSnapshot,
    OrderBookSnapshot,
    TradeTick,
)


def parse_combined_message(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    stream = str(raw.get("stream") or "")
    data = raw.get("data")
    if isinstance(data, dict):
        return stream, data
    return stream, raw


def parse_trade(data: dict[str, Any]) -> TradeTick | None:
    if str(data.get("e") or "") not in ("trade", ""):
        if "p" not in data or "q" not in data:
            return None
    try:
        return TradeTick(
            symbol=str(data.get("s") or "").upper(),
            price=float(data.get("p") or 0),
            qty=float(data.get("q") or 0),
            ts_ms=int(data.get("T") or data.get("E") or 0),
            is_buyer_maker=bool(data.get("m")),
        )
    except (TypeError, ValueError):
        return None


def _parse_levels(levels: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not isinstance(levels, list):
        return out
    for row in levels:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            out.append((float(row[0]), float(row[1])))
        except (TypeError, ValueError):
            continue
    return out


def parse_depth_snapshot(symbol: str, data: dict[str, Any], *, ts_ms: int = 0) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        symbol=symbol.upper(),
        bids=_parse_levels(data.get("bids") or data.get("b")),
        asks=_parse_levels(data.get("asks") or data.get("a")),
        last_update_id=int(data.get("lastUpdateId") or data.get("u") or 0),
        ts_ms=int(ts_ms or data.get("E") or 0),
    )


def parse_mark_price(data: dict[str, Any]) -> FundingSnapshot | None:
    if str(data.get("e") or "") not in ("markPriceUpdate", ""):
        pass
    try:
        return FundingSnapshot(
            symbol=str(data.get("s") or "").upper(),
            mark_price=float(data.get("p") or 0),
            funding_rate=float(data.get("r") or 0),
            next_funding_ts_ms=int(data.get("T") or 0),
            ts_ms=int(data.get("E") or 0),
        )
    except (TypeError, ValueError):
        return None


def stream_symbol(stream_name: str) -> str:
    """btcusdt@trade -> BTCUSDT"""
    base = stream_name.split("@", 1)[0]
    return base.upper()
