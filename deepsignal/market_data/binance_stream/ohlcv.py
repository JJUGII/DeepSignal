"""Multi-timeframe OHLCV aggregation from trade ticks."""

from __future__ import annotations

from dataclasses import dataclass, field

from deepsignal.market_data.binance_stream.models import OhlcvBar, TradeTick


def _bar_open_ts(ts_ms: int, interval_ms: int) -> int:
    return (int(ts_ms) // int(interval_ms)) * int(interval_ms)


def timeframe_label(minutes: int) -> str:
    return f"{int(minutes)}m"


@dataclass
class _BarBuilder:
    symbol: str
    timeframe: str
    interval_ms: int
    open_ts_ms: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    quote_volume: float = 0.0
    trade_count: int = 0
    taker_buy_volume: float = 0.0

    def apply_trade(self, tick: TradeTick) -> None:
        px = float(tick.price)
        qty = float(tick.qty)
        if self.trade_count == 0:
            self.open = self.high = self.low = self.close = px
        else:
            self.high = max(self.high, px)
            self.low = min(self.low, px)
            self.close = px
        self.volume += qty
        self.quote_volume += px * qty
        self.trade_count += 1
        if not tick.is_buyer_maker:   # taker buy (buyer hit the ask)
            self.taker_buy_volume += qty

    def to_bar(self, *, closed: bool) -> OhlcvBar:
        return OhlcvBar(
            symbol=self.symbol,
            timeframe=self.timeframe,
            open_ts_ms=self.open_ts_ms,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            quote_volume=self.quote_volume,
            trade_count=self.trade_count,
            taker_buy_ratio=self.taker_buy_volume / self.volume if self.volume > 0 else 0.0,
            closed=closed,
        )


class OhlcvAggregator:
    """Build 1m/3m/15m bars from tick-by-tick trades."""

    def __init__(self, symbol: str, timeframes_minutes: tuple[int, ...] = (1, 3, 15)) -> None:
        self.symbol = symbol.upper()
        self._tfs = tuple(int(m) for m in timeframes_minutes)
        self._builders: dict[str, _BarBuilder] = {}
        self._interval_ms: dict[str, int] = {
            timeframe_label(m): int(m) * 60_000 for m in self._tfs
        }

    def on_trade(self, tick: TradeTick) -> list[OhlcvBar]:
        closed: list[OhlcvBar] = []
        for tf, interval_ms in self._interval_ms.items():
            bucket = _bar_open_ts(tick.ts_ms, interval_ms)
            builder = self._builders.get(tf)
            if builder is None:
                builder = _BarBuilder(
                    symbol=self.symbol,
                    timeframe=tf,
                    interval_ms=interval_ms,
                    open_ts_ms=bucket,
                )
                self._builders[tf] = builder
                builder.apply_trade(tick)
                continue

            if bucket > builder.open_ts_ms:
                if builder.trade_count > 0:
                    closed.append(builder.to_bar(closed=True))
                builder = _BarBuilder(
                    symbol=self.symbol,
                    timeframe=tf,
                    interval_ms=interval_ms,
                    open_ts_ms=bucket,
                )
                builder.apply_trade(tick)
                self._builders[tf] = builder
            elif bucket < builder.open_ts_ms:
                continue
            else:
                builder.apply_trade(tick)
        return closed

    def snapshot_open_bars(self) -> list[OhlcvBar]:
        return [
            b.to_bar(closed=False)
            for b in self._builders.values()
            if b.trade_count > 0
        ]
