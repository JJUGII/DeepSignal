"""백테스트 v2 뉴스 반영·fetch_news_items_until."""

from __future__ import annotations

from datetime import date, timedelta

from deepsignal.backtest.backtest_engine import BacktestEngine
from deepsignal.collector.market.market_data import MarketData
from deepsignal.collector.news.news_item import NewsItem, create_source_hash
from deepsignal.storage.database import (
    fetch_market_prices,
    fetch_news_items_until,
    init_database,
    insert_market_prices,
    insert_news_items,
)


def _news(sym: str, title: str, published_at: str, url: str) -> NewsItem:
    h = create_source_hash("t", url, title)
    return NewsItem(
        title=title,
        url=url,
        source="t",
        published_at=published_at,
        summary="",
        symbol=sym,
        raw={},
        source_hash=h,
    )


def _ohlcv_rows(sym: str, n: int, start: date) -> list[MarketData]:
    out: list[MarketData] = []
    for i in range(n):
        d = (start + timedelta(days=i)).isoformat()[:10]
        c = 100.0 + float(i) * 0.2
        out.append(
            MarketData(
                symbol=sym,
                trade_date=d,
                open=c,
                high=c + 0.1,
                low=c - 0.1,
                close=c,
                adjusted_close=None,
                volume=1_000_000,
                source="yfinance",
                raw={},
            )
        )
    return out


def test_fetch_news_items_until_excludes_future_published(tmp_path) -> None:
    db = str(tmp_path / "fn.db")
    init_database(db)
    sym = "UUT"
    insert_news_items(
        db,
        [
            _news(sym, "profit day", "2024-01-05T10:00:00+00:00", "https://u/1"),
            _news(sym, "plunge after period", "2024-02-20T10:00:00+00:00", "https://u/2"),
        ],
    )
    rows_mid = fetch_news_items_until(db, sym, "2024-01-10", limit=100)
    assert len(rows_mid) == 1
    assert "profit" in (rows_mid[0].get("title") or "")

    rows_late = fetch_news_items_until(db, sym, "2024-03-01", limit=100)
    assert len(rows_late) == 2


def test_fetch_news_items_until_ignores_null_published(tmp_path) -> None:
    db = str(tmp_path / "fn2.db")
    init_database(db)
    sym = "ZZZ"
    insert_news_items(
        db,
        [
            _news(sym, "has date", "2024-03-01T00:00:00+00:00", "https://z/1"),
            NewsItem(
                title="no pub",
                url="https://z/2",
                source="t",
                published_at=None,
                summary="",
                symbol=sym,
                raw={},
                source_hash=create_source_hash("t", "https://z/2", "no pub"),
            ),
        ],
    )
    r = fetch_news_items_until(db, sym, "2024-03-31", limit=50)
    assert len(r) == 1


def test_backtest_include_news_equity_and_parameters(tmp_path) -> None:
    db = str(tmp_path / "btn.db")
    init_database(db)
    sym = "XBT"
    insert_market_prices(db, _ohlcv_rows(sym, 40, date(2024, 1, 1)), timeframe="1d")
    insert_news_items(
        db,
        [
            _news(sym, "strong profit rally", "2024-01-15T12:00:00+00:00", "https://x/1"),
        ],
    )
    rows = fetch_market_prices(db, sym, source="yfinance", limit=None, timeframe="1d")
    eng = BacktestEngine()
    r = eng.run_symbol_backtest(sym, rows, include_news=True, db_path=db)
    assert r is not None
    assert r.raw["parameters"]["include_news"] is True
    assert r.raw["parameters"]["db_path_used"] is True
    assert all("news_score" in e for e in r.raw["equity_curve"])

    r0 = eng.run_symbol_backtest(sym, rows, include_news=False)
    assert r0 is not None
    assert r0.raw["parameters"]["include_news"] is False
    assert r0.raw["parameters"]["db_path_used"] is False
    assert not any("news_score" in e for e in r0.raw["equity_curve"])


def test_backtest_include_news_without_db_path_is_technical_only(tmp_path) -> None:
    db = str(tmp_path / "nt.db")
    init_database(db)
    sym = "YBT"
    insert_market_prices(db, _ohlcv_rows(sym, 30, date(2024, 2, 1)), timeframe="1d")
    insert_news_items(db, [_news(sym, "profit", "2024-02-10T00:00:00+00:00", "https://y/1")])
    rows = fetch_market_prices(db, sym, source="yfinance", limit=None, timeframe="1d")
    r = BacktestEngine().run_symbol_backtest(sym, rows, include_news=True, db_path=None)
    assert r is not None
    assert r.raw["parameters"]["db_path_used"] is False
    assert not any("news_score" in e for e in r.raw["equity_curve"])
