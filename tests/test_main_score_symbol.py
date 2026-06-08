"""score-symbol CLI 스모크."""

from __future__ import annotations

import main as main_mod
from deepsignal.analyzer.technical.technical_analyzer import TechnicalIndicator


def _fake_analyze(self, db_path, symbol, source="yfinance", limit=120):
    return [
        TechnicalIndicator(
            symbol="FAKE",
            trade_date="2026-05-10",
            close=100.0,
            ema_12=95.0,
            ema_26=90.0,
            rsi_14=50.0,
            trend_score=1.0,
            raw={},
        )
    ]


def test_main_score_symbol_smoke(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "sc.db"
    monkeypatch.setenv("DB_PATH", str(db))
    from deepsignal.analyzer.technical import technical_analyzer as ta_mod

    monkeypatch.setattr(
        ta_mod.TechnicalAnalyzer,
        "analyze_symbol_from_db",
        _fake_analyze,
        raising=True,
    )

    main_mod.main(["score-symbol", "FAKE"])
    out = capsys.readouterr().out
    assert "DeepSignal signal scoring finished" in out
    assert "Symbol: FAKE" in out
    assert "Technical Score:" in out
    assert "News Score:" in out
    assert "Macro Score:" in out
    assert "BUY_CANDIDATE" in out
    assert "Saved:" in out
