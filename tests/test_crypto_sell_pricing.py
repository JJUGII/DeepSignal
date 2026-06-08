"""Sell limit pricing — TP at +2%, no single-holding churn."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_recommendation import build_sell_recommendation
from deepsignal.crypto_trading.crypto_order_plan import build_plan_from_recommendation
from deepsignal.crypto_trading.crypto_sell_pricing import compute_sell_limit_price
from deepsignal.crypto_trading.upbit_broker import CryptoHolding, UpbitBroker, UpbitConfig


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def test_near_take_profit_limit_at_target_pct() -> None:
    h = CryptoHolding(
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
    px = compute_sell_limit_price(h, "near_take_profit", take_profit_pct=2.0, stop_loss_pct=-1.5)
    assert px == 2040.0


def test_single_holding_zero_pnl_no_forced_sell() -> None:
    br = _br()

    def one_coin():
        return [
            CryptoHolding(
                market="KRW-RVN",
                currency="RVN",
                balance=1000.0,
                locked=0.0,
                available=1000.0,
                avg_buy_price=8.0,
                current_price=8.0,
                valuation_krw=8000.0,
                pnl_pct=0.0,
                pnl_krw=0.0,
            )
        ]

    br.get_crypto_holdings = one_coin  # type: ignore[method-assign]
    assert build_sell_recommendation(br, take_profit_pct=2.0, stop_loss_pct=-1.5) is None


def test_near_take_profit_plan_uses_target_limit() -> None:
    br = _br()

    def holdings_near_tp():
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
    assert plan.limit_price == 2040.0
