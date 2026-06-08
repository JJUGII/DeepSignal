"""Tests for crypto execution quality gates."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from deepsignal.crypto_trading.crypto_execution_quality import (
    effective_min_order_krw,
    evaluate_pre_trade,
    floor_order_krw,
    should_block_entry_by_execution_quality,
)
from deepsignal.crypto_trading.upbit_broker import UpbitTicker


@dataclass
class _StubBroker:
    krw: float = 500_000.0
    price: float = 1000.0
    vol24: float = 2_000_000_000.0
    change_rate: float = 0.01

    def get_krw_available(self) -> float:
        return self.krw

    def get_ticker(self, market: str) -> UpbitTicker:
        return UpbitTicker(
            market=market,
            trade_price=self.price,
            signed_change_rate=self.change_rate,
            acc_trade_price_24h=self.vol24,
        )


def test_effective_min_order_is_at_least_10k():
    assert effective_min_order_krw() >= 10_000.0


def test_floor_order_raises_small_amount():
    effective, notes = floor_order_krw(9_264, available_krw=500_000)
    assert effective >= 10_000.0
    assert any("상향" in n for n in notes)


def test_floor_blocks_when_cannot_afford_min():
    effective, _ = floor_order_krw(9_000, available_krw=8_000)
    assert effective == 0.0


def test_buy_passes_with_default_tp_sl():
    broker = _StubBroker()
    report = evaluate_pre_trade(
        broker,
        market="KRW-BTC",
        side="buy",
        order_krw=50_000,
        take_profit_pct=2.0,
        stop_loss_pct=-1.5,
    )
    assert report.allowed
    assert report.effective_order_krw >= effective_min_order_krw()
    assert report.net_rr_after_fees >= 0.92
    assert report.spread_bps <= 45.0
    assert not should_block_entry_by_execution_quality(report)


def test_buy_blocked_on_tiny_unaffordable_order():
    broker = _StubBroker(krw=5_000)
    report = evaluate_pre_trade(broker, market="KRW-BTC", side="buy", order_krw=9_264)
    assert not report.allowed
    assert should_block_entry_by_execution_quality(report)


def test_wide_spread_blocks_entry():
    broker = _StubBroker(vol24=50_000_000, change_rate=0.25)
    report = evaluate_pre_trade(
        broker,
        market="KRW-ILLQ",
        side="buy",
        order_krw=20_000,
        max_spread_bps=5.0,
    )
    assert not report.allowed
