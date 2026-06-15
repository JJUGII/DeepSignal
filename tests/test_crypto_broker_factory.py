from __future__ import annotations

from deepsignal.crypto_trading.broker.bithumb.broker import BithumbBroker
from deepsignal.crypto_trading.broker.bithumb.config import BithumbConfig
from deepsignal.crypto_trading.broker.factory import load_crypto_broker
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig


def test_factory_upbit() -> None:
    br = load_crypto_broker("upbit")
    assert isinstance(br, UpbitBroker)
    assert br.exchange_id == "upbit"


def test_factory_bithumb() -> None:
    br = load_crypto_broker("bithumb")
    assert isinstance(br, BithumbBroker)
    assert br.exchange_id == "bithumb"


def test_bithumb_demo_balances() -> None:
    br = BithumbBroker(BithumbConfig(api_key="demo-key", secret_key="demo-secret", dry_run=True))
    krw = br.get_krw_available()
    assert krw >= 0
    t = br.get_ticker("KRW-BTC")
    assert t.market == "KRW-BTC"
    assert t.trade_price > 0


def test_upbit_still_works() -> None:
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    assert br.exchange_id == "upbit"
    assert br.get_krw_available() >= 0
