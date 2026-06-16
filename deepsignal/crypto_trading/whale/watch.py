"""Whale trade watch coordinator."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from deepsignal.crypto_trading.whale.buffer import get_whale_buffer, make_whale_trade
from deepsignal.crypto_trading.whale.config import WhaleWatchConfig
from deepsignal.crypto_trading.whale.streams import stream_exchange_trades
from deepsignal.crypto_trading.whale.tracker import MarketFlowTracker
from deepsignal.crypto_trading.whale.universe import select_watch_markets

logger = logging.getLogger(__name__)


class WhaleWatchService:
    def __init__(self) -> None:
        self._buffer = get_whale_buffer()
        self._cfg = WhaleWatchConfig.from_env()
        self._markets: list[str] = []
        self._trackers: dict[str, MarketFlowTracker] = {}
        self._markets_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    async def _refresh_markets(self) -> None:
        cfg = WhaleWatchConfig.from_env()
        self._cfg = cfg
        markets = await asyncio.to_thread(select_watch_markets, cfg)
        async with self._markets_lock:
            self._markets = markets
            self._trackers = {
                m: MarketFlowTracker(min_krw=cfg.min_krw, surge_ratio=cfg.vol_surge_ratio)
                for m in markets
            }
        self._buffer.configure(cfg, markets)
        logger.info("whale watch: %d markets selected", len(markets))

    def _markets_for_exchange(self, exchange: str) -> list[str]:
        return list(self._markets)

    async def _on_trade(
        self,
        exchange: str,
        market: str,
        price: float,
        volume: float,
        side: str,
        ts_sec: float,
    ) -> None:
        async with self._markets_lock:
            if market not in self._trackers:
                return
            tracker = self._trackers[market]
            cfg = self._cfg

        krw = price * volume
        self._buffer.bump_seen()
        flow = tracker.on_trade(ts_sec, krw)
        if krw < cfg.min_krw:
            return

        trade = make_whale_trade(
            exchange=exchange,
            market=market,
            side=side,
            price=price,
            volume=volume,
            krw=krw,
            vol_1m_krw=flow.vol_1m_krw,
            vol_surge_ratio=flow.vol_surge_ratio,
            whale_count_5m=flow.whale_count_5m,
            vol_surge=flow.vol_surge,
            ts_sec=ts_sec,
        )
        self._buffer.add(trade)
        if self._on_emit:
            await self._on_emit(trade.to_dict())

    _on_emit: Any = None

    async def run(self, *, on_whale: Any = None) -> None:
        """Run until cancelled."""
        from dotenv import load_dotenv

        load_dotenv(override=False)
        self._on_emit = on_whale
        self._stop.clear()
        cfg = WhaleWatchConfig.from_env()
        if not cfg.enabled:
            logger.info("whale watch disabled (WHALE_WATCH_ENABLED=false)")
            return

        await self._refresh_markets()
        self._buffer.mark_started(list(cfg.exchanges))

        async def _market_refresh_loop() -> None:
            while not self._stop.is_set():
                await asyncio.sleep(cfg.market_refresh_sec)
                if self._stop.is_set():
                    break
                try:
                    await self._refresh_markets()
                except Exception as exc:
                    logger.warning("whale market refresh failed: %s", exc)

        refresh_task = asyncio.create_task(_market_refresh_loop())
        stream_tasks = []
        for ex in cfg.exchanges:
            stream_tasks.append(
                asyncio.create_task(
                    stream_exchange_trades(
                        ex,
                        lambda e=ex: self._markets_for_exchange(e),
                        self._on_trade,
                        self._stop,
                        chunk_size=cfg.ws_chunk_size,
                    )
                )
            )
        try:
            await asyncio.gather(*stream_tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._stop.set()
            refresh_task.cancel()
            for t in stream_tasks:
                t.cancel()
            await asyncio.gather(refresh_task, *stream_tasks, return_exceptions=True)

    def stop(self) -> None:
        self._stop.set()


_service: WhaleWatchService | None = None


def get_whale_service() -> WhaleWatchService:
    global _service
    if _service is None:
        _service = WhaleWatchService()
    return _service
