"""fetch_recent_news_items 조회."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.collector.news.news_item import NewsItem, create_source_hash
from deepsignal.storage.database import (
    fetch_recent_news_items,
    init_database,
    insert_news_items,
)


def _item(
    title: str,
    *,
    summary: str | None = None,
    symbol: str | None = None,
    url: str = "https://example.com/x",
) -> NewsItem:
    h = create_source_hash("src", url, title)
    return NewsItem(
        title=title,
        url=url,
        source="src",
        published_at="2024-01-15T12:00:00+00:00",
        summary=summary,
        symbol=symbol,
        raw={"id": title[:8]},
        source_hash=h,
    )


def test_fetch_by_symbol_column(tmp_path: Path) -> None:
    db = str(tmp_path / "n.db")
    init_database(db)
    insert_news_items(
        db,
        [
            _item("Other", symbol=None, url="https://a/1"),
            _item("NVDA news", symbol="NVDA", url="https://a/2"),
        ],
    )
    rows = fetch_recent_news_items(db, symbol="NVDA", limit=50)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NVDA"


def test_fetch_by_title_like(tmp_path: Path) -> None:
    db = str(tmp_path / "n2.db")
    init_database(db)
    insert_news_items(
        db,
        [
            _item("Unrelated", symbol=None, summary="no ticker", url="https://b/1"),
            _item("Apple AAPL outlook", symbol=None, summary="summary only", url="https://b/2"),
        ],
    )
    rows = fetch_recent_news_items(db, symbol="AAPL", limit=50)
    assert len(rows) >= 1
    titles = " ".join(r.get("title") or "" for r in rows)
    assert "AAPL" in titles


def test_fetch_all_recent_limit(tmp_path: Path) -> None:
    db = str(tmp_path / "n3.db")
    init_database(db)
    items = [_item(f"T{i}", url=f"https://c/{i}") for i in range(5)]
    insert_news_items(db, items)
    rows = fetch_recent_news_items(db, symbol=None, limit=3)
    assert len(rows) == 3
