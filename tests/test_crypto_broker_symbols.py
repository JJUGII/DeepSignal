from __future__ import annotations

import pytest

from deepsignal.crypto_trading.broker.symbols import (
    currency_from_market,
    normalize_market,
    to_bithumb_market,
    to_upbit_market,
)


def test_normalize_upbit_market() -> None:
    assert normalize_market("krw-btc") == "KRW-BTC"
    assert to_upbit_market("KRW-BTC") == "KRW-BTC"


def test_normalize_bithumb_market() -> None:
    assert normalize_market("BTC_KRW") == "KRW-BTC"
    assert to_bithumb_market("KRW-ETH") == "ETH_KRW"


def test_currency_from_market() -> None:
    assert currency_from_market("BTC_KRW") == "BTC"
