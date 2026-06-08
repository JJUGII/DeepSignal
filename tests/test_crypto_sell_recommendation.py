from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from deepsignal.crypto_trading.crypto_order_plan import build_plan_from_recommendation
from deepsignal.crypto_trading.crypto_recommendation import (
    build_daily_crypto_recommendation,
    build_sell_recommendation,
)
from deepsignal.crypto_trading.crypto_telegram_flow import (
    execute_approved_crypto_order,
    format_approval_message,
)
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def test_holdings_profit_triggers_sell() -> None:
    br = _br()
    rec = build_sell_recommendation(br, take_profit_pct=2.0, stop_loss_pct=-1.5)
    assert rec is not None
    assert rec.side == "sell"
    assert rec.market == "KRW-XRP"
    assert rec.pnl_pct >= 2.0


def test_no_holdings_falls_back_to_buy() -> None:
    br = _br()
    mp = pytest.MonkeyPatch()
    mp.setattr(br, "get_crypto_holdings", lambda: [])
    rec = build_daily_crypto_recommendation(br, take_profit_pct=99.0)
    mp.undo()
    assert rec is not None
    assert rec.side == "buy"


def test_min_order_blocks_sell() -> None:
    br = _br()

    def tiny_holdings():
        from deepsignal.crypto_trading.upbit_broker import CryptoHolding

        return [
            CryptoHolding(
                market="KRW-XRP",
                currency="XRP",
                balance=0.001,
                locked=0,
                available=0.001,
                avg_buy_price=2000,
                current_price=2050,
                valuation_krw=2.05,
                pnl_pct=5.0,
                pnl_krw=0.05,
            )
        ]

    mp = pytest.MonkeyPatch()
    mp.setattr(br, "get_crypto_holdings", tiny_holdings)
    assert build_sell_recommendation(br, take_profit_pct=2.0) is None
    mp.undo()


def test_stop_loss_sell() -> None:
    br = _br()

    def loss_holdings():
        from deepsignal.crypto_trading.upbit_broker import CryptoHolding

        return [
            CryptoHolding(
                market="KRW-XRP",
                currency="XRP",
                balance=10,
                locked=0,
                available=10,
                avg_buy_price=2000,
                current_price=1960,
                valuation_krw=19_600,
                pnl_pct=-2.0,
                pnl_krw=-400,
            )
        ]

    mp = pytest.MonkeyPatch()
    mp.setattr(br, "get_crypto_holdings", loss_holdings)
    rec = build_sell_recommendation(br, take_profit_pct=2.0, stop_loss_pct=-1.5)
    mp.undo()
    assert rec is not None
    assert rec.side == "sell"
    assert rec.pnl_pct <= -1.5


def test_sell_plan_has_volume() -> None:
    br = _br()
    rec = build_sell_recommendation(br, take_profit_pct=2.0)
    assert rec
    plan = build_plan_from_recommendation(rec)
    assert plan.side == "sell"
    assert plan.volume > 0


def test_sell_limit_dry_run() -> None:
    br = _br()
    result = br.sell_limit("KRW-XRP", 4.99, 2050, execute=False)
    assert result.side == "ask"
    assert result.status == "UPBIT_DRY_RUN_BLOCKED"


def test_telegram_sell_message() -> None:
    br = _br()
    rec = build_sell_recommendation(br, take_profit_pct=2.0)
    plan = build_plan_from_recommendation(rec)
    text = format_approval_message(plan)
    assert "매도" in text
    assert "보유수량" in text
    assert "수익률" in text


def test_execute_sell_dry_run() -> None:
    br = _br()
    rec = build_sell_recommendation(br, take_profit_pct=2.0)
    plan = build_plan_from_recommendation(rec)
    result = execute_approved_crypto_order(br, plan, execute=True)
    assert result.side == "ask"


def test_sell_limit_execute_mock() -> None:
    cfg = UpbitConfig(access_key="real-key-12345678", secret_key="real-secret-12345678", dry_run=False)

    def fake_request(method, url, **kwargs):
        resp = MagicMock(status_code=201, text="{}")
        if "/ticker" in url:
            resp.json.return_value = [
                {
                    "market": "KRW-XRP",
                    "trade_price": 2050,
                    "signed_change_rate": 0.01,
                    "acc_trade_price_24h": 1e9,
                }
            ]
        elif "/accounts" in url:
            resp.json.return_value = [
                {"currency": "XRP", "balance": "10", "locked": "0", "avg_buy_price": "2000"},
                {"currency": "KRW", "balance": "100000", "locked": "0", "avg_buy_price": "0"},
            ]
        elif "/orders" in url and method.upper() == "POST":
            resp.json.return_value = {"uuid": "sell-1", "state": "wait"}
        return resp

    br = UpbitBroker(cfg, request_fn=fake_request)
    out = br.place_limit_sell(market="KRW-XRP", volume=4.99, price=2050, execute=True)
    assert out.uuid == "sell-1"
    assert out.side == "ask"
