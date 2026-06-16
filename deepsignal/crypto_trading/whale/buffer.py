"""In-memory ring buffer for recent whale trades."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from deepsignal.crypto_trading.whale.config import WhaleWatchConfig
from deepsignal.crypto_trading.whale.models import WhaleTrade


def _iso_now(ts: float | None = None) -> str:
    t = ts if ts is not None else time.time()
    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


class WhaleWatchBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._trades: deque[WhaleTrade] = deque(maxlen=200)
        self._cfg: WhaleWatchConfig | None = None
        self._markets: list[str] = []
        self._stats: dict[str, Any] = {
            "connected_exchanges": [],
            "trades_seen": 0,
            "whales_emitted": 0,
            "started_at": None,
            "last_trade_at": None,
        }

    def configure(self, cfg: WhaleWatchConfig, markets: list[str]) -> None:
        with self._lock:
            self._cfg = cfg
            self._markets = list(markets)
            self._trades = deque(list(self._trades)[-cfg.buffer_size :], maxlen=cfg.buffer_size)

    def mark_started(self, exchanges: list[str]) -> None:
        with self._lock:
            self._stats["connected_exchanges"] = exchanges
            self._stats["started_at"] = _iso_now()

    def add(self, trade: WhaleTrade) -> None:
        with self._lock:
            if self._cfg and len(self._trades) >= self._cfg.buffer_size:
                pass
            self._trades.appendleft(trade)
            self._stats["whales_emitted"] = int(self._stats.get("whales_emitted") or 0) + 1
            self._stats["last_trade_at"] = trade.ts

    def bump_seen(self) -> None:
        with self._lock:
            self._stats["trades_seen"] = int(self._stats.get("trades_seen") or 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            cfg = self._cfg
            return {
                "enabled": bool(cfg.enabled) if cfg else False,
                "min_krw": cfg.min_krw if cfg else 0,
                "vol_surge_ratio": cfg.vol_surge_ratio if cfg else 0,
                "markets_subscribed": len(self._markets),
                "markets": list(self._markets),
                "stats": dict(self._stats),
                "trades": [t.to_dict() for t in list(self._trades)],
            }


_buffer = WhaleWatchBuffer()


def get_whale_buffer() -> WhaleWatchBuffer:
    return _buffer


def make_whale_trade(
    *,
    exchange: str,
    market: str,
    side: str,
    price: float,
    volume: float,
    krw: float,
    vol_1m_krw: float,
    vol_surge_ratio: float | None,
    whale_count_5m: int,
    vol_surge: bool,
    ts_sec: float,
) -> WhaleTrade:
    sym = market.replace("KRW-", "")
    return WhaleTrade(
        exchange=exchange,
        market=market,
        symbol=sym,
        side=side,
        price=price,
        volume=volume,
        krw=krw,
        vol_1m_krw=vol_1m_krw,
        vol_surge_ratio=vol_surge_ratio,
        whale_count_5m=whale_count_5m,
        vol_surge=vol_surge,
        ts=_iso_now(ts_sec),
        ts_ms=ts_sec * 1000.0,
        event_id=str(uuid.uuid4()),
    )
