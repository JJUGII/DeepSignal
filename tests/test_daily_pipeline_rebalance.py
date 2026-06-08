"""run-daily --paper-rebalance 경로."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from deepsignal.config.settings import Settings
from deepsignal.pipelines import daily_pipeline as dp
from deepsignal.storage import database as dbmod


def test_run_daily_paper_rebalance_calls_once(monkeypatch) -> None:
    calls: list[str] = []

    def fake_reb(path_str, settings, **kwargs):
        calls.append("rebalance")
        return {"outcome": "success", "trades": []}

    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))
    monkeypatch.setattr(dp, "collect_news_to_db", lambda *_a, **_k: {})
    monkeypatch.setattr(
        dp,
        "collect_market_to_db",
        lambda *_a, **_k: {"yfinance_status": "success", "collector_errors": []},
    )
    monkeypatch.setattr(
        dp,
        "collect_macro_to_db",
        lambda *_a, **_k: {"collector_status": "success"},
    )
    monkeypatch.setattr(
        dp, "score_symbol_to_db", lambda *_a, **_k: {"outcome": "success"}
    )
    monkeypatch.setattr(dp, "backtest_symbol_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_step_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_rebalance_to_db", fake_reb)

    settings = replace(Settings(db_path="data/x.db"), market_symbols=("X",))
    r = dp.run_daily_pipeline(
        settings,
        skip_news=True,
        skip_market=True,
        run_backtest=False,
        paper_rebalance=True,
    )
    assert "rebalance" in calls
    assert calls.count("rebalance") == 1
    assert any(s.name == "paper-rebalance" for s in r.steps)
    assert any(s.name == "paper:X" and "skipped" in s.status for s in r.steps)


def test_run_daily_no_paper_skips_rebalance(monkeypatch) -> None:
    calls: list[str] = []

    def fake_reb(*_a, **_k):
        calls.append("rebalance")
        return {"outcome": "success"}

    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))
    monkeypatch.setattr(dp, "collect_news_to_db", lambda *_a, **_k: {})
    monkeypatch.setattr(
        dp,
        "collect_market_to_db",
        lambda *_a, **_k: {"yfinance_status": "success"},
    )
    monkeypatch.setattr(
        dp,
        "collect_macro_to_db",
        lambda *_a, **_k: {"collector_status": "success"},
    )
    monkeypatch.setattr(
        dp, "score_symbol_to_db", lambda *_a, **_k: {"outcome": "success"}
    )
    monkeypatch.setattr(dp, "backtest_symbol_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_rebalance_to_db", fake_reb)

    settings = replace(Settings(db_path="data/x.db"), market_symbols=("X",))
    dp.run_daily_pipeline(
        settings,
        skip_news=True,
        skip_market=True,
        run_backtest=False,
        run_paper=False,
        paper_rebalance=True,
    )
    assert "rebalance" not in calls


def test_run_daily_paper_rebalance_passes_rebalance_config(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_reb(path_str, settings, *, rebalance_config=None, **_k):
        _ = path_str, settings
        captured["cfg"] = rebalance_config
        return {"outcome": "success", "trades": []}

    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))
    monkeypatch.setattr(dp, "collect_news_to_db", lambda *_a, **_k: {})
    monkeypatch.setattr(
        dp,
        "collect_market_to_db",
        lambda *_a, **_k: {"yfinance_status": "success", "collector_errors": []},
    )
    monkeypatch.setattr(
        dp,
        "collect_macro_to_db",
        lambda *_a, **_k: {"collector_status": "success"},
    )
    monkeypatch.setattr(
        dp, "score_symbol_to_db", lambda *_a, **_k: {"outcome": "success"}
    )
    monkeypatch.setattr(dp, "backtest_symbol_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_step_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_rebalance_to_db", fake_reb)

    from deepsignal.paper_trading.paper_trading_engine import PaperRebalanceConfig

    custom = PaperRebalanceConfig(commission_rate=0.055)
    settings = replace(Settings(db_path="data/x.db"), market_symbols=("X",))
    dp.run_daily_pipeline(
        settings,
        skip_news=True,
        skip_market=True,
        run_backtest=False,
        paper_rebalance=True,
        paper_rebalance_config=custom,
    )
    cfg = captured.get("cfg")
    assert cfg is not None
    assert cfg.commission_rate == 0.055
