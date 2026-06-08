"""MacroScorer v1 규칙 테스트."""

from __future__ import annotations

from deepsignal.scoring.macro_scorer import MacroScorer


def test_high_vix_negative_score() -> None:
    sc = MacroScorer()
    r = sc.calculate_macro_score(
        [{"indicator_name": "VIX", "indicator_date": "2026-01-02", "value": 32.0}]
    )
    assert r.macro_score is not None
    assert r.macro_score < 0
    assert r.market_regime == "risk_off"


def test_low_vol_risk_on_regime() -> None:
    sc = MacroScorer()
    r = sc.calculate_macro_score(
        [
            {"indicator_name": "VIX", "indicator_date": "2026-01-02", "value": 12.0},
            {"indicator_name": "DXY", "indicator_date": "2026-01-02", "value": 99.0},
            {"indicator_name": "TNX", "indicator_date": "2026-01-02", "value": 2.8},
        ]
    )
    assert r.macro_score is not None
    assert r.macro_score >= 20
    assert r.market_regime == "risk_on"


def test_macro_score_clamped() -> None:
    sc = MacroScorer()
    r = sc.calculate_macro_score(
        [
            {"indicator_name": "VIX", "indicator_date": "2026-01-02", "value": 40.0},
            {"indicator_name": "DXY", "indicator_date": "2026-01-02", "value": 110.0},
            {"indicator_name": "TNX", "indicator_date": "2026-01-02", "value": 6.0},
        ]
    )
    assert r.macro_score is not None
    assert -100.0 <= r.macro_score <= 100.0


def test_regime_neutral_band() -> None:
    sc = MacroScorer()
    r = sc.calculate_macro_score(
        [
            {"indicator_name": "VIX", "indicator_date": "2026-01-02", "value": 18.0},
            {"indicator_name": "DXY", "indicator_date": "2026-01-02", "value": 102.0},
            {"indicator_name": "TNX", "indicator_date": "2026-01-02", "value": 3.5},
        ]
    )
    assert r.macro_score is not None
    assert -20 < r.macro_score < 20
    assert r.market_regime == "neutral"


def test_empty_indicators_no_score() -> None:
    sc = MacroScorer()
    r = sc.calculate_macro_score([])
    assert r.macro_score is None
    assert r.confidence == 0.0
