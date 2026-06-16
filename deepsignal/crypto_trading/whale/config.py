"""Whale trade watch configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(key: str, default: str = "false") -> bool:
    return (os.environ.get(key) or default).strip().lower() in ("1", "true", "yes", "on")


def _float_env(key: str, default: float) -> float:
    try:
        return float((os.environ.get(key) or str(default)).strip())
    except (TypeError, ValueError):
        return default


def _int_env(key: str, default: int) -> int:
    try:
        return int((os.environ.get(key) or str(default)).strip())
    except (TypeError, ValueError):
        return default


_DEFAULT_EXCLUDE = (
    "KRW-BTC",
    "KRW-ETH",
    "KRW-XRP",
    "KRW-SOL",
    "KRW-USDT",
    "KRW-USDC",
    "KRW-USD1",
    "KRW-USDE",
    "KRW-USDS",
)


@dataclass(frozen=True)
class WhaleWatchConfig:
    enabled: bool = True
    min_krw: float = 30_000_000.0
    vol_surge_ratio: float = 2.5
    exchanges: tuple[str, ...] = ("upbit", "bithumb")
    exclude_markets: frozenset[str] = frozenset(_DEFAULT_EXCLUDE)
    max_markets: int = 80
    buffer_size: int = 200
    market_refresh_sec: float = 1800.0
    ws_chunk_size: int = 40

    @classmethod
    def from_env(cls) -> WhaleWatchConfig:
        raw_ex = (os.environ.get("WHALE_EXCLUDE_MARKETS") or "").strip()
        exclude = frozenset(
            m.strip().upper()
            for m in (raw_ex.split(",") if raw_ex else _DEFAULT_EXCLUDE)
            if m.strip()
        )
        raw_exch = (os.environ.get("WHALE_WATCH_EXCHANGES") or "upbit,bithumb").strip().lower()
        exchanges = tuple(
            x.strip()
            for x in raw_exch.split(",")
            if x.strip() in ("upbit", "bithumb")
        ) or ("upbit", "bithumb")
        return cls(
            enabled=_truthy("WHALE_WATCH_ENABLED", "true"),
            min_krw=max(1_000_000.0, _float_env("WHALE_MIN_KRW", 30_000_000.0)),
            vol_surge_ratio=max(1.0, _float_env("WHALE_VOL_SURGE_RATIO", 2.5)),
            exchanges=exchanges,
            exclude_markets=exclude,
            max_markets=max(10, _int_env("WHALE_MAX_MARKETS", 80)),
            buffer_size=max(20, _int_env("WHALE_BUFFER_SIZE", 200)),
            market_refresh_sec=max(300.0, _float_env("WHALE_MARKET_REFRESH_SEC", 1800.0)),
            ws_chunk_size=max(10, _int_env("WHALE_WS_CHUNK_SIZE", 40)),
        )
