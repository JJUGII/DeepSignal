from __future__ import annotations

import json
from unittest.mock import MagicMock

from deepsignal.crypto_trading.broker.bithumb.broker import BithumbBroker, BithumbBrokerError
from deepsignal.crypto_trading.broker.bithumb.config import BithumbConfig


def _dry_cfg() -> BithumbConfig:
    return BithumbConfig(api_key="dry-run-key", secret_key="dry-run-secret", dry_run=True)


def test_place_limit_buy_dry_run_blocks_post() -> None:
    br = BithumbBroker(_dry_cfg())
    result = br.place_limit_buy(market="KRW-BTC", krw_amount=10_000, execute=False)
    assert result.status == "BITHUMB_DRY_RUN_BLOCKED"
    assert result.dry_run is True


def test_validate_min_order() -> None:
    br = BithumbBroker(_dry_cfg())
    ok, errs = br.validate_limit_buy(market="KRW-BTC", krw_amount=1000, price=95_000_000)
    assert ok is False
    assert any("10,000" in e for e in errs)


def test_place_limit_buy_execute_mock_session() -> None:
    cfg = BithumbConfig(api_key="real-key-12345678", secret_key="real-secret-12345678", dry_run=False)

    def fake_request(method, url, **kwargs):
        if "/ticker" in url:
            resp = MagicMock(status_code=200, text="[]")
            resp.json.return_value = [
                {
                    "market": "KRW-BTC",
                    "trade_price": 95_000_000,
                    "signed_change_rate": 0.01,
                    "acc_trade_price_24h": 1e9,
                }
            ]
            return resp
        if "/accounts" in url:
            resp = MagicMock(status_code=200, text="[]")
            resp.json.return_value = [
                {"currency": "KRW", "balance": "100000", "locked": "0", "avg_buy_price": "0"},
            ]
            return resp
        if "/v2/orders" in url and method.upper() == "POST":
            resp = MagicMock(status_code=201, text="{}")
            resp.json.return_value = {"order_id": "bh-order-1", "state": "wait", "market": "KRW-BTC"}
            return resp
        raise AssertionError(f"unexpected request: {method} {url}")

    br = BithumbBroker(cfg, request_fn=fake_request)
    result = br.place_limit_buy(market="KRW-BTC", krw_amount=10_000, execute=True)
    assert result.uuid == "bh-order-1"
    assert result.dry_run is False


def test_cancel_order_demo() -> None:
    br = BithumbBroker(_dry_cfg())
    row = br.cancel_order("demo-order-1")
    assert row["state"] == "cancel"


def test_get_order_demo() -> None:
    br = BithumbBroker(_dry_cfg())
    row = br.get_order("demo-order-wait")
    assert row["state"] == "wait"


def test_paper_mode_blocks_execute_post() -> None:
    cfg = BithumbConfig(
        api_key="real-key-12345678",
        secret_key="real-secret-12345678",
        dry_run=False,
        paper_mode=True,
    )

    def fake_request(method, url, **kwargs):
        if "/ticker" in url:
            resp = MagicMock(status_code=200, text="[]")
            resp.json.return_value = [
                {
                    "market": "KRW-BTC",
                    "trade_price": 95_000_000,
                    "signed_change_rate": 0.01,
                    "acc_trade_price_24h": 1e9,
                }
            ]
            return resp
        if "/accounts" in url:
            resp = MagicMock(status_code=200, text="[]")
            resp.json.return_value = [
                {"currency": "KRW", "balance": "100000", "locked": "0", "avg_buy_price": "0"},
            ]
            return resp
        raise AssertionError(f"unexpected request: {method} {url}")

    br = BithumbBroker(cfg, request_fn=fake_request)
    result = br.place_limit_buy(market="KRW-BTC", krw_amount=10_000, price=95_000_000, execute=True)
    assert result.status == "CRYPTO_PAPER_MODE_BLOCKED"
    assert result.dry_run is True
