"""analyze-technical CLI 스모크."""

from __future__ import annotations

import main as main_mod
from deepsignal.analyzer.technical.technical_analyzer import TechnicalIndicator


def _fake_analyze_symbol_from_db(self, db_path, symbol, source="yfinance", limit=120):
    return [
        TechnicalIndicator(
            symbol="FAKE",
            trade_date="2026-05-03",
            close=190.0,
            ema_12=188.0,
            ema_26=185.0,
            rsi_14=55.0,
            trend_score=1.0,
            raw={},
        ),
        TechnicalIndicator(
            symbol="FAKE",
            trade_date="2026-05-04",
            close=192.0,
            ema_12=189.0,
            ema_26=186.0,
            rsi_14=56.0,
            trend_score=1.0,
            raw={},
        ),
    ]


def test_main_analyze_technical_smoke(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "at.db"
    monkeypatch.setenv("DB_PATH", str(db))
    from deepsignal.analyzer.technical import technical_analyzer as ta_mod

    monkeypatch.setattr(
        ta_mod.TechnicalAnalyzer,
        "analyze_symbol_from_db",
        _fake_analyze_symbol_from_db,
        raising=True,
    )

    main_mod.main(["analyze-technical", "FAKE"])
    out = capsys.readouterr().out
    assert "DeepSignal technical analysis finished" in out
    assert "Symbol: FAKE" in out
    assert "Rows analyzed: 2" in out
