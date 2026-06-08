"""run-daily 오케스트레이션 순서·심볼 루프 (네트워크 없음, monkeypatch)."""

from __future__ import annotations

from typing import Any

from dataclasses import replace
from pathlib import Path

from deepsignal.config.settings import Settings
from deepsignal.pipelines import daily_pipeline as dp
from deepsignal.storage import database as dbmod


def test_run_daily_pipeline_order_and_symbols(monkeypatch) -> None:
    calls: list[str] = []

    def fake_init_db(_p: str) -> Path:
        calls.append("init")
        return Path("/tmp/fake.db")

    def fake_news(path_str: str, settings: Settings, **_kwargs) -> dict[str, int]:
        calls.append("news")
        return {"collected": 0, "inserted": 0, "skipped": 0, "failed": 0}

    def fake_market(path_str: str, settings: Settings, symbols=None, **_kwargs) -> dict:
        calls.append("market")
        return {"yfinance_status": "success", "collector_errors": []}

    def fake_macro(path_str: str, settings: Settings, **_kwargs) -> dict:
        calls.append("macro")
        return {"collector_status": "success", "collected": 3, "inserted": 0, "skipped": 0, "failed": 0}

    def fake_score(path_str: str, symbol: str) -> dict[str, Any]:
        calls.append(f"score:{symbol.strip().upper()}")
        return {"outcome": "success", "news_score": None, "news_count": 0}

    def fake_bt(path_str: str, symbol: str) -> str:
        calls.append(f"bt:{symbol.strip().upper()}")
        return "success"

    def fake_paper(path_str: str, symbol: str) -> str:
        calls.append(f"paper:{symbol.strip().upper()}")
        return "success"

    monkeypatch.setattr(dbmod, "init_database", fake_init_db)
    monkeypatch.setattr(dp, "collect_news_to_db", fake_news)
    monkeypatch.setattr(dp, "collect_market_to_db", fake_market)
    monkeypatch.setattr(dp, "collect_macro_to_db", fake_macro)
    monkeypatch.setattr(dp, "score_symbol_to_db", fake_score)
    monkeypatch.setattr(dp, "backtest_symbol_to_db", fake_bt)
    monkeypatch.setattr(dp, "paper_step_to_db", fake_paper)

    settings = replace(
        Settings(db_path="data/x.db"),
        market_symbols=("AAA", "BBB"),
    )
    dp.run_daily_pipeline(settings)

    assert calls[0] == "init"
    assert "news" in calls and "market" in calls and "macro" in calls
    idx_news = calls.index("news")
    idx_market = calls.index("market")
    idx_macro = calls.index("macro")
    assert idx_news < idx_market < idx_macro
    assert calls.index("score:AAA") < calls.index("bt:AAA")
    assert calls.index("bt:AAA") < calls.index("paper:AAA")
    assert calls.index("paper:AAA") < calls.index("score:BBB")


def test_run_daily_pipeline_skips_blank_symbols(monkeypatch, capsys) -> None:
    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))

    def noop(*_a, **_k) -> None:
        pass

    def noop_market(*_a, **_k):
        return {"yfinance_status": "success"}

    monkeypatch.setattr(dp, "collect_news_to_db", noop)
    monkeypatch.setattr(dp, "collect_market_to_db", noop_market)
    monkeypatch.setattr(
        dp,
        "collect_macro_to_db",
        lambda *_a, **_k: {"collector_status": "success"},
    )
    monkeypatch.setattr(dp, "score_symbol_to_db", lambda *_a, **_k: {"outcome": "success"})
    monkeypatch.setattr(dp, "backtest_symbol_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_step_to_db", lambda *_a, **_k: "success")

    settings = replace(Settings(db_path="data/x.db"), market_symbols=("  ", "ZZ"))
    dp.run_daily_pipeline(settings)
    out = capsys.readouterr().out
    assert "Symbol: ZZ" in out
    # blank-only symbol should not emit a ZZ-less stray "Symbol:" for whitespace
    assert out.count("--- Symbol:") == 1
