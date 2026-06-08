"""crypto_signal_scorer — technical/macro/final and breakdown."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_recommendation_quality import (
    apply_crypto_buy_quality_gates,
    check_crypto_validation_gate,
)
from deepsignal.crypto_trading.crypto_signal_scorer import (
    build_crypto_score_breakdown,
    compute_crypto_technical_score,
    load_crypto_macro_context,
    score_crypto_market,
)
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig, UpbitTicker


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def test_macro_context_fallback_neutral() -> None:
    ctx = load_crypto_macro_context(None)
    assert ctx["market_regime"] == "neutral"
    assert ctx["macro_score"] is None


def test_technical_score_positive_momentum() -> None:
    t = UpbitTicker(
        market="KRW-BTC",
        trade_price=100_000_000,
        signed_change_rate=0.03,
        acc_trade_price_24h=50_000_000_000,
    )
    diag = {"rsi_14": 55.0, "volume_ratio": 1.2, "atr_pct": 3.0}
    tech, comp = compute_crypto_technical_score(t, diag)
    assert tech > 0
    assert comp["momentum_pct"] == 3.0


def test_score_crypto_market_final_with_macro() -> None:
    br = _br()
    t = UpbitTicker(
        market="KRW-ETH",
        trade_price=5_000_000,
        signed_change_rate=0.02,
        acc_trade_price_24h=20_000_000_000,
    )
    macro = {"macro_score": 10.0, "market_regime": "neutral"}
    ms = score_crypto_market(br, t, display_name="이더리움", macro_context=macro)
    assert ms.final_score is not None
    assert ms.macro_regime == "neutral"
    bd = build_crypto_score_breakdown(ms, macro)
    assert bd["display"]["final"] != "n/a"


def test_validation_gate_blocks_risk_off() -> None:
    status, reasons = check_crypto_validation_gate(
        80.0,
        macro_regime="risk_off",
        min_final_score=55.0,
        block_buy_on_risk_off=True,
    )
    assert status == "blocked"
    assert reasons


def test_buy_gates_block_low_final() -> None:
    br = _br()
    t = UpbitTicker(
        market="KRW-XRP",
        trade_price=2000,
        signed_change_rate=-0.05,
        acc_trade_price_24h=100_000_000,
    )
    macro = {"macro_score": -20.0, "market_regime": "neutral"}
    ms = score_crypto_market(br, t, display_name="리플", macro_context=macro)
    from deepsignal.crypto_trading.crypto_recommendation_quality import CryptoRecommendationQualityConfig

    allowed, gates, _bd, blocked = apply_crypto_buy_quality_gates(
        ms,
        ticker=t,
        macro_context=macro,
        order_krw=10_000,
        config=CryptoRecommendationQualityConfig(min_final_score=99.0, enabled=True),
    )
    assert gates["validation"] == "blocked"
    assert not allowed
    assert blocked
