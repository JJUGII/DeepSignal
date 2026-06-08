"""analyze-portfolio CLI 스모크."""

from __future__ import annotations

import main as main_mod


def test_main_analyze_portfolio_smoke(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "pf.db"
    monkeypatch.setenv("DB_PATH", str(db))

    def fake_fetch_signals(_p, limit=100):
        _ = limit
        return [
            {
                "symbol": "ZZZ",
                "signal_date": "2026-02-01",
                "final_score": 75.0,
                "action": "BUY_CANDIDATE",
                "confidence": 0.8,
                "technical_score": 75.0,
                "news_score": None,
                "macro_score": None,
            }
        ]

    def fake_macro_rows(_p):
        return []

    def fake_snap(_p):
        return None

    class _MR:
        market_regime = "neutral"
        macro_score = 0.0
        confidence = 0.0
        analyzed_at = "t"
        reason = ""
        raw = {}

    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_signals",
        fake_fetch_signals,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_economic_indicators",
        fake_macro_rows,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_paper_snapshot",
        fake_snap,
    )
    monkeypatch.setattr(
        "deepsignal.scoring.macro_scorer.MacroScorer.calculate_macro_score",
        lambda self, rows: _MR(),
    )

    main_mod.main(["analyze-portfolio"])
    out = capsys.readouterr().out
    assert "DeepSignal portfolio analysis finished" in out
    assert "Market Regime:" in out
    assert "Cash Buffer:" in out
    assert "Allocations:" in out
    assert "ZZZ" in out
