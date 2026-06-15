from __future__ import annotations

from deepsignal.crypto_trading.broker.selection import (
    crypto_broker_label,
    normalize_crypto_broker_name,
)


def test_normalize_crypto_broker_default(monkeypatch) -> None:
    monkeypatch.delenv("CRYPTO_BROKER", raising=False)
    assert normalize_crypto_broker_name() == "upbit"


def test_normalize_crypto_broker_bithumb(monkeypatch) -> None:
    monkeypatch.setenv("CRYPTO_BROKER", "bithumb")
    assert normalize_crypto_broker_name() == "bithumb"
    assert crypto_broker_label() == "Bithumb"
