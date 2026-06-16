"""Per-market rolling trade flow for volume surge detection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class FlowSnapshot:
    vol_1m_krw: float
    vol_surge_ratio: float | None
    whale_count_5m: int
    vol_surge: bool


class MarketFlowTracker:
    """Rolling 1h trade KRW amounts for one market."""

    __slots__ = ("_events", "_min_krw", "_surge_ratio")

    def __init__(self, *, min_krw: float, surge_ratio: float) -> None:
        self._events: deque[tuple[float, float]] = deque()
        self._min_krw = min_krw
        self._surge_ratio = surge_ratio

    def on_trade(self, ts: float, krw: float) -> FlowSnapshot:
        self._events.append((ts, krw))
        cutoff_1h = ts - 3600.0
        while self._events and self._events[0][0] < cutoff_1h:
            self._events.popleft()

        vol_1m = sum(k for t, k in self._events if t >= ts - 60.0)
        vol_1h = sum(k for _, k in self._events)
        avg_per_min = vol_1h / 60.0 if vol_1h > 0 else 0.0
        ratio = (vol_1m / avg_per_min) if avg_per_min > 0 else None
        whale_5m = sum(
            1 for t, k in self._events if t >= ts - 300.0 and k >= self._min_krw
        )
        surge = ratio is not None and ratio >= self._surge_ratio
        return FlowSnapshot(
            vol_1m_krw=vol_1m,
            vol_surge_ratio=ratio,
            whale_count_5m=whale_5m,
            vol_surge=surge,
        )
