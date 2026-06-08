"""market_prices 저장 테스트."""

from __future__ import annotations

from deepsignal.collector.market.market_data import MarketData
from deepsignal.storage.database import init_database, insert_market_prices


def test_duplicate_market_row_skipped(tmp_path) -> None:
    db = tmp_path / "m.db"
    init_database(str(db))
    row = MarketData(
        symbol="AAA",
        trade_date="2024-01-05",
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        adjusted_close=None,
        volume=10,
        source="yfinance",
        raw={"k": 1},
    )
    s1 = insert_market_prices(str(db), [row, row], timeframe="1d")
    assert s1["inserted"] == 1
    assert s1["skipped"] == 1
    assert s1["failed"] == 0

    s2 = insert_market_prices(str(db), [row], timeframe="1d")
    assert s2["inserted"] == 0
    assert s2["skipped"] == 1
