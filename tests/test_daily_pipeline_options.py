"""run-daily 운영 옵션 (네트워크 없음, monkeypatch)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import main as main_mod
from deepsignal.config.settings import Settings
from deepsignal.pipelines import daily_pipeline as dp
from deepsignal.pipelines.daily_pipeline import DailyPipelineResult
from deepsignal.storage import database as dbmod


def _stub_collect_macro(*_a, **_k):
    return {
        "collector_status": "success",
        "collected": 3,
        "inserted": 0,
        "skipped": 0,
        "failed": 0,
    }


def test_skip_news_skips_collect_news(monkeypatch) -> None:
    calls: list[str] = []

    def track_news(*_a, **_k):
        calls.append("news")

    def track_market(*_a, **_k):
        calls.append("market")
        return {"yfinance_status": "success", "collector_errors": []}

    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))
    monkeypatch.setattr(dp, "collect_news_to_db", track_news)
    monkeypatch.setattr(dp, "collect_market_to_db", track_market)
    monkeypatch.setattr(dp, "collect_macro_to_db", _stub_collect_macro)
    monkeypatch.setattr(dp, "score_symbol_to_db", lambda *_a, **_k: {"outcome": "success"})
    monkeypatch.setattr(dp, "backtest_symbol_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_step_to_db", lambda *_a, **_k: "success")

    settings = replace(Settings(db_path="data/x.db"), market_symbols=("X",))
    r = dp.run_daily_pipeline(settings, skip_news=True)
    assert "news" not in calls
    assert "market" in calls
    assert any(s.name == "collect-news" and s.status == "skipped" for s in r.steps)
    assert any(s.name == "collect-macro" and s.status == "success" for s in r.steps)


def test_skip_market_skips_collect_market(monkeypatch) -> None:
    calls: list[str] = []

    def track_news(*_a, **_k):
        calls.append("news")
        return {}

    def track_market(*_a, **_k):
        calls.append("market")
        return {"yfinance_status": "success"}

    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))
    monkeypatch.setattr(dp, "collect_news_to_db", track_news)
    monkeypatch.setattr(dp, "collect_market_to_db", track_market)
    monkeypatch.setattr(dp, "collect_macro_to_db", _stub_collect_macro)
    monkeypatch.setattr(dp, "score_symbol_to_db", lambda *_a, **_k: {"outcome": "success"})
    monkeypatch.setattr(dp, "backtest_symbol_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_step_to_db", lambda *_a, **_k: "success")

    settings = replace(Settings(db_path="data/x.db"), market_symbols=("X",))
    dp.run_daily_pipeline(settings, skip_market=True)
    assert "market" not in calls
    assert "news" in calls


def test_symbols_override_priority(monkeypatch) -> None:
    market_syms: list[tuple[str, ...] | None] = []
    scored: list[str] = []

    def capture_market(_p, _s, symbols=None):
        market_syms.append(symbols)
        return {"yfinance_status": "success", "collector_errors": []}

    def capture_score(_p, sym: str):
        scored.append(sym.upper())
        return {"outcome": "success", "news_score": None, "news_count": 0}

    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))
    monkeypatch.setattr(dp, "collect_news_to_db", lambda *_a, **_k: {})
    monkeypatch.setattr(dp, "collect_market_to_db", capture_market)
    monkeypatch.setattr(dp, "collect_macro_to_db", _stub_collect_macro)
    monkeypatch.setattr(dp, "score_symbol_to_db", capture_score)
    monkeypatch.setattr(dp, "backtest_symbol_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_step_to_db", lambda *_a, **_k: "success")

    settings = replace(Settings(db_path="data/x.db"), market_symbols=("IGNORE", "ME"))
    dp.run_daily_pipeline(settings, symbols=("AAPL", "NVDA"))
    assert market_syms and market_syms[0] == ("AAPL", "NVDA")
    assert scored == ["AAPL", "NVDA"]


def test_no_backtest_no_paper(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))
    monkeypatch.setattr(dp, "collect_news_to_db", lambda *_a, **_k: {})
    monkeypatch.setattr(
        dp,
        "collect_market_to_db",
        lambda *_a, **_k: {"yfinance_status": "success"},
    )
    monkeypatch.setattr(dp, "collect_macro_to_db", _stub_collect_macro)

    def track_score(_p, sym):
        calls.append(f"score:{sym}")
        return {"outcome": "success"}

    def track_bt(*_a, **_k):
        calls.append("bt")

    def track_paper(*_a, **_k):
        calls.append("paper")

    monkeypatch.setattr(dp, "score_symbol_to_db", track_score)
    monkeypatch.setattr(dp, "backtest_symbol_to_db", track_bt)
    monkeypatch.setattr(dp, "paper_step_to_db", track_paper)

    settings = replace(Settings(db_path="data/x.db"), market_symbols=("Z",))
    r = dp.run_daily_pipeline(
        settings,
        run_backtest=False,
        run_paper=False,
        skip_news=True,
        skip_market=True,
    )
    assert "score:Z" in calls
    assert "bt" not in calls
    assert "paper" not in calls
    assert any(s.name == "backtest:Z" and s.status == "skipped" for s in r.steps)
    assert any(s.name == "paper:Z" and s.status == "skipped" for s in r.steps)


def test_log_json_written(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))
    monkeypatch.setattr(dp, "collect_news_to_db", lambda *_a, **_k: {})
    monkeypatch.setattr(
        dp,
        "collect_market_to_db",
        lambda *_a, **_k: {"yfinance_status": "success", "collector_errors": []},
    )
    monkeypatch.setattr(dp, "collect_macro_to_db", _stub_collect_macro)
    monkeypatch.setattr(
        dp,
        "score_symbol_to_db",
        lambda *_a, **_k: {
            "outcome": "success",
            "symbol": "QQ",
            "news_score": 12.5,
            "news_count": 2,
            "technical_score": 50.0,
            "macro_score": -5.0,
            "market_regime": "neutral",
            "macro_confidence": 0.66,
            "final_score": 53.75,
        },
    )
    monkeypatch.setattr(dp, "backtest_symbol_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_step_to_db", lambda *_a, **_k: "success")

    settings = replace(Settings(db_path="data/x.db"), market_symbols=("QQ",))
    r = dp.run_daily_pipeline(
        settings,
        skip_news=True,
        skip_market=True,
        write_log_json=True,
    )
    assert r.log_json_path is not None
    log_file = Path(r.log_json_path)
    assert log_file.is_file()
    data = json.loads(log_file.read_text(encoding="utf-8"))
    assert data["options"]["skip_news"] is True
    assert "steps" in data and len(data["steps"]) >= 1
    assert "errors" in data
    score_step = next(s for s in data["steps"] if s["name"] == "score:QQ")
    assert score_step["raw"]["news_score"] == 12.5
    assert score_step["raw"]["news_count"] == 2
    assert data["macro"]["macro_score"] == -5.0
    assert data["macro"]["symbol"] == "QQ"


def test_main_run_daily_passes_flags(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(settings, **kwargs):
        captured["settings"] = settings
        captured.update(kwargs)
        return DailyPipelineResult(
            started_at="t0",
            finished_at="t1",
            symbols=(),
            success=True,
        )

    monkeypatch.setattr(dp, "run_daily_pipeline", fake_run)
    assert (
        main_mod.main(
            [
                "run-daily",
                "--skip-news",
                "--symbols",
                "A,B",
                "--no-backtest",
                "--log-json",
            ]
        )
        == 0
    )
    assert captured.get("skip_news") is True
    assert captured.get("symbols") == ("A", "B")
    assert captured.get("run_backtest") is False
    assert captured.get("write_log_json") is True
    assert captured.get("skip_macro") is False


def test_skip_macro_skips_collect_macro(monkeypatch) -> None:
    calls: list[str] = []

    def track_macro(*_a, **_k):
        calls.append("macro")
        return _stub_collect_macro()

    monkeypatch.setattr(dbmod, "init_database", lambda _p: Path("/tmp/fake.db"))
    monkeypatch.setattr(dp, "collect_news_to_db", lambda *_a, **_k: {})
    monkeypatch.setattr(
        dp,
        "collect_market_to_db",
        lambda *_a, **_k: {"yfinance_status": "success"},
    )
    monkeypatch.setattr(dp, "collect_macro_to_db", track_macro)
    monkeypatch.setattr(dp, "score_symbol_to_db", lambda *_a, **_k: {"outcome": "success"})
    monkeypatch.setattr(dp, "backtest_symbol_to_db", lambda *_a, **_k: "success")
    monkeypatch.setattr(dp, "paper_step_to_db", lambda *_a, **_k: "success")

    settings = replace(Settings(db_path="data/x.db"), market_symbols=("X",))
    r = dp.run_daily_pipeline(settings, skip_news=True, skip_market=True, skip_macro=True)
    assert "macro" not in calls
    assert any(s.name == "collect-macro" and s.status == "skipped" for s in r.steps)
