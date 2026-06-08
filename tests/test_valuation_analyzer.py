"""밸류에이션 v1 (mock)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from deepsignal.analyzer.valuation.valuation_analyzer import ValuationAnalyzer


@patch("yfinance.Ticker")
def test_valuation_mispricing(mock_ticker: MagicMock) -> None:
    inst = mock_ticker.return_value
    inst.info = {
        "currentPrice": 100.0,
        "trailingPE": 20.0,
        "forwardPE": 15.0,
        "priceToBook": 3.0,
        "revenueGrowth": 0.1,
        "trailingEps": 5.0,
        "bookValue": 30.0,
    }
    import pandas as pd

    inst.history.return_value = pd.DataFrame({"Close": [100.0]})
    res = ValuationAnalyzer().analyze_symbol("AAPL")
    assert res.market_price == 100.0
    assert res.intrinsic_value is not None
    assert res.mispricing_pct is not None
