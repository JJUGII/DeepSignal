"""A1 KRX 호가단위 정렬 테스트 — APBK0506 거부 재발 방지."""

from __future__ import annotations

import pytest

from deepsignal.live_trading.broker.krx_tick import krx_tick_size, round_krx_price


def test_tick_table():
    assert krx_tick_size(1_500) == 1
    assert krx_tick_size(3_000) == 5
    assert krx_tick_size(12_000) == 10
    assert krx_tick_size(35_000) == 50
    assert krx_tick_size(120_000) == 100
    assert krx_tick_size(293_000) == 500   # 20만~50만 → 500원
    assert krx_tick_size(700_000) == 1_000


def test_actual_failed_prices_become_valid():
    # 실제 108회 거부된 가격들 — 정렬 후 호가단위 배수여야 한다.
    for px in (293_028, 336_750):
        sell = round_krx_price(px, side="SELL")
        buy = round_krx_price(px, side="BUY")
        assert sell % 500 == 0, f"SELL {sell} not on 500 grid"
        assert buy % 500 == 0, f"BUY {buy} not on 500 grid"
        assert sell <= px <= buy  # 매도 내림 ≤ 원가 ≤ 매수 올림


def test_specific_snaps():
    assert round_krx_price(293_028, side="SELL") == 293_000
    assert round_krx_price(293_028, side="BUY") == 293_500
    assert round_krx_price(336_750, side="SELL") == 336_500
    assert round_krx_price(336_750, side="BUY") == 337_000


@pytest.mark.parametrize("px", [1_499, 2_001, 4_999, 19_999, 49_999, 199_999, 499_999, 1_234_567])
def test_all_ranges_produce_valid_tick(px):
    for side in ("BUY", "SELL", ""):
        out = round_krx_price(px, side=side)
        assert out % krx_tick_size(out) == 0
        assert out > 0


def test_zero_and_negative():
    assert round_krx_price(0) == 0
    assert round_krx_price(-100) == 0
