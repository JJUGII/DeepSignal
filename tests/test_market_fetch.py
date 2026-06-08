"""fetch_market_prices 조회 테스트."""

from __future__ import annotations

from datetime import date, timedelta

from deepsignal.collector.market.market_data import MarketData
from deepsignal.storage.database import fetch_market_prices, init_database, insert_market_prices


def test_fetch_market_prices_order_and_limit(tmp_path) -> None:
    db = tmp_path / "mf.db"
    init_database(str(db))
    base = date(2024, 1, 1)
    rows = [
        MarketData(
            symbol="ZZ",
            trade_date=(base + timedelta(days=i)).isoformat(),
            open=1.0,
            high=1.0,
            low=1.0,
            close=float(i + 1),
            adjusted_close=None,
            volume=100,
            source="yfinance",
            raw={},
        )
        for i in range(7)
    ]
    insert_market_prices(str(db), rows, timeframe="1d")

    all_rows = fetch_market_prices(str(db), "ZZ", source="yfinance", limit=None, timeframe="1d")
    times = [r["bar_time"] for r in all_rows]
    assert times == sorted(times)

    lim_rows = fetch_market_prices(str(db), "ZZ", source="yfinance", limit=3, timeframe="1d")
    assert len(lim_rows) == 3
    assert [r["bar_time"] for r in lim_rows] == [
        (base + timedelta(days=4)).isoformat(),
        (base + timedelta(days=5)).isoformat(),
        (base + timedelta(days=6)).isoformat(),
    ]
    assert lim_rows[0]["trade_date"] == lim_rows[0]["bar_time"]
