"""main analyze-news (DB·네트워크 최소)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import main as main_mod
from deepsignal.config.settings import Settings


def test_main_analyze_news_output(monkeypatch, tmp_path: Path, capsys) -> None:
    db = str(tmp_path / "m.db")

    def fake_init(_p):
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        Path(db).touch()
        return Path(db)

    def fake_fetch(_path: str, symbol=None, limit=100):
        assert symbol == "AAPL"
        return [
            {
                "id": 1,
                "title": "Apple profit beats expectations",
                "summary": "bullish rally",
                "symbol": "AAPL",
                "published_at": None,
                "url": "",
            }
        ]

    monkeypatch.setattr("deepsignal.storage.database.init_database", fake_init)
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_recent_news_items",
        fake_fetch,
    )
    monkeypatch.setattr(
        "deepsignal.config.settings.load_settings",
        lambda: replace(Settings(db_path=db)),
    )

    main_mod.main(["analyze-news", "AAPL"])
    out = capsys.readouterr().out
    assert "DeepSignal news sentiment analysis finished" in out
    assert "Symbol: AAPL" in out
    assert "News Count: 1" in out
    assert "News Score:" in out
