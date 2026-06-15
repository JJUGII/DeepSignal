"""Phase4 EV 게이트 + 장세별 점수 테스트."""

from __future__ import annotations

from deepsignal.crypto_trading.downtrend.ev_gate import (
    TradeStats,
    compute_trade_stats,
    expected_value_pct,
    ev_allows_buy,
    regime_min_score,
)


def test_compute_stats():
    s = compute_trade_stats([2.0, -1.0, 3.0, -1.0])
    assert s.n == 4
    assert s.p_win == 0.5
    assert abs(s.avg_win - 2.5) < 1e-9
    assert abs(s.avg_loss - 1.0) < 1e-9


def test_ev_formula():
    # P=0.5, win=2, loss=1, fee 0.1, slip 0.05 → 1.0 - 0.5 - 0.15 = +0.35
    ev = expected_value_pct(0.5, 2.0, 1.0)
    assert abs(ev - 0.35) < 1e-9
    # 실거래 실측(승률 28.9%, win 6.68, loss 2.15): mean 기반 EV는 +0.25%로 *양수*다
    # (소수 대박이 평균을 끌어올림). 그래서 EV만으론 못 거르고 승률 하한이 필요하다.
    ev_real = expected_value_pct(0.289, 6.68, 2.15)
    assert ev_real > 0   # mean 기반 EV의 함정을 명시


def test_ev_gate_allows_positive():
    stats = TradeStats(n=100, p_win=0.55, avg_win=1.0, avg_loss=0.6)
    ok, ev, _ = ev_allows_buy(stats)
    # 0.55*1.0 - 0.45*0.6 - 0.15 = 0.55-0.27-0.15 = +0.13 ≤ 0.15 → 차단
    assert ok is False
    stats2 = TradeStats(n=100, p_win=0.62, avg_win=1.0, avg_loss=0.5)
    ok2, ev2, _ = ev_allows_buy(stats2)
    # 0.62 - 0.19 - 0.15 = +0.28 > 0.15 → 허용
    assert ok2 is True and ev2 > 0.15


def test_ev_gate_blocks_low_samples():
    stats = TradeStats(n=10, p_win=0.9, avg_win=5.0, avg_loss=0.1)  # EV 높아도 표본 부족
    ok, _, reason = ev_allows_buy(stats, min_samples=30)
    assert ok is False and "표본" in reason


def test_ev_gate_blocks_real_losing():
    # 실거래 실측(승률 28.9%): mean EV는 +지만 승률 하한(0.45)에서 차단되어야 — 로터리 방어
    stats = TradeStats(n=200, p_win=0.289, avg_win=6.68, avg_loss=2.15)
    ok, ev, reason = ev_allows_buy(stats)
    assert ok is False
    assert ev > 0 and "승률" in reason   # EV는 양수지만 승률으로 차단


def test_regime_min_score():
    assert regime_min_score("DOWN_TREND") == 70.0
    assert regime_min_score("PANIC") == 75.0
    assert regime_min_score("UP_TREND") == 55.0
    assert regime_min_score("unknown") == 60.0
