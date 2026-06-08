"""KIS 체결 틱 → OHLCV 봉 집계기.

Binance OhlcvAggregator와 동일한 버킷 알고리즘, KIS 데이터 모델에 맞게 조정.
"""

from __future__ import annotations

from dataclasses import dataclass

from deepsignal.market_data.kis_stream.models import KisOhlcvBar, KisTradeTick


def _bar_open_ts(ts_ms: int, interval_ms: int) -> int:
    return (int(ts_ms) // int(interval_ms)) * int(interval_ms)


@dataclass
class _KisBarBuilder:
    symbol: str
    timeframe: str
    interval_ms: int
    open_ts_ms: int = 0
    open: int = 0
    high: int = 0
    low: int = 0
    close: int = 0
    volume: int = 0
    trade_value: int = 0
    trade_count: int = 0
    buy_count: int = 0

    def apply_tick(self, tick: KisTradeTick) -> None:
        px = tick.price
        qty = tick.qty
        if self.trade_count == 0:
            self.open = self.high = self.low = self.close = px
        else:
            if px > self.high:
                self.high = px
            if px < self.low:
                self.low = px
            self.close = px
        self.volume += qty
        self.trade_value += px * qty
        self.trade_count += 1
        if tick.is_buyer:
            self.buy_count += 1

    def to_bar(self, *, closed: bool) -> KisOhlcvBar:
        buy_ratio = self.buy_count / self.trade_count if self.trade_count > 0 else 0.0
        return KisOhlcvBar(
            symbol=self.symbol,
            timeframe=self.timeframe,
            open_ts_ms=self.open_ts_ms,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            trade_value=self.trade_value,
            trade_count=self.trade_count,
            buy_ratio=buy_ratio,
            closed=closed,
        )


class KisOhlcvAggregator:
    """KIS 체결 틱으로 1m/5m/15m 봉을 실시간 생성."""

    def __init__(
        self,
        symbol: str,
        timeframes_minutes: tuple[int, ...] = (1, 5, 15),
    ) -> None:
        self.symbol = symbol.strip()
        self._tfs = tuple(int(m) for m in timeframes_minutes)
        self._builders: dict[str, _KisBarBuilder] = {}
        self._interval_ms: dict[str, int] = {
            f"{m}m": int(m) * 60_000 for m in self._tfs
        }

    def on_tick(self, tick: KisTradeTick) -> list[KisOhlcvBar]:
        """틱 처리. 완성된 봉 리스트를 반환 (보통 0~1개)."""
        closed: list[KisOhlcvBar] = []
        for tf, interval_ms in self._interval_ms.items():
            bucket = _bar_open_ts(tick.ts_ms, interval_ms)
            builder = self._builders.get(tf)
            if builder is None:
                builder = _KisBarBuilder(
                    symbol=self.symbol,
                    timeframe=tf,
                    interval_ms=interval_ms,
                    open_ts_ms=bucket,
                )
                self._builders[tf] = builder
                builder.apply_tick(tick)
                continue

            if bucket > builder.open_ts_ms:
                if builder.trade_count > 0:
                    closed.append(builder.to_bar(closed=True))
                builder = _KisBarBuilder(
                    symbol=self.symbol,
                    timeframe=tf,
                    interval_ms=interval_ms,
                    open_ts_ms=bucket,
                )
                builder.apply_tick(tick)
                self._builders[tf] = builder
            elif bucket < builder.open_ts_ms:
                # 이전 버킷 틱 (순서 역전) — 무시
                continue
            else:
                builder.apply_tick(tick)
        return closed

    def snapshot_open_bars(self) -> list[KisOhlcvBar]:
        """현재 열린(미완성) 봉 스냅샷."""
        return [
            b.to_bar(closed=False)
            for b in self._builders.values()
            if b.trade_count > 0
        ]

    def reset(self) -> None:
        self._builders.clear()
