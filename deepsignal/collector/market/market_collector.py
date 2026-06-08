"""시장 OHLCV 수집 (yfinance, API 키 불필요)."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from deepsignal.collector.market.market_data import MarketData

logger = logging.getLogger(__name__)

_DEFAULT_SYMBOLS: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ")


class MarketCollector:
    """yfinance 기반 일봉 OHLCV 수집."""

    def __init__(
        self,
        symbols: Optional[Sequence[str]] = None,
        *,
        period: str = "1mo",
        interval: str = "1d",
    ) -> None:
        self._symbols: tuple[str, ...] = _normalize_symbols(symbols) if symbols else _DEFAULT_SYMBOLS
        self._period = period
        self._interval = interval

    def collect_per_symbol(
        self,
        symbols: Optional[Sequence[str]] = None,
        *,
        period: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> Iterator[tuple[str, list[MarketData], str | None]]:
        """
        심볼별로 (티커, 수집 행 목록, 오류 메시지)를 순회한다.
        오류 시 해당 심볼만 실패하고 빈 리스트를 반환한다.
        """
        syms = _normalize_symbols(symbols) if symbols is not None else self._symbols
        per = period if period is not None else self._period
        inv = interval if interval is not None else self._interval
        for symbol in syms:
            batch, err = self._fetch_symbol(symbol, per, inv)
            yield symbol, batch, err

    def collect_daily(
        self,
        symbols: Optional[Sequence[str]] = None,
        *,
        period: Optional[str] = None,
        interval: Optional[str] = None,
    ) -> list[MarketData]:
        """모든 심볼의 일봉 데이터를 한 리스트로 반환한다."""
        out: list[MarketData] = []
        for sym, batch, err in self.collect_per_symbol(
            symbols=symbols, period=period, interval=interval
        ):
            if err:
                logger.warning("market fetch issue symbol=%s err=%s", sym, err)
            out.extend(batch)
        return out

    def _fetch_symbol(self, symbol: str, period: str, interval: str) -> tuple[list[MarketData], str | None]:
        rows: list[MarketData] = []
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period, interval=interval, auto_adjust=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance history failed symbol=%s err=%s", symbol, exc)
            return [], str(exc)

        if hist is None or hist.empty:
            return [], "empty history"

        try:
            for idx, r in hist.iterrows():
                trade_date = _index_to_date_str(idx)
                rows.append(MarketData.from_yfinance_row(symbol, trade_date, r, source="yfinance"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance row parse failed symbol=%s err=%s", symbol, exc)
            return [], str(exc)

        return rows, None


def _normalize_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    out: list[str] = []
    for s in symbols:
        t = str(s).strip().upper()
        if t:
            out.append(t)
    return tuple(out)


def _index_to_date_str(idx: Any) -> str:
    ts = pd.Timestamp(idx)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%d")
