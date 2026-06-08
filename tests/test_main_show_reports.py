"""show-* CLI 스모크."""

from __future__ import annotations

import main as main_mod
from deepsignal.scoring.signal_scorer import SignalResult
from deepsignal.storage.database import init_database, insert_signal_result


def test_main_show_signals_smoke(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "sr.db"
    monkeypatch.setenv("DB_PATH", str(db))
    init_database(str(db))
    insert_signal_result(
        str(db),
        SignalResult(
            symbol="Z",
            signal_date="2024-01-01",
            technical_score=1.0,
            news_score=2.0,
            macro_score=None,
            final_score=1.3,
            action="HOLD",
            confidence=0.1,
            reason="x",
            raw={},
        ),
    )
    main_mod.main(["show-signals"])
    out = capsys.readouterr().out
    assert "signals" in out
    assert "news_score" in out
    assert "technical_score" in out


def test_main_show_backtests_smoke(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "br.db"))
    main_mod.main(["show-backtests"])
    out = capsys.readouterr().out
    assert "backtest" in out.lower()


def test_main_show_paper_smoke(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pr.db"))
    main_mod.main(["show-paper"])
    out = capsys.readouterr().out
    assert "paper" in out.lower()
