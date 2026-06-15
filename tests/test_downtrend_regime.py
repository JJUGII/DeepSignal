"""Phase2 4장세 레짐 판정 테스트."""

from __future__ import annotations

from deepsignal.crypto_trading.downtrend.regime import (
    Regime,
    classify_btc_regime,
    allows_new_long_by_default,
    ema,
    vwap,
)


def _bars(closes, vol=10.0):
    return [{"open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": vol} for c in closes]


def test_ema_vwap_basic():
    assert ema([1, 2, 3], 5) is None            # 표본 부족
    assert ema([10] * 30, 20) == 10.0           # 평탄 → 동일
    assert abs(vwap(_bars([100, 100, 100])) - 100.0) < 1e-6


def test_uptrend():
    closes = [100 + i * 0.5 for i in range(60)]  # 꾸준한 상승
    r = classify_btc_regime(_bars(closes), breadth=0.7)
    assert r == Regime.UP_TREND


def test_downtrend():
    closes = [200 - i * 0.5 for i in range(60)]  # 꾸준한 하락
    r = classify_btc_regime(_bars(closes), breadth=0.25)
    assert r == Regime.DOWN_TREND


def test_panic():
    closes = [200 - i * 0.2 for i in range(56)] + [188, 185, 181, 176]  # 막판 급락 -6%
    r = classify_btc_regime(_bars(closes), breadth=0.10)
    assert r == Regime.PANIC


def test_range_when_choppy():
    closes = [100 + (1 if i % 2 else -1) for i in range(60)]  # 횡보
    r = classify_btc_regime(_bars(closes), breadth=0.5)
    assert r == Regime.RANGE


def test_insufficient_data_is_range():
    assert classify_btc_regime(_bars([100, 101, 102]), breadth=0.5) == Regime.RANGE


def test_default_long_permission():
    assert allows_new_long_by_default(Regime.UP_TREND) is True
    assert allows_new_long_by_default(Regime.RANGE) is True
    assert allows_new_long_by_default(Regime.DOWN_TREND) is False
    assert allows_new_long_by_default(Regime.PANIC) is False
