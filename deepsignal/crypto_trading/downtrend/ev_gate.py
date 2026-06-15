"""[Phase4] 기대값(EV) 게이트 + 장세별 점수 기준.

핵심: 매수는 수수료·슬리피지 차감 후에도 기대값이 양(+)일 때만. 음수 EV 신호를 원천 차단.
P(win)·avg_win·avg_loss는 ML이 아니라 **셋업별 실측 통계**로 추정한다(ML 모델 공백 상태에서
0.58 임계는 전량 차단을 의미 — 실측 승률이 더 정직하다).

EV = P·avg_win − (1−P)·avg_loss − fee − slippage   (전부 % 단위, avg_loss·fee·slip는 양수)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# 업비트 KRW 일반주문 0.05% × 왕복 = 0.10%
DEFAULT_FEE_PCT = 0.10
DEFAULT_SLIPPAGE_PCT = 0.05
DEFAULT_MIN_EV_PCT = 0.15


@dataclass
class TradeStats:
    n: int
    p_win: float       # 승률 (0~1)
    avg_win: float     # 이긴 거래 평균 수익 % (양수)
    avg_loss: float    # 진 거래 평균 손실 크기 % (양수)

    @property
    def usable(self) -> bool:
        return self.n >= 1 and self.avg_win > 0 and self.avg_loss > 0


def compute_trade_stats(returns_pct: Sequence[float]) -> TradeStats:
    """수익률(%) 목록 → 통계. (순수 함수)"""
    rs = [float(r) for r in returns_pct]
    n = len(rs)
    if n == 0:
        return TradeStats(0, 0.0, 0.0, 0.0)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    p_win = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    return TradeStats(n=n, p_win=p_win, avg_win=avg_win, avg_loss=avg_loss)


def expected_value_pct(
    p_win: float,
    avg_win: float,
    avg_loss: float,
    *,
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
) -> float:
    """1거래 기대값(%). avg_loss·fee·slippage는 양수 크기로 입력."""
    p = max(0.0, min(1.0, float(p_win)))
    return p * float(avg_win) - (1.0 - p) * abs(float(avg_loss)) - abs(fee_pct) - abs(slippage_pct)


def ev_allows_buy(
    stats: TradeStats,
    *,
    min_ev_pct: float = DEFAULT_MIN_EV_PCT,
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
    min_samples: int = 30,
    min_win_rate: float = 0.45,
) -> tuple[bool, float, str]:
    """실측 통계 기반 EV 게이트. (허용, EV%, 사유).

    3중 조건 (모두 충족해야 허용):
    1. 표본 ≥ min_samples (통계 신뢰)
    2. 승률 ≥ min_win_rate — **로터리 분포 방어**. mean 기반 EV는 소수 대박(+56% 등)에
       끌려 양수가 될 수 있으나 실제론 잃는다(실측 승률 28.9%·net 음수가 그 예). 절대
       승률 하한으로 '대박 의존' 전략을 차단한다.
    3. EV > min_ev_pct (수수료·슬리피지 차감 후 기대값 양수)
    """
    if not stats.usable or stats.n < min_samples:
        return (False, 0.0, f"표본 부족/무효({stats.n}<{min_samples}) — 보수적 차단")
    ev = expected_value_pct(stats.p_win, stats.avg_win, stats.avg_loss,
                            fee_pct=fee_pct, slippage_pct=slippage_pct)
    if stats.p_win < min_win_rate:
        return (False, ev, f"승률 {stats.p_win:.2f} < {min_win_rate} — 로터리 분포 차단")
    if ev > min_ev_pct:
        return (True, ev, f"EV {ev:+.3f}% > {min_ev_pct}% · 승률 {stats.p_win:.2f}")
    return (False, ev, f"EV {ev:+.3f}% ≤ {min_ev_pct}% — 차단")


def setup_stats_from_trades(
    db_path: str | Path,
    *,
    setup: str | None = None,
    include_paper: bool = True,
) -> TradeStats:
    """crypto_trades.db 청산 거래에서 (셋업별) 실측 통계. 셋업 태그는 features_snapshot에 있으면 필터.

    setup이 None이면 전체. DB/컬럼 없으면 빈 통계(→ EV 게이트가 보수적으로 차단).
    """
    import sqlite3

    p = Path(db_path)
    if not p.is_file():
        return TradeStats(0, 0.0, 0.0, 0.0)
    where = ["exit_price IS NOT NULL", "actual_return IS NOT NULL"]
    params: list = []
    if not include_paper:
        where.append("paper = 0")
    if setup:
        where.append("features_snapshot LIKE ?")
        params.append(f"%{setup}%")
    try:
        conn = sqlite3.connect(str(p))
        try:
            rows = conn.execute(
                f"SELECT actual_return FROM crypto_trades WHERE {' AND '.join(where)}",
                params,
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return TradeStats(0, 0.0, 0.0, 0.0)
    return compute_trade_stats([float(r[0]) * 100.0 for r in rows if r and r[0] is not None])


# ── 장세별 진입 점수 기준 (C2) ──────────────────────────────────────────
_REGIME_MIN_SCORE = {
    "UP_TREND": 55.0,
    "RANGE": 60.0,
    "DOWN_TREND": 70.0,
    "PANIC": 75.0,
}


def regime_min_score(regime: str) -> float:
    """레짐별 매수 점수 문턱. 하락장일수록 높임(거의 안 산다)."""
    return _REGIME_MIN_SCORE.get(str(regime), 60.0)
