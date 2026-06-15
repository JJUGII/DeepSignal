"""Phase5 검증 게이트 + 오케스트레이터 테스트."""

from __future__ import annotations

from deepsignal.crypto_trading.downtrend.validation import (
    validate_strategy,
    max_drawdown_pct,
    max_consecutive_losses,
)
from deepsignal.crypto_trading.downtrend.engine import (
    decide_downtrend_buy,
    downtrend_engine_enabled,
)
from deepsignal.crypto_trading.downtrend.ev_gate import TradeStats
from deepsignal.crypto_trading.downtrend.regime import Regime
from deepsignal.crypto_trading.downtrend.setups import SetupFeatures


# ── validation ──────────────────────────────────────────────
def test_mdd_and_consec():
    assert max_drawdown_pct([1, -2, -3, 5]) < 0
    assert max_consecutive_losses([1, -1, -1, -1, 2, -1]) == 3


def test_validation_blocks_insufficient():
    trades = [{"regime": "DOWN_TREND", "net_return_pct": 0.3, "is_shadow": False} for _ in range(10)]
    r = validate_strategy(trades)
    assert r.deploy is False
    assert any("표본" in x for x in r.reasons)


def test_validation_passes_good_strategy():
    # 250 하락장 거래, 승률 높고 net 양수, + shadow 120 양수
    win = {"regime": "DOWN_TREND", "net_return_pct": 0.5, "is_shadow": False}
    loss = {"regime": "DOWN_TREND", "net_return_pct": -0.3, "is_shadow": False}
    trades = ([win, win, loss] * 84)  # 252건, 2승1패
    trades += [{"regime": "DOWN_TREND", "net_return_pct": 0.4, "is_shadow": True} for _ in range(120)]
    r = validate_strategy(trades)
    assert r.deploy is True, r.reasons
    assert r.profit_factor >= 1.2


# ── orchestrator ────────────────────────────────────────────
_GOOD_SETUP = SetupFeatures(
    drop_3m_pct=-2.0, atr_1m_pct=1.0, lower_wick_ratio=0.5,
    close=101.0, prev_candle_high=100.0, volume_ratio_1m=2.5, vwap_1m=100.5,
)
_GOOD_STATS = TradeStats(n=100, p_win=0.60, avg_win=1.0, avg_loss=0.5)  # EV +0.25, 승률>0.45


def test_defer_on_uptrend():
    d = decide_downtrend_buy(Regime.UP_TREND, _GOOD_SETUP, _GOOD_STATS, 90)
    assert d.defer is True and d.allow is False


def test_panic_blocks_by_default():
    d = decide_downtrend_buy(Regime.PANIC, _GOOD_SETUP, _GOOD_STATS, 90)
    assert d.allow is False and "PANIC" in d.reason


def test_downtrend_allows_full_setup():
    d = decide_downtrend_buy(Regime.DOWN_TREND, _GOOD_SETUP, _GOOD_STATS, 90)
    assert d.allow is True
    assert "capitulation_bounce" in d.setups


def test_downtrend_blocks_no_setup():
    d = decide_downtrend_buy(Regime.DOWN_TREND, SetupFeatures(), _GOOD_STATS, 90)
    assert d.allow is False and "셋업 미발화" in d.reason


def test_downtrend_blocks_low_score():
    d = decide_downtrend_buy(Regime.DOWN_TREND, _GOOD_SETUP, _GOOD_STATS, 50)  # < 70
    assert d.allow is False and "문턱" in d.reason


def test_downtrend_blocks_bad_ev():
    bad = TradeStats(n=200, p_win=0.289, avg_win=6.68, avg_loss=2.15)  # 승률 하한 미달
    d = decide_downtrend_buy(Regime.DOWN_TREND, _GOOD_SETUP, bad, 90)
    assert d.allow is False and "EV" in d.reason


def test_engine_disabled_by_default():
    assert downtrend_engine_enabled({}) is False
    assert downtrend_engine_enabled({"DOWNTREND_ENGINE_ENABLED": "true"}) is True
