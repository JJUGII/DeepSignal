"""score_symbol_to_db + 뉴스 감성 → signals.news_score 저장."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from deepsignal.collector.market.market_data import MarketData
from deepsignal.collector.news.news_item import NewsItem, create_source_hash
from deepsignal.pipelines.daily_pipeline import score_symbol_to_db
from deepsignal.storage.database import init_database, insert_market_prices, insert_news_items


def _news_row(sym: str, title: str, url: str) -> NewsItem:
    h = create_source_hash("t", url, title)
    return NewsItem(
        title=title,
        url=url,
        source="t",
        published_at="2024-06-01T10:00:00+00:00",
        summary="bullish growth outlook",
        symbol=sym,
        raw={"x": 1},
        source_hash=h,
    )


def _market_rows(sym: str, n: int = 50) -> list[MarketData]:
    base = date(2024, 1, 1)
    out: list[MarketData] = []
    for i in range(n):
        d = (base + timedelta(days=i)).isoformat()[:10]
        c = 100.0 + i * 0.4
        out.append(
            MarketData(
                symbol=sym,
                trade_date=d,
                open=c,
                high=c + 0.5,
                low=c - 0.5,
                close=c,
                adjusted_close=None,
                volume=1_000_000,
                source="yfinance",
                raw={},
            )
        )
    return out


def test_score_symbol_to_db_persists_news_score(tmp_path: Path) -> None:
    db = str(tmp_path / "sn.db")
    init_database(db)
    sym = "NEWS1"
    insert_market_prices(db, _market_rows(sym), timeframe="1d")
    insert_news_items(
        db,
        [
            _news_row(
                sym,
                "profit rally surge beats expectations",
                f"https://ex.test/{sym}/1",
            )
        ],
    )

    meta = score_symbol_to_db(db, sym)
    assert meta["outcome"] == "success"
    assert meta.get("news_score") is not None

    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "SELECT news_score, final_score, technical_score FROM signals WHERE symbol = ?",
            (sym,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] is not None
    assert abs(float(row[0]) - float(meta["news_score"])) < 1e-6
