from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from deepsignal.crypto_trading.crypto_holdings import format_holdings_console
from deepsignal.crypto_trading.crypto_recommendation import build_sell_recommendation
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig


def _real_cfg() -> UpbitConfig:
    return UpbitConfig(access_key="real-key-12345678", secret_key="real-secret-12345678", dry_run=False)


def _accounts_response(rows: list[dict]) -> MagicMock:
    resp = MagicMock(status_code=200)
    resp.json.return_value = rows
    resp.text = json.dumps(rows)
    return resp


def _ticker_response(market: str, price: float) -> MagicMock:
    resp = MagicMock(status_code=200)
    row = {
        "market": market,
        "trade_price": price,
        "signed_change_rate": 0.0,
        "acc_trade_price_24h": 1e9,
    }
    resp.json.return_value = [row]
    resp.text = json.dumps([row])
    return resp


def test_get_crypto_holdings_xrp_from_accounts() -> None:
    cfg = _real_cfg()

    def fake_request(method, url, **kwargs):
        if "/accounts" in url:
            return _accounts_response(
                [
                    {"currency": "KRW", "balance": "50000", "locked": "0", "avg_buy_price": "0"},
                    {
                        "currency": "XRP",
                        "balance": "4.99001996",
                        "locked": "0",
                        "avg_buy_price": "2004",
                    },
                ]
            )
        if "/ticker" in url:
            return _ticker_response("KRW-XRP", 2002.0)
        raise AssertionError(url)

    br = UpbitBroker(cfg, request_fn=fake_request)
    holdings = br.get_crypto_holdings()
    assert len(holdings) == 1
    h = holdings[0]
    assert h.currency == "XRP"
    assert h.market == "KRW-XRP"
    assert abs(h.total_quantity - 4.99001996) < 1e-6
    assert h.avg_buy_price == 2004.0
    assert h.current_price == 2002.0
    assert abs(h.pnl_pct - ((2002.0 - 2004.0) / 2004.0 * 100.0)) < 0.01
    assert h.valuation_krw == pytest.approx(4.99001996 * 2002.0, rel=1e-4)


def test_holdings_balance_plus_locked() -> None:
    cfg = _real_cfg()

    def fake_request(method, url, **kwargs):
        if "/accounts" in url:
            return _accounts_response(
                [
                    {
                        "currency": "XRP",
                        "balance": "0",
                        "locked": "4.99001996",
                        "avg_buy_price": "2004",
                    },
                ]
            )
        if "/ticker" in url:
            return _ticker_response("KRW-XRP", 2002.0)
        raise AssertionError(url)

    br = UpbitBroker(cfg, request_fn=fake_request)
    holdings = br.get_crypto_holdings()
    assert len(holdings) == 1
    assert holdings[0].total_quantity == pytest.approx(4.99001996)
    assert holdings[0].available == 0.0
    assert holdings[0].locked == pytest.approx(4.99001996)


def test_take_profit_0_3_triggers_sell() -> None:
    cfg = _real_cfg()

    def fake_request(method, url, **kwargs):
        if "/accounts" in url:
            return _accounts_response(
                [
                    {
                        "currency": "XRP",
                        "balance": "10",
                        "locked": "0",
                        "avg_buy_price": "2000",
                    },
                ]
            )
        if "/ticker" in url:
            return _ticker_response("KRW-XRP", 2006.0)
        raise AssertionError(url)

    br = UpbitBroker(cfg, request_fn=fake_request)
    rec = build_sell_recommendation(br, take_profit_pct=0.3, stop_loss_pct=-0.3)
    assert rec is not None
    assert rec.side == "sell"
    assert rec.pnl_pct >= 0.3
    assert rec.volume > 0
    assert rec.avg_buy_price == 2000.0


def test_stop_loss_0_3_triggers_sell() -> None:
    cfg = _real_cfg()

    def fake_request(method, url, **kwargs):
        if "/accounts" in url:
            return _accounts_response(
                [
                    {
                        "currency": "XRP",
                        "balance": "10",
                        "locked": "0",
                        "avg_buy_price": "2000",
                    },
                ]
            )
        if "/ticker" in url:
            return _ticker_response("KRW-XRP", 1994.0)
        raise AssertionError(url)

    br = UpbitBroker(cfg, request_fn=fake_request)
    rec = build_sell_recommendation(br, take_profit_pct=5.0, stop_loss_pct=-0.3)
    assert rec is not None
    assert rec.side == "sell"
    assert rec.pnl_pct <= -0.3


def test_format_holdings_console_xrp_line() -> None:
    cfg = _real_cfg()

    def fake_request(method, url, **kwargs):
        if "/accounts" in url:
            return _accounts_response(
                [
                    {
                        "currency": "XRP",
                        "balance": "4.99001996",
                        "locked": "0",
                        "avg_buy_price": "2004",
                    },
                ]
            )
        if "/ticker" in url:
            return _ticker_response("KRW-XRP", 2002.0)
        raise AssertionError(url)

    br = UpbitBroker(cfg, request_fn=fake_request)
    lines = format_holdings_console(br.get_crypto_holdings())
    text = "\n".join(lines)
    assert "Holdings:" in text
    assert "XRP" in text
    assert "리플" in text
    assert "4.99001996" in text
    assert "2,004" in text
    assert "2,002" in text
    assert "-0.10" in text or "-0.09" in text
