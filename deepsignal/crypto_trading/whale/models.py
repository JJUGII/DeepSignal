"""Whale trade event models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class WhaleTrade:
    exchange: str
    market: str
    symbol: str
    side: str
    price: float
    volume: float
    krw: float
    vol_1m_krw: float
    vol_surge_ratio: float | None
    whale_count_5m: int
    vol_surge: bool
    ts: str
    ts_ms: float
    event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
