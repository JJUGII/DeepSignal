from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from deepsignal.crypto_trading.crypto_market_data import mock_ticker
from deepsignal.crypto_trading.crypto_recommendation import build_crypto_recommendation
from deepsignal.crypto_trading.upbit_auth import build_upbit_jwt
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitBrokerError
from deepsignal.crypto_trading.upbit_config import UpbitConfig


def _dry_cfg() -> UpbitConfig:
    return UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True)


def test_jwt_has_three_segments() -> None:
    token = build_upbit_jwt("ak", "sk", query={"market": "KRW-BTC"})
    assert len(token.split(".")) == 3


def test_get_balances_mock() -> None:
    br = UpbitBroker(_dry_cfg())
    balances = br.get_balances()
    krw = next(b for b in balances if b.currency == "KRW")
    assert krw.balance >= 5000


def test_place_limit_buy_dry_run_blocks_post() -> None:
    br = UpbitBroker(_dry_cfg())
    result = br.place_limit_buy(market="KRW-BTC", krw_amount=10_000, execute=False)
    assert result.status == "UPBIT_DRY_RUN_BLOCKED"
    assert result.dry_run is True


def test_place_limit_buy_execute_mock_session() -> None:
    cfg = UpbitConfig(access_key="real-key-12345678", secret_key="real-secret-12345678", dry_run=False)
    session = MagicMock()
    ticker_resp = MagicMock(status_code=200, text=json.dumps([mock_ticker("KRW-BTC").__dict__]))
    # fix ticker response - use proper dict
    ticker_resp.json.return_value = [
        {
            "market": "KRW-BTC",
            "trade_price": 95_000_000,
            "signed_change_rate": 0.01,
            "acc_trade_price_24h": 1e9,
        }
    ]
    acct_resp = MagicMock(status_code=200, text="[]")
    acct_resp.json.return_value = [{"currency": "KRW", "balance": "100000", "locked": "0", "avg_buy_price": "0"}]
    order_resp = MagicMock(status_code=201, text='{"uuid":"u1","state":"wait"}')
    order_resp.json.return_value = {"uuid": "u1", "state": "wait"}

    def fake_request(method, url, **kwargs):
        if "/ticker" in url:
            return ticker_resp
        if "/accounts" in url:
            return acct_resp
        if "/orders" in url and method.upper() == "POST":
            return order_resp
        raise AssertionError(url)

    br = UpbitBroker(cfg, request_fn=fake_request)
    result = br.place_limit_buy(market="KRW-BTC", krw_amount=10_000, execute=True)
    assert result.uuid == "u1"
    assert result.dry_run is False


def test_validate_min_order() -> None:
    br = UpbitBroker(_dry_cfg())
    ok, errs = br.validate_limit_buy(market="KRW-BTC", krw_amount=1000, price=95_000_000)
    assert ok is False
    assert any("5,000" in e for e in errs)


def test_recommendation_picks_market() -> None:
    br = UpbitBroker(_dry_cfg())
    rec = build_crypto_recommendation(br, max_order_value=10_000)
    assert rec is not None
    assert rec.market.startswith("KRW-")
    assert rec.krw_amount >= 5000
