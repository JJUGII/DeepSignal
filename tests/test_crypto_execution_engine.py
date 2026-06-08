"""Execution engine — orderbook, Kelly, dynamic exits."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_execution_engine import (
    ExecutionEngineConfig,
    check_orderbook_for_buy,
    compute_entry_limit_price,
    kelly_fraction,
    kelly_order_krw,
    limit_price_bid_plus_tick,
    load_execution_positions,
    record_position_entry,
    update_peak_price,
    CryptoExecutionEngine,
    execution_engine_enabled,
)
from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig


def test_kelly_fraction_positive_edge() -> None:
    f = kelly_fraction(0.6, take_profit_pct=2.0, stop_loss_pct=-1.5, max_fraction=0.05)
    assert 0 < f <= 0.05


def test_kelly_fraction_no_edge() -> None:
    assert kelly_fraction(0.3, take_profit_pct=2.0, stop_loss_pct=-1.5) == 0.0


def test_orderbook_check_passes_dry_run() -> None:
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    ob = check_orderbook_for_buy(br, "KRW-BTC")
    assert ob.allowed
    assert ob.spread_pct < 0.15
    px = compute_entry_limit_price(ob)
    assert px > 0


def test_orderbook_blocks_wide_spread() -> None:
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))

    def fake_ob(_market: str, *, levels: int = 5):
        return {
            "market": "KRW-BTC",
            "orderbook_units": [
                {"bid_price": 100.0, "ask_price": 100.5, "bid_size": 1.0, "ask_size": 10.0}
            ],
        }

    br.get_orderbook = fake_ob  # type: ignore[method-assign]
    ob = check_orderbook_for_buy(br, "KRW-BTC", max_spread_pct=0.01, min_bid_ask_ratio=1.5)
    assert not ob.allowed


def test_buy_dry_run_engine() -> None:
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    plan = CryptoOrderPlan(
        market="KRW-ETH",
        krw_amount=15_000,
        limit_price=3_500_000,
        take_profit_pct=2.0,
        stop_loss_pct=-1.5,
        score_breakdown={"win_probability": 0.58},
    )
    state: dict = {}
    engine = CryptoExecutionEngine(br, cfg=ExecutionEngineConfig(buy_min_win_prob=0.55))
    out = engine.execute_buy(plan, execute=True, total_portfolio_krw=1_000_000, runner_state=state)
    assert out.success
    assert out.order is not None
    assert "KRW-ETH" in load_execution_positions(state)


def test_trailing_exit() -> None:
    from types import SimpleNamespace

    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    state: dict = {}
    record_position_entry(state, market="KRW-XRP", entry_price=1000.0)
    update_peak_price(state, market="KRW-XRP", current_price=1010.0)
    holding = SimpleNamespace(
        market="KRW-XRP",
        current_price=1000.0,
        pnl_pct=0.0,
        avg_buy_price=990.0,
        available=10.0,
        valuation_krw=10_000.0,
    )
    engine = CryptoExecutionEngine(br, cfg=ExecutionEngineConfig(trailing_stop_pct=0.8))
    dec = engine.evaluate_exit(holding, runner_state=state, static_trigger=None)
    assert dec is not None
    assert dec.reason == "trailing_stop"


def test_execution_engine_enabled_default() -> None:
    assert execution_engine_enabled() is True


def test_bid_plus_tick() -> None:
    assert limit_price_bid_plus_tick(10_000) >= 10_001
