"""MarketData / yfinance 행 변환 테스트."""

from __future__ import annotations

import math

import pandas as pd

from deepsignal.collector.market.market_data import MarketData, _float_or_none, _int_or_none


def test_float_or_none_nan() -> None:
    assert _float_or_none(float("nan")) is None
    assert _float_or_none(None) is None
    assert _float_or_none(1.25) == 1.25


def test_int_or_none_from_float() -> None:
    assert _int_or_none(10.0) == 10
    assert _int_or_none(math.nan) is None


def test_from_yfinance_row_normalizes_date_and_none() -> None:
    row = pd.Series(
        {
            "Open": 1.0,
            "High": 2.0,
            "Low": 0.5,
            "Close": 1.5,
            "Adj Close": float("nan"),
            "Volume": 1000.0,
        }
    )
    md = MarketData.from_yfinance_row("aapl", "2024-01-02", row, source="yfinance")
    assert md.symbol == "AAPL"
    assert md.trade_date == "2024-01-02"
    assert md.open == 1.0
    assert md.adjusted_close is None
    assert md.volume == 1000
    assert md.source == "yfinance"
    assert "Open" in md.raw


def test_from_yfinance_row_accepts_mapping() -> None:
    md = MarketData.from_yfinance_row(
        "MSFT",
        "2024-03-01",
        {"Open": 1, "High": 2, "Low": 0.5, "Close": 1.2, "Volume": 500},
        source="yfinance",
    )
    assert md.close == 1.2
    assert md.volume == 500
