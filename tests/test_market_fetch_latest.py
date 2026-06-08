"""fetch_latest_market_price 테스트."""

from __future__ import annotations

import sqlite3

from deepsignal.storage.database import fetch_latest_market_price, init_database


def test_fetch_latest_market_price_returns_close(tmp_path) -> None:
    db = tmp_path / "m.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            INSERT INTO market_prices (
                symbol, timeframe, bar_time, source, open, high, low, close,
                adjusted_close, volume, raw_json
            ) VALUES (?, '1d', ?, 'yfinance', 1,1,1,?, ?, 100, '{}')
            """,
            ("ZZ", "2026-05-10", 123.45, 123.45),
        )
        conn.commit()
    row = fetch_latest_market_price(str(db), "ZZ")
    assert row is not None
    assert row["symbol"] == "ZZ"
    assert row["trade_date"] == "2026-05-10"
    assert abs(float(row["close"]) - 123.45) < 1e-6


def test_fetch_latest_market_price_missing(tmp_path) -> None:
    db = tmp_path / "m2.db"
    init_database(str(db))
    assert fetch_latest_market_price(str(db), "NONE") is None
