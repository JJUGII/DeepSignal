"""뉴스 SQLite 저장 테스트."""

from __future__ import annotations

from deepsignal.collector.news.news_item import NewsItem, create_source_hash
from deepsignal.storage.database import init_database, insert_news_items


def test_duplicate_news_item_insert_skipped(tmp_path: Path) -> None:
    db = tmp_path / "news.db"
    init_database(str(db))
    h = create_source_hash("u", "https://example.com/x", "T")
    one = NewsItem(
        title="T",
        url="https://example.com/x",
        source="u",
        published_at=None,
        summary=None,
        symbol=None,
        raw={"k": 1},
        source_hash=h,
    )
    s1 = insert_news_items(str(db), [one, one])
    assert s1["inserted"] == 1
    assert s1["skipped"] == 1
    assert s1["failed"] == 0

    s2 = insert_news_items(str(db), [one])
    assert s2["inserted"] == 0
    assert s2["skipped"] == 1
