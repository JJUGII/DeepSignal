"""
crypto_ws_price_stream.py — Upbit WebSocket ticker 실시간 스트림.

wss://api.upbit.com/websocket/v1 에 연결하여 지정 마켓의 현재가를
콜백으로 전달한다. 연결 끊김 시 exponential backoff 후 자동 재연결.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_WS_URI = "wss://api.upbit.com/websocket/v1"
_RECONNECT_BASE = 2.0   # seconds
_RECONNECT_MAX = 60.0


def _subscribe_payload(markets: list[str]) -> str:
    return json.dumps([
        {"ticket": str(uuid.uuid4())},
        {"type": "ticker", "codes": sorted(markets), "isOnlyRealtime": True},
    ])


async def stream_upbit_prices(
    markets_fn: Callable[[], list[str]],
    on_price: Callable[[str, float], Coroutine[Any, Any, None]],
    stop_event: asyncio.Event,
    *,
    ping_interval: float = 20.0,
    ping_timeout: float = 10.0,
) -> None:
    """
    Upbit ticker WebSocket 스트림. stop_event 세트 시 종료.

    Args:
        markets_fn: 구독할 마켓 목록 반환 함수 (재연결마다 재조회)
        on_price: async 콜백 (market, trade_price)
        stop_event: 종료 시그널
    """
    try:
        import websockets
    except ImportError:
        logger.error("websockets 패키지가 없습니다: pip install 'websockets>=12.0'")
        return

    backoff = _RECONNECT_BASE
    while not stop_event.is_set():
        markets = markets_fn()
        if not markets:
            await asyncio.sleep(1.0)
            continue
        try:
            async with websockets.connect(
                _WS_URI,
                ping_interval=ping_interval,
                ping_timeout=ping_timeout,
                max_size=2**20,
            ) as ws:
                await ws.send(_subscribe_payload(markets))
                logger.info("ws: 연결 완료 (%d마켓 구독)", len(markets))
                backoff = _RECONNECT_BASE  # reset on success

                current_markets = set(markets)
                async for raw in ws:
                    if stop_event.is_set():
                        break
                    try:
                        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                    except Exception:
                        continue

                    market = str(data.get("code") or "")
                    price = data.get("trade_price")
                    if not market or price is None:
                        continue

                    try:
                        await on_price(market, float(price))
                    except Exception as exc:
                        logger.warning("ws on_price error: %s", exc)

                    # 마켓 목록이 바뀌었으면 재연결
                    new_markets = set(markets_fn())
                    if new_markets != current_markets:
                        logger.info("ws: 구독 마켓 변경 → 재연결")
                        break

        except asyncio.CancelledError:
            break
        except Exception as exc:
            if stop_event.is_set():
                break
            logger.warning("ws: 연결 오류 (%s) — %.0f초 후 재연결", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX)

    logger.info("ws: 스트림 종료")
