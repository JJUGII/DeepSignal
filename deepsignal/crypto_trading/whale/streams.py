"""Upbit / Bithumb public trade WebSocket streams."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

_WS_URIS = {
    "upbit": "wss://api.upbit.com/websocket/v1",
    "bithumb": "wss://ws-api.bithumb.com/websocket/v1",
}
_RECONNECT_BASE = 2.0
_RECONNECT_MAX = 60.0


def _subscribe_payload(markets: list[str]) -> str:
    return json.dumps([
        {"ticket": str(uuid.uuid4())},
        {"type": "trade", "codes": sorted(markets), "isOnlyRealtime": True},
    ])


def _parse_trade(data: dict[str, Any], exchange: str) -> tuple[str, float, float, str, float] | None:
    """Returns (market, price, volume, side, ts_sec) or None."""
    if str(data.get("type") or "") != "trade":
        return None
    if str(data.get("stream_type") or "").upper() == "SNAPSHOT":
        return None
    market = str(data.get("code") or "").strip().upper()
    price = data.get("trade_price")
    volume = data.get("trade_volume")
    if not market or price is None or volume is None:
        return None
    try:
        px = float(price)
        vol = float(volume)
    except (TypeError, ValueError):
        return None
    if px <= 0 or vol <= 0:
        return None
    ask_bid = str(data.get("ask_bid") or "").upper()
    side = "buy" if ask_bid == "BID" else "sell"
    ts_ms = float(data.get("trade_timestamp") or data.get("timestamp") or 0)
    ts_sec = ts_ms / 1000.0 if ts_ms > 1e12 else (ts_ms if ts_ms > 0 else time.time())
    return market, px, vol, side, ts_sec


async def stream_exchange_trades(
    exchange: str,
    markets_fn: Callable[[], list[str]],
    on_trade: Callable[[str, str, float, float, str, float], Awaitable[None]],
    stop_event: asyncio.Event,
    *,
    chunk_size: int = 40,
) -> None:
    """Subscribe to trade ticks for one exchange (may open multiple WS connections)."""
    try:
        import websockets
    except ImportError:
        logger.error("websockets 패키지 없음 — whale watch 비활성")
        return

    uri = _WS_URIS.get(exchange)
    if not uri:
        return

    backoff = _RECONNECT_BASE
    while not stop_event.is_set():
        markets = [m for m in markets_fn() if m]
        if not markets:
            await asyncio.sleep(5.0)
            continue

        chunks = [markets[i : i + chunk_size] for i in range(0, len(markets), chunk_size)]

        async def _run_chunk(codes: list[str]) -> None:
            nonlocal backoff
            local_backoff = backoff
            current = set(codes)
            while not stop_event.is_set():
                try:
                    async with websockets.connect(
                        uri,
                        ping_interval=20.0,
                        ping_timeout=10.0,
                        max_size=2**20,
                        open_timeout=15.0,
                    ) as ws:
                        await ws.send(_subscribe_payload(codes))
                        logger.info("whale ws %s: %d markets connected", exchange, len(codes))
                        local_backoff = _RECONNECT_BASE
                        async for raw in ws:
                            if stop_event.is_set():
                                break
                            try:
                                data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                            except Exception:
                                continue
                            if not isinstance(data, dict):
                                continue
                            parsed = _parse_trade(data, exchange)
                            if parsed is None:
                                continue
                            market, px, vol, side, ts_sec = parsed
                            try:
                                await on_trade(exchange, market, px, vol, side, ts_sec)
                            except Exception as exc:
                                logger.warning("whale on_trade error: %s", exc)
                            if set(codes) != current:
                                break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if stop_event.is_set():
                        break
                    logger.warning("whale ws %s error (%s) — %.0fs reconnect", exchange, exc, local_backoff)
                    await asyncio.sleep(local_backoff)
                    local_backoff = min(local_backoff * 2, _RECONNECT_MAX)

        tasks = [asyncio.create_task(_run_chunk(c)) for c in chunks]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        if stop_event.is_set():
            break
        await asyncio.sleep(1.0)

    logger.info("whale ws %s: stopped", exchange)
