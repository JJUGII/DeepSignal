"""collect-news CLI 경로 테스트 (네트워크 없음)."""

from __future__ import annotations

import main as main_mod
from deepsignal.collector.news import news_collector as nc_mod
from deepsignal.collector.news.news_item import NewsItem, create_source_hash


def _fake_collect_per_source(self: nc_mod.NewsCollector):
    item = NewsItem(
        title="Stub",
        url="https://example.com/stub",
        source="unit",
        published_at=None,
        summary="S",
        symbol=None,
        raw={"stub": True},
        source_hash=create_source_hash("unit", "https://example.com/stub", "Stub"),
    )
    yield "unit", [item], None


def test_main_collect_news_pipeline(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "cli_news.db"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setattr(nc_mod.NewsCollector, "collect_per_source", _fake_collect_per_source, raising=True)

    main_mod.main(["collect-news"])
    out = capsys.readouterr().out
    assert "DeepSignal news collection finished" in out
    assert "Collected: 1" in out
    assert "Inserted: 1" in out
