"""Crypto sell buffers, Upbit 429 retry, batch tickers, min_volume_ratio."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from deepsignal.crypto_trading.crypto_order_plan import build_plan_from_recommendation
from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig
from deepsignal.crypto_trading.crypto_recommendation import build_crypto_recommendation, build_sell_recommendation
from deepsignal.crypto_trading.crypto_sell_triggers import classify_crypto_sell_trigger
from deepsignal.crypto_trading.crypto_telegram_flow import format_approval_message
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig, UpbitTicker


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def test_classify_near_take_profit() -> None:
    assert classify_crypto_sell_trigger(1.98, take_profit_pct=2.0, take_profit_buffer_pct=0.05) == "near_take_profit"
    assert classify_crypto_sell_trigger(2.0, take_profit_pct=2.0, take_profit_buffer_pct=0.05) == "take_profit"
    assert classify_crypto_sell_trigger(1.90, take_profit_pct=2.0, take_profit_buffer_pct=0.05) is None


def test_classify_near_stop_loss() -> None:
    assert classify_crypto_sell_trigger(-1.47, stop_loss_pct=-1.5, stop_loss_buffer_pct=0.05) == "near_stop_loss"
    assert classify_crypto_sell_trigger(-1.5, stop_loss_pct=-1.5, stop_loss_buffer_pct=0.05) == "stop_loss"
    assert classify_crypto_sell_trigger(-1.40, stop_loss_pct=-1.5, stop_loss_buffer_pct=0.05) is None


def test_near_take_profit_triggers_sell_recommendation() -> None:
    br = _br()

    def holdings_near_tp():
        from deepsignal.crypto_trading.upbit_broker import CryptoHolding

        return [
            CryptoHolding(
                market="KRW-BTC",
                currency="BTC",
                balance=0.001,
                locked=0.0,
                available=0.001,
                avg_buy_price=90_000_000.0,
                current_price=91_790_000.0,
                valuation_krw=91_790.0,
                pnl_pct=1.99,
                pnl_krw=1_790.0,
            )
        ]

    br.get_crypto_holdings = holdings_near_tp  # type: ignore[method-assign]
    rec = build_sell_recommendation(
        br,
        take_profit_pct=2.0,
        take_profit_buffer_pct=0.05,
        stop_loss_pct=-1.5,
    )
    assert rec is not None
    assert rec.sell_trigger == "near_take_profit"
    assert rec.pnl_pct == pytest.approx(1.99)


def test_near_take_profit_telegram_message() -> None:
    br = _br()

    def holdings_near_tp():
        from deepsignal.crypto_trading.upbit_broker import CryptoHolding

        return [
            CryptoHolding(
                market="KRW-XRP",
                currency="XRP",
                balance=10.0,
                locked=0.0,
                available=10.0,
                avg_buy_price=2000.0,
                current_price=2039.6,
                valuation_krw=20_396.0,
                pnl_pct=1.98,
                pnl_krw=396.0,
            )
        ]

    br.get_crypto_holdings = holdings_near_tp  # type: ignore[method-assign]
    rec = build_sell_recommendation(br, take_profit_pct=2.0, take_profit_buffer_pct=0.05)
    assert rec is not None
    plan = build_plan_from_recommendation(rec)
    msg = format_approval_message(plan)
    assert "익절 근접 매도 승인" in msg
    assert "+1.98%" in msg
    assert "+2.00%" in msg
    assert "거의 도달" in msg


def test_upbit_429_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def fake_request(method: str, url: str, **kwargs: object) -> MagicMock:
        calls["n"] += 1
        resp = MagicMock()
        if calls["n"] <= 2:
            resp.status_code = 429
            resp.text = '{"error":{"message":"too_many_requests"}}'
        else:
            resp.status_code = 200
            resp.text = json_payload()
            resp.json.return_value = [
                {
                    "market": "KRW-BTC",
                    "trade_price": 95000000,
                    "signed_change_rate": 0.01,
                    "acc_trade_price_24h": 1e11,
                }
            ]
        return resp

    def json_payload() -> str:
        return "[]"

    cfg = UpbitConfig(access_key="live-key", secret_key="live-secret", dry_run=False)
    br = UpbitBroker(cfg, request_fn=fake_request)
    ticker = br.get_ticker("KRW-BTC")
    assert ticker.market == "KRW-BTC"
    assert calls["n"] == 3


def test_get_tickers_batch_single_request() -> None:
    seen: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> MagicMock:
        seen["params"] = kwargs.get("params")
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "[]"
        resp.json.return_value = [
            {
                "market": "KRW-BTC",
                "trade_price": 1.0,
                "signed_change_rate": 0.01,
                "acc_trade_price_24h": 1e9,
            },
            {
                "market": "KRW-ETH",
                "trade_price": 2.0,
                "signed_change_rate": 0.02,
                "acc_trade_price_24h": 2e9,
            },
            {
                "market": "KRW-XRP",
                "trade_price": 3.0,
                "signed_change_rate": 0.03,
                "acc_trade_price_24h": 3e9,
            },
        ]
        return resp

    cfg = UpbitConfig(access_key="live-key", secret_key="live-secret", dry_run=False)
    br = UpbitBroker(cfg, request_fn=fake_request)
    out = br.get_tickers(["KRW-BTC", "KRW-ETH", "KRW-XRP"])
    assert set(out.keys()) == {"KRW-BTC", "KRW-ETH", "KRW-XRP"}
    assert seen["params"] == {"markets": "KRW-BTC,KRW-ETH,KRW-XRP"}


def test_min_volume_ratio_cli_relaxes_buy_block() -> None:
    br = _br()
    flat_candles = [
        {
            "trade_price": 100.0,
            "high_price": 101.0,
            "low_price": 99.0,
            "candle_acc_trade_volume": 10.0,
        }
    ] * 30
    br.get_daily_candles = lambda market, count=30: flat_candles[-count:]  # type: ignore[method-assign]
    ticker = UpbitTicker(
        market="KRW-BTC",
        trade_price=100.0,
        signed_change_rate=0.01,
        acc_trade_price_24h=730.0,
    )
    from deepsignal.crypto_trading.crypto_quality import evaluate_crypto_buy_quality

    ok_strict, reason_strict, _, diag = evaluate_crypto_buy_quality(
        br, "KRW-BTC", ticker, cfg=CryptoBuyQualityConfig(min_volume_ratio=0.8, max_rsi=100)
    )
    ok_relaxed, _, _, _ = evaluate_crypto_buy_quality(
        br, "KRW-BTC", ticker, cfg=CryptoBuyQualityConfig(min_volume_ratio=0.7, max_rsi=100)
    )
    assert ok_strict is False
    assert "거래량" in reason_strict
    assert diag.get("volume_ratio") == pytest.approx(0.73, rel=1e-3)
    assert ok_relaxed is True
