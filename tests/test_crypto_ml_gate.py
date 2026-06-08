"""ML buy gate tests."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_ml_gate import (
    CryptoMlBuyGate,
    default_buy_threshold,
    ml_buy_gate_enabled,
    upbit_market_to_binance_symbol,
)


def test_upbit_to_binance_symbol() -> None:
    assert upbit_market_to_binance_symbol("KRW-BTC") == "BTCUSDT"
    assert upbit_market_to_binance_symbol("BTCUSDT") == "BTCUSDT"


def test_gate_skipped_without_model(tmp_path) -> None:
    gate = CryptoMlBuyGate(tmp_path, threshold=0.55)
    r = gate.predict_for_upbit_market("KRW-ETH")
    assert r.allowed or r.status in ("skipped", "disabled", "no_model", "error")


def test_default_threshold() -> None:
    assert default_buy_threshold() == 0.55
    assert isinstance(ml_buy_gate_enabled(), bool)
