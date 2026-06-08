"""NewsItem 해시 및 RSS 정규화 테스트."""

from __future__ import annotations

import feedparser

from deepsignal.collector.news.news_item import NewsItem, create_source_hash


def test_same_url_same_hash() -> None:
    h1 = create_source_hash("s", "https://Example.COM/path?q=1", "t1")
    h2 = create_source_hash("s", "https://example.com/path?q=1", "t2")
    assert h1 == h2


def test_no_url_uses_source_and_title() -> None:
    h1 = create_source_hash("src_a", "", "Hello")
    h2 = create_source_hash("src_a", "", "Hello")
    h3 = create_source_hash("src_b", "", "Hello")
    assert h1 == h2
    assert h1 != h3


def test_from_rss_entry_minimal() -> None:
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0"><channel><item>
      <title>Test Title</title>
      <link>https://example.com/news/1</link>
      <description>Short desc</description>
    </item></channel></rss>"""
    parsed = feedparser.parse(xml)
    assert len(parsed.entries) == 1
    item = NewsItem.from_rss_entry("test_src", parsed.entries[0])
    assert item.title == "Test Title"
    assert item.url == "https://example.com/news/1"
    assert item.source == "test_src"
    assert item.summary == "Short desc"
    assert item.symbol is None
    assert isinstance(item.raw, dict)
    assert item.source_hash == create_source_hash("test_src", item.url, item.title)


def test_published_parsed_to_iso() -> None:
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel><item>
      <title>T</title><link>https://a/b</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
    </item></channel></rss>"""
    parsed = feedparser.parse(xml)
    item = NewsItem.from_rss_entry("x", parsed.entries[0])
    assert item.published_at is not None
    assert item.published_at.endswith("+00:00")
    assert len(item.published_at) >= 19
