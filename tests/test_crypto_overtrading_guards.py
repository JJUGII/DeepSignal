"""Overtrading guard unit tests."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_overtrading_guards import (
    OvertradingGuardConfig,
    check_buy_allowed,
    excluded_markets_for_buy,
    record_buy_in_state,
    record_sell_in_state,
    sell_blocked_by_min_hold,
)
from deepsignal.live_trading.time_utils import now_kst


def test_rebuy_cooldown_excludes_market():
    state: dict = {}
    cfg = OvertradingGuardConfig(rebuy_cooldown_minutes=20, post_sell_reentry_cooldown_minutes=0)
    record_buy_in_state(state, market="KRW-ERA", krw_amount=10_000)
    assert "KRW-ERA" in excluded_markets_for_buy(state, cfg)


def test_post_sell_blocks_reentry():
    state: dict = {}
    cfg = OvertradingGuardConfig(rebuy_cooldown_minutes=0, post_sell_reentry_cooldown_minutes=15)
    record_sell_in_state(state, market="KRW-MMT")
    ok, reason = check_buy_allowed(
        state,
        market="KRW-MMT",
        order_krw=10_000,
        total_portfolio_krw=500_000,
        cfg=cfg,
    )
    assert not ok
    assert "post_sell" in reason or "cooldown" in reason


def test_hourly_buy_cap():
    state: dict = {}
    cfg = OvertradingGuardConfig(
        rebuy_cooldown_minutes=0,
        post_sell_reentry_cooldown_minutes=0,
        max_buy_per_market_per_hour=2,
    )
    record_buy_in_state(state, market="KRW-BTC", krw_amount=10_000)
    record_buy_in_state(state, market="KRW-BTC", krw_amount=10_000)
    ok, reason = check_buy_allowed(
        state,
        market="KRW-BTC",
        order_krw=10_000,
        total_portfolio_krw=500_000,
        cfg=cfg,
    )
    assert not ok
    assert "hourly" in reason


def test_min_hold_blocks_near_tp_sell():
    state: dict = {}
    record_buy_in_state(state, market="KRW-ERA", krw_amount=10_000)
    blocked, reason = sell_blocked_by_min_hold(
        state,
        market="KRW-ERA",
        sell_trigger="near_take_profit",
        cfg=OvertradingGuardConfig(min_hold_minutes_before_sell=5),
    )
    assert blocked
    assert "min_hold" in reason
