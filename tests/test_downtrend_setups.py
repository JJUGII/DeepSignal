"""Phase3 반등 셋업 3종 테스트."""

from __future__ import annotations

from deepsignal.crypto_trading.downtrend.setups import (
    SetupFeatures,
    capitulation_bounce,
    relative_strength_reclaim,
    vwap_reclaim_scalp,
    detect_setups,
)


def test_capitulation_bounce_fires():
    f = SetupFeatures(
        drop_3m_pct=-2.0, atr_1m_pct=1.0,        # -2.0 ≤ -1.2×1.0 ✓
        lower_wick_ratio=0.5,                     # ✓
        close=101.0, prev_candle_high=100.0,      # 직전고점 회복 ✓
        volume_ratio_1m=2.5,                      # ✓
        vwap_1m=100.5,                            # close>vwap ✓
    )
    assert capitulation_bounce(f) is True


def test_capitulation_fails_if_no_wick():
    f = SetupFeatures(drop_3m_pct=-2.0, atr_1m_pct=1.0, lower_wick_ratio=0.1,
                      close=101.0, prev_candle_high=100.0, volume_ratio_1m=2.5, vwap_1m=100.5)
    assert capitulation_bounce(f) is False


def test_capitulation_fails_if_not_dropped_enough():
    f = SetupFeatures(drop_3m_pct=-0.5, atr_1m_pct=1.0, lower_wick_ratio=0.5,
                      close=101.0, prev_candle_high=100.0, volume_ratio_1m=2.5, vwap_1m=100.5)
    assert capitulation_bounce(f) is False


def test_relative_strength_reclaim_fires():
    f = SetupFeatures(close=100.0, coin_ret_5m_pct=-0.2, btc_ret_5m_pct=-1.2,  # rs=+1.0 ✓
                      coin_vwap_5m=99.5, btc_breaking_new_low=False)
    assert relative_strength_reclaim(f) is True


def test_rs_fails_if_btc_new_low():
    f = SetupFeatures(close=100.0, coin_ret_5m_pct=-0.2, btc_ret_5m_pct=-1.2,
                      coin_vwap_5m=99.5, btc_breaking_new_low=True)
    assert relative_strength_reclaim(f) is False


def test_rs_fails_if_weak_relative():
    f = SetupFeatures(close=100.0, coin_ret_5m_pct=-1.0, btc_ret_5m_pct=-1.2,  # rs=+0.2 < 0.7
                      coin_vwap_5m=99.5, btc_breaking_new_low=False)
    assert relative_strength_reclaim(f) is False


def test_vwap_reclaim_fires():
    f = SetupFeatures(close=100.5, vwap_1m=100.0, was_below_vwap_recently=True, volume_ratio_1m=1.8)
    assert vwap_reclaim_scalp(f) is True


def test_vwap_reclaim_fails_if_never_below():
    f = SetupFeatures(close=100.5, vwap_1m=100.0, was_below_vwap_recently=False, volume_ratio_1m=1.8)
    assert vwap_reclaim_scalp(f) is False


def test_detect_setups_empty_when_nothing():
    assert detect_setups(SetupFeatures()) == []


def test_detect_setups_lists_fired():
    f = SetupFeatures(close=100.5, vwap_1m=100.0, was_below_vwap_recently=True, volume_ratio_1m=1.8)
    assert "vwap_reclaim_scalp" in detect_setups(f)
