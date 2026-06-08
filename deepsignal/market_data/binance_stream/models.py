"""Shared models for Binance stream pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TradeTick:
    symbol: str
    price: float
    qty: float
    ts_ms: int
    is_buyer_maker: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OhlcvBar:
    symbol: str
    timeframe: str
    open_ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trade_count: int
    taker_buy_ratio: float = 0.0
    closed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OhlcvBar":
        return cls(
            symbol=str(d["symbol"]),
            timeframe=str(d["timeframe"]),
            open_ts_ms=int(d["open_ts_ms"]),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=float(d["volume"]),
            quote_volume=float(d.get("quote_volume", 0.0)),
            trade_count=int(d.get("trade_count", 0)),
            taker_buy_ratio=float(d.get("taker_buy_ratio", 0.0)),
            closed=bool(d.get("closed", True)),
        )


@dataclass
class OrderBookSnapshot:
    symbol: str
    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)
    last_update_id: int = 0
    ts_ms: int = 0

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid_price(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is not None and ba is not None:
            return (bb + ba) / 2.0
        return bb or ba

    @property
    def spread_bps(self) -> float | None:
        bb, ba = self.best_bid, self.best_ask
        if bb is None or ba is None or bb <= 0:
            return None
        mid = (bb + ba) / 2.0
        if mid <= 0:
            return None
        return (ba - bb) / mid * 10_000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bids": [[p, q] for p, q in self.bids[:20]],
            "asks": [[p, q] for p, q in self.asks[:20]],
            "last_update_id": self.last_update_id,
            "ts_ms": self.ts_ms,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": self.mid_price,
            "spread_bps": self.spread_bps,
        }


@dataclass
class FundingSnapshot:
    symbol: str
    mark_price: float
    funding_rate: float
    next_funding_ts_ms: int
    ts_ms: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
