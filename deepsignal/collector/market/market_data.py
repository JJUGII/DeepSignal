"""일봉 OHLCV 등 시장 데이터 모델."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    f = _float_or_none(value)
    if f is None:
        return None
    return int(f)


@dataclass
class MarketData:
    """단일 거래일 OHLCV (yfinance 등 소스에서 정규화)."""

    symbol: str
    trade_date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adjusted_close: float | None
    volume: int | None
    source: str
    raw: dict[str, Any]

    @classmethod
    def from_yfinance_row(
        cls,
        symbol: str,
        trade_date: str,
        row: Mapping[str, Any] | Any,
        *,
        source: str = "yfinance",
    ) -> MarketData:
        """
        yfinance `history()`의 한 행(Series)으로부터 MarketData를 생성한다.
        trade_date는 YYYY-MM-DD 형식이어야 한다.
        """
        if not hasattr(row, "index"):
            series = pd.Series(row)
        else:
            series = row

        def pick(*names: str) -> Any:
            for n in names:
                if n in series.index:
                    return series[n]
            return None

        o = _float_or_none(pick("Open"))
        h = _float_or_none(pick("High"))
        l = _float_or_none(pick("Low"))
        c = _float_or_none(pick("Close"))
        adj = _float_or_none(pick("Adj Close", "AdjClose"))
        vol = _int_or_none(pick("Volume"))

        raw: dict[str, Any] = {}
        for key in series.index:
            val = series[key]
            if hasattr(val, "isoformat"):
                raw[str(key)] = val.isoformat()
            elif pd.isna(val):
                raw[str(key)] = None
            elif isinstance(val, (int, float)):
                fv = float(val)
                raw[str(key)] = None if math.isnan(fv) else fv
            else:
                raw[str(key)] = str(val)

        return cls(
            symbol=symbol.strip().upper(),
            trade_date=trade_date,
            open=o,
            high=h,
            low=l,
            close=c,
            adjusted_close=adj,
            volume=vol,
            source=source,
            raw=raw,
        )
