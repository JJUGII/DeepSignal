"""Ensemble and live universe tests."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_live_universe import (
    binance_symbol_to_upbit_market,
    live_state_scan_enabled,
)
from deepsignal.crypto_trading.crypto_ml_ensemble import (
    CryptoMlEnsemble,
    ensemble_enabled,
    fmt_ml_prob,
    rule_score_to_probability,
)
from deepsignal.crypto_trading.crypto_ml_gate import MlGateResult


def test_symbol_map() -> None:
    assert binance_symbol_to_upbit_market("BTCUSDT") == "KRW-BTC"


def test_rule_score_probability() -> None:
    p = rule_score_to_probability(60.0, reference=55.0)
    assert 0.4 < p < 0.8


def test_flags() -> None:
    assert isinstance(ensemble_enabled(), bool)
    assert isinstance(live_state_scan_enabled(), bool)


def test_fmt_ml_prob_none() -> None:
    assert fmt_ml_prob(None) == "n/a"


def test_lgbm_only_none_probability(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CRYPTO_ML_ENSEMBLE", "true")
    monkeypatch.setenv("CRYPTO_ENSEMBLE_MODE", "lgbm_only")
    monkeypatch.setenv("CRYPTO_ML_BUY_GATE", "true")
    ens = CryptoMlEnsemble(tmp_path)

    def _fake_predict(_market: str) -> MlGateResult:
        return MlGateResult(
            allowed=True,
            win_probability=None,
            threshold=0.55,
            model_path=None,
            binance_symbol="BTCUSDT",
            status="skipped",
            reason="no model file — gate skipped",
        )

    monkeypatch.setattr(ens._lgbm, "predict_for_upbit_market", _fake_predict)
    monkeypatch.setattr(ens, "_resolve_mode", lambda **k: ("lgbm_only", True))
    out = ens.predict("KRW-BTC", final_score=60.0)
    assert "n/a" in out.reason
    assert out.allowed is True
    assert out.ensemble_mode == "lgbm_only"
