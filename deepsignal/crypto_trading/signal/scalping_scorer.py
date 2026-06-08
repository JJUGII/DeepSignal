"""GSQS — Global Scalper Quant Score (단타 전용 100점 스코어).

점수 구조:
    A. trend_score     * 0.15  (내부 20pt)  — 추세 + 고점돌파 + 상대강도
    B. volume_score    * 0.15  (내부 15pt)  — 거래량
    C. orderbook_score * 0.20  (내부 20pt)  — 호가창
    D. tradeflow_score * 0.20  (내부 23pt)  — 체결강도 + 체결가속도
    E. futures_score   * 0.15  (내부 15pt)  — 선물수급
    F. risk_score      * 0.10  (내부 10pt)  — 변동성/리스크
    G. market_score    * 0.05  (내부  5pt)  — 시장상태
    총계: 100점  (각 서브스코어는 0~100 정규화 후 가중 합산)

GATS 적용 추가 항목:
    A. breakout_score        — 최근 20봉 최고가 돌파 강도 (+3pt)
    A. relative_strength_rank — 전체 심볼 모멘텀 순위 (+2pt)
    D. trade_acceleration    — 1분 체결수 vs 5분 평균 가속도 (+3pt)
    E. long_short_ratio      — 글로벌 L/S 비율 (피처 벡터 수정)

판정:
    score >= 80   STRONG_BUY    (강한 단타 후보)
    score 70~79   BUY_CANDIDATE (단타 후보)
    score 60~69   WATCH         (관망)
    score <  60   NO_TRADE      (진입 금지)

하드 블록 (어느 하나라도 충족 시 score=0):
    spread_bps > 8.0            스프레드 과대 (0.08%)
    ob_depth_1pct < 0.20        OB 유동성 부족
    btc_ret_5m < -0.005         BTC 5분 급락 (-0.5%)
    atr_14_1m_pct > 3.0         변동성 폭발
    fake_pump_detected          가짜 펌핑 (거래량↑ + 위꼬리↑ + CVD↓)
    divergence_detected         가격↑ + CVD↓ (분기 신호)
    funding_rate > 0.03         펀딩비 과열
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# ── 가중치 ─────────────────────────────────────────────────────────
_WEIGHTS: dict[str, float] = {
    "trend":     0.15,
    "volume":    0.15,
    "orderbook": 0.20,
    "tradeflow": 0.20,
    "futures":   0.15,
    "risk":      0.10,
    "market":    0.05,
}

# ── 하드 블록 임계값 ────────────────────────────────────────────────
_MAX_SPREAD_BPS     = 8.0      # 0.08% 이상 스프레드 = 진입 불리
_MIN_DEPTH_RATIO    = 0.20     # OB depth 비율 < 0.20 = 유동성 부족
_MAX_BTC_DROP_5M    = -0.005   # BTC 5분 -0.5% 이하 = 알트 매수 금지
_MAX_ATR_PCT        = 3.0      # ATR > 3% = 급등락 위험
_MAX_FUNDING_RATE   = 0.03     # 펀딩비 > 3% = 롱 과열 (극단값)

# ── 판정 임계값 ─────────────────────────────────────────────────────
SCORE_STRONG_BUY    = 80.0
SCORE_BUY_CANDIDATE = 70.0
SCORE_WATCH         = 60.0


# ── 유틸 ───────────────────────────────────────────────────────────

def _nan(v: Any) -> bool:
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _pts_to_pct(pts: float, max_pts: float) -> float:
    """원점수(pts)를 0~100 퍼센트로 변환.
    pts=0 → 50(중립),  pts=max_pts → 100(최고),  pts=-max_pts → 0(최저).
    """
    return _clamp(50.0 + pts / max_pts * 50.0)


# ── 데이터 클래스 ───────────────────────────────────────────────────

@dataclass
class ScalpingScore:
    symbol:       str
    score:        float        # 0~100 최종 GSQS 점수
    decision:     str          # STRONG_BUY / BUY_CANDIDATE / WATCH / NO_TRADE / BLOCKED
    blocked:      bool  = False
    block_reason: str   = ""
    sub_scores:   dict[str, float] = field(default_factory=dict)
    signals:      dict[str, Any]   = field(default_factory=dict)
    notes:        list[str]        = field(default_factory=list)

    @property
    def is_buy(self) -> bool:
        return self.decision in ("STRONG_BUY", "BUY_CANDIDATE") and not self.blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":       self.symbol,
            "score":        round(self.score, 2),
            "decision":     self.decision,
            "blocked":      self.blocked,
            "block_reason": self.block_reason,
            "sub_scores":   {k: round(v, 2) for k, v in self.sub_scores.items()},
            "signals":      self.signals,
            "notes":        self.notes,
        }


# ══════════════════════════════════════════════════════════════════
# A. 추세점수 (최대 15점)
# ══════════════════════════════════════════════════════════════════

def _trend_score(feats: dict[str, float], notes: list[str]) -> float:
    """return_1m/3m/5m, ema_gap_1m, vwap_deviation, high_low_position_1m → 0~100."""
    pts = 0.0

    # ① 단기 수익률 (각 +2pt / -2pt)
    ret1 = feats.get("ret_1m", float("nan"))
    if not _nan(ret1):
        r = float(ret1)
        if r > 0:
            pts += 2.0
        elif r < -0.003:   # -0.3% 이하 = 하락 명확
            pts -= 2.0

    ret3 = feats.get("ret_3m", float("nan"))
    if not _nan(ret3):
        r = float(ret3)
        if r > 0:
            pts += 2.0
        elif r < -0.005:
            pts -= 2.0

    ret5 = feats.get("ret_5m", float("nan"))
    if not _nan(ret5):
        r = float(ret5)
        if r > 0:
            pts += 2.0
        elif r < -0.005:
            pts -= 2.0

    # ② EMA9 > EMA21 배열 (+3pt / -3pt)
    ema_gap = feats.get("ema_gap_1m", float("nan"))
    if not _nan(ema_gap):
        if float(ema_gap) > 0:
            pts += 3.0
            notes.append(f"EMA정배열 +3pt (gap={float(ema_gap):.3f}%)")
        else:
            pts -= 3.0

    # ③ close > VWAP (+3pt) + VWAP 황금구간 0~1.5% (+2pt)
    vwap_dev = feats.get("vwap_deviation", float("nan"))
    if not _nan(vwap_dev):
        v = float(vwap_dev)
        if v > 0:
            pts += 3.0
        elif v < -0.02:   # VWAP 2% 이상 아래 = 약세
            pts -= 2.0
        if 0.0 < v <= 0.015:    # VWAP 위 0~1.5% = 황금구간
            pts += 2.0
            notes.append("VWAP 황금구간 +2pt")
        elif v > 0.03:           # VWAP 3% 초과 = 과열
            pts -= 2.0
            notes.append(f"VWAP 과열 감점 (dev={v:.3f})")

    # ④ 5분 고점 돌파 프록시: high_low_position 상단 > 0.7 (+1pt)
    hlpos = feats.get("high_low_position_1m", float("nan"))
    if not _nan(hlpos) and float(hlpos) > 0.7:
        pts += 1.0

    # ⑤ 직전 20봉 최고가 돌파 강도 (GATS breakout_score)
    breakout = feats.get("breakout_score", float("nan"))
    if not _nan(breakout):
        b = float(breakout)
        if b > 0.1:              # 0.1% 이상 신고가 돌파
            pts += 3.0
            notes.append(f"신고가돌파 +3pt ({b:.2f}%)")
        elif b > 0.0:
            pts += 1.0
        elif b < -1.5:           # 고점 대비 1.5% 이상 아래
            pts -= 1.0

    # ⑥ 심볼 간 상대 모멘텀 순위 (GATS relative_strength_rank)
    rsr = feats.get("relative_strength_rank", float("nan"))
    if not _nan(rsr):
        r = float(rsr)
        if r >= 0.80:            # 상위 20% — 강한 알트 선도
            pts += 2.0
            notes.append(f"모멘텀 상위20% +2pt (rank={r:.2f})")
        elif r >= 0.65:          # 상위 35%
            pts += 1.0
        elif r <= 0.25:          # 하위 25% — 약세 심볼
            pts -= 1.0

    return _pts_to_pct(pts, max_pts=20.0)


# ══════════════════════════════════════════════════════════════════
# B. 거래량점수 (최대 15점)
# ══════════════════════════════════════════════════════════════════

def _volume_score(feats: dict[str, float], notes: list[str]) -> float:
    """volume_zscore, volume_ratio, up_vol_ratio, volume_acceleration → 0~100."""
    pts = 0.0

    # ① 거래량 z-score (window=20, GSQS spec은 60 — 현재 20봉으로 근사)
    vzs = feats.get("quote_vol_zscore_20", float("nan"))
    if not _nan(vzs):
        z = float(vzs)
        if z > 2.5:
            pts += 8.0           # > 1.5 (+4) AND > 2.5 (+4) 누적
            notes.append(f"거래량 급증 z={z:.2f} +8pt")
        elif z > 1.5:
            pts += 4.0
            notes.append(f"거래량 증가 z={z:.2f} +4pt")
        elif z < -1.0:
            pts -= 3.0           # 거래량 급감

    # ② 거래량 비율 > 2.0 (+3pt)
    vr20 = feats.get("volume_ratio_20", float("nan"))
    if not _nan(vr20) and float(vr20) > 2.0:
        pts += 3.0

    # ③ 상승봉 거래량 비율 (+2pt / -2pt)
    up_vol = feats.get("up_vol_ratio_10", float("nan"))
    if not _nan(up_vol):
        u = float(up_vol)
        if u > 0.55:             # 상승봉 거래량 우세
            pts += 2.0
        elif u < 0.40:           # 하락봉 거래량 우세
            pts -= 2.0

    # ④ 거래량 가속 + 가격 상승 (+2pt)
    vacc = feats.get("volume_acceleration", float("nan"))
    ret1 = feats.get("ret_1m", float("nan"))
    if not _nan(vacc) and not _nan(ret1):
        if float(vacc) > 0.3 and float(ret1) > 0:
            pts += 2.0
            notes.append("거래량↑+가격↑ +2pt")

    # ⑤ 가짜 펌핑 감지: 거래량 급증 + 큰 위꼬리 + CVD 음수 → 감점
    upper_wick = feats.get("upper_wick_ratio", float("nan"))
    cvd = feats.get("buy_sell_delta", float("nan"))
    if not _nan(vzs) and not _nan(upper_wick) and not _nan(cvd):
        if float(vzs) > 3.0 and float(upper_wick) > 0.40 and float(cvd) < -0.1:
            pts -= 6.0
            notes.append("가짜펌핑 의심 -6pt (vol↑ + 위꼬리↑ + CVD↓)")

    return _pts_to_pct(pts, max_pts=15.0)


# ══════════════════════════════════════════════════════════════════
# C. 호가창점수 (최대 20점)
# ══════════════════════════════════════════════════════════════════

def _orderbook_score(feats: dict[str, float], notes: list[str]) -> float:
    """OBI 단계별, bid/ask depth 비율, 매수벽 거리, spread → 0~100."""
    pts = 0.0

    # ① OBI 단계별 누적 가점 (GSQS 스펙과 동일)
    obi = feats.get("ob_imbalance_l5", feats.get("ob_imbalance", float("nan")))
    if not _nan(obi):
        o = float(obi)
        # 양방향 단계별 적용
        if o > 0.35:
            pts += 12.0    # +3 (>0.10) + 5 (>0.20) + 4 (>0.35)
        elif o > 0.20:
            pts += 8.0     # +3 + 5
        elif o > 0.10:
            pts += 3.0
        elif o < -0.20:
            pts -= 8.0
        elif o < -0.10:
            pts -= 3.0
        notes.append(f"OBI={o:.3f}")

    # ② bid/ask depth 비율 (1% 이내)
    bad = feats.get("bid_ask_depth_ratio", float("nan"))
    if not _nan(bad):
        b = float(bad)
        if b > 0.60:        # 매수 깊이 우세 (> 60%)
            pts += 3.0
        elif b > 0.55:
            pts += 1.5
        elif b < 0.40:      # 매도 깊이 우세
            pts -= 2.0

    # ③ 대형 매수벽 근접 (bid_wall_distance < 0.5%)
    bid_wall = feats.get("bid_wall_distance", float("nan"))
    if not _nan(bid_wall) and 0.0 <= float(bid_wall) < 0.005:
        pts += 2.0

    # ④ 스프레드 타이트 (< 3bps = +3pt)
    spread = feats.get("spread_bps", float("nan"))
    if not _nan(spread):
        s = float(spread)
        if s < 3.0:
            pts += 3.0
        elif s < 5.0:
            pts += 1.0
        elif s >= 8.0:
            pts -= 3.0

    return _pts_to_pct(pts, max_pts=20.0)


# ══════════════════════════════════════════════════════════════════
# D. 체결강도점수 (최대 20점)
# ══════════════════════════════════════════════════════════════════

def _tradeflow_score(feats: dict[str, float], notes: list[str]) -> float:
    """trade_delta(taker_buy_ratio), buy_sell_delta(CVD), large_trade_count → 0~100."""
    pts = 0.0

    # ① trade_delta = taker_buy_ratio * 2 - 1  (수학적 동치)
    tbr = feats.get("taker_buy_ratio", float("nan"))
    if not _nan(tbr):
        delta = float(tbr) * 2.0 - 1.0   # -1 ~ +1 변환
        if delta > 0.25:
            pts += 9.0    # +4 (>0.10) + 5 (>0.25) 누적
            notes.append(f"매수 체결 강함 delta={delta:.3f} +9pt")
        elif delta > 0.10:
            pts += 4.0
        elif delta < -0.25:
            pts -= 9.0
        elif delta < -0.10:
            pts -= 4.0

    # ② CVD 상승 (buy_sell_delta > 0 = +4pt)
    cvd = feats.get("buy_sell_delta", float("nan"))
    if not _nan(cvd):
        c = float(cvd)
        if c > 0.1:
            pts += 4.0
        elif c < -0.2:
            pts -= 3.0

    # ③ 가격 상승 + CVD 상승 동시 = +3pt (진짜 매수 흐름 확인)
    ret1 = feats.get("ret_1m", float("nan"))
    if not _nan(ret1) and not _nan(cvd):
        if float(ret1) > 0 and float(cvd) > 0.1:
            pts += 3.0
            notes.append("가격↑+CVD↑ +3pt")

    # ④ 대형 시장가 매수 체결 빈도 (+2 ~ +4pt)
    ltc = feats.get("large_trade_count_1m", float("nan"))
    if not _nan(ltc):
        lc = float(ltc)
        if lc >= 10:
            pts += 4.0
        elif lc >= 5:
            pts += 2.0

    # ⑤ 체결 가속도 — 현재 1분 체결수 vs 5분 평균 (GATS trade_acceleration)
    tacc = feats.get("trade_acceleration", float("nan"))
    if not _nan(tacc):
        t = float(tacc)
        if t > 1.0:              # 체결 속도 2배 이상 급증
            pts += 3.0
            notes.append(f"체결가속 +3pt (acc={t:.2f}x)")
        elif t > 0.3:
            pts += 1.5
        elif t < -0.5:
            pts -= 2.0

    # ⑥ 고래 체결 감지 (whale_trade_ratio: 최대단일체결 / 평균체결)
    whale = feats.get("whale_trade_ratio", float("nan"))
    if not _nan(whale):
        w = float(whale)
        if w > 20.0:              # 평균의 20배 이상 = 고래 대형 매수
            pts += 3.0
            notes.append(f"고래출현 +3pt (ratio={w:.1f}x)")
        elif w > 10.0:
            pts += 1.5
            notes.append(f"고래감지 +1.5pt (ratio={w:.1f}x)")

    return _pts_to_pct(pts, max_pts=26.0)


# ══════════════════════════════════════════════════════════════════
# E. 선물수급점수 (최대 15점)
# ══════════════════════════════════════════════════════════════════

def _futures_score(feats: dict[str, float], notes: list[str]) -> float:
    """oi_change_pct, funding_rate, long_short_ratio → 0~100."""
    pts = 0.0

    ret1 = feats.get("ret_1m", float("nan"))

    # ① OI 변화 + 가격 방향 (최대 ±5pt)
    oi_chg = feats.get("oi_change_pct", float("nan"))
    if not _nan(oi_chg) and not _nan(ret1):
        oi = float(oi_chg)
        r = float(ret1)
        if oi > 0.5 and r > 0:
            pts += 5.0     # 신규 롱 포지션 유입 + 가격 상승 = 강세
            notes.append(f"OI↑+가격↑ +5pt (oi_chg={oi:.2f}%)")
        elif oi > 0.5 and r < 0:
            pts -= 3.0     # 신규 숏 유입 + 가격 하락 = 추가 하락 압력
        elif oi < -0.5 and r > 0:
            pts += 1.0     # 숏 청산 상승 = 단기성 가능
        elif oi < -2.0:
            pts -= 2.0     # OI 급감 = 포지션 청산 혼조

    # ② Funding rate 레벨
    fr = feats.get("funding_rate", float("nan"))
    if not _nan(fr):
        f = float(fr)
        if f < 0.0:
            pts += 5.0     # 숏 과열 → 롱 유리 (반등 가능성)
            notes.append(f"funding 숏과열 {f:.5f} +5pt")
        elif f <= 0.0001:   # 0 ~ 0.01% = 중립
            pts += (3.0 + 2.0)   # 가격 상승 + funding 낮음 +3, 중립 +2
        elif f <= 0.0005:   # ~ 0.05% = 약간 롱 쏠림
            pts += 2.0
        elif f <= 0.002:    # ~ 0.2% = 롱 쏠림 주의
            pts -= 2.0
        elif f <= 0.005:    # ~ 0.5% = 롱 과열
            pts -= 5.0
            notes.append(f"funding 롱과열 {f:.5f} -5pt")
        else:
            pts -= 8.0     # 극단 과열

    # ③ Long/Short ratio (< 1.5x = 정상, > 2.5x = 쏠림)
    # 0.0은 선물 데이터 없음 (forward-fill 결과) — NaN으로 처리
    ls = feats.get("long_short_ratio", float("nan"))
    if not _nan(ls) and float(ls) > 0.0:
        l = float(ls)
        if l < 1.5:
            pts += 2.0     # 균형 잡힌 포지션
        elif l > 2.5:
            pts -= 3.0     # 롱 쏠림 과다
            notes.append(f"L/S 과열 {l:.2f} -3pt")

    return _pts_to_pct(pts, max_pts=15.0)


# ══════════════════════════════════════════════════════════════════
# F. 변동성/리스크점수 (최대 10점)  ← 기존 구현에 없던 컴포넌트
# ══════════════════════════════════════════════════════════════════

def _risk_score(feats: dict[str, float], notes: list[str]) -> float:
    """ATR_pct, realized_vol, spread 안정성, OB depth → 0~100."""
    pts = 0.0

    # ① ATR 황금구간 0.3~1.5% (+4pt)
    atr = feats.get("atr_14_1m_pct", float("nan"))
    if not _nan(atr):
        a = float(atr)
        if 0.3 <= a <= 1.5:
            pts += 4.0     # 단타에 적합한 변동성 범위
        elif 1.5 < a <= 2.0:
            pts += 2.0     # 약간 높지만 수용 가능
        elif a < 0.3:
            pts -= 2.0     # 죽은 장 — 슬리피지 대비 수익 낮음
        elif a > 2.0:
            pts -= 2.0     # 급등락 위험

    # ② 최근 변동성 상태 (range_pct_1m 기반)
    rng = feats.get("range_pct_1m", float("nan"))
    if not _nan(rng) and not _nan(atr):
        r = float(rng)
        if 0.15 < r < 2.0:    # 적절한 1분봉 레인지
            pts += 2.0

    # ③ 스프레드 안정성 (현재 vs 1분 평균 대비)
    spread_now  = feats.get("spread_bps",     float("nan"))
    spread_mean = feats.get("spread_1m_mean", float("nan"))
    if not _nan(spread_now) and not _nan(spread_mean):
        sm = float(spread_mean)
        sn = float(spread_now)
        if sm > 0 and sn / sm < 1.5:
            pts += 2.0     # 스프레드 안정
        elif sm > 0 and sn / sm > 2.5:
            pts -= 2.0     # 스프레드 확대 = 슬리피지 증가
    elif not _nan(spread_now) and float(spread_now) < 5.0:
        pts += 1.0

    # ④ OB 깊이 충분 → 낮은 슬리피지 예상 (+2pt)
    ob_depth = feats.get("ob_depth_1pct", float("nan"))
    if not _nan(ob_depth) and float(ob_depth) > 0.5:
        pts += 2.0

    return _pts_to_pct(pts, max_pts=10.0)


# ══════════════════════════════════════════════════════════════════
# G. 시장상태점수 (최대 5점)  ← 기존 구현에 없던 컴포넌트
# ══════════════════════════════════════════════════════════════════

def _market_score(feats: dict[str, float], notes: list[str]) -> float:
    """BTC 5분 수익률, 전체 알트 흐름 → 0~100."""
    pts = 0.0

    # ① BTC 5분 절대 수익률 (btc_ret_5m 신규 피처, 없으면 btc_trend_1h 폴백)
    btc5 = feats.get("btc_ret_5m", float("nan"))
    btc_trend = feats.get("btc_trend_1h", float("nan"))

    if not _nan(btc5):
        b = float(btc5)
        if b > 0.001:           # BTC 0.1% 이상 상승
            pts += 2.0
        elif b >= -0.001:       # BTC 보합 (-0.1% ~ +0.1%)
            pts += 1.0
        elif b < -0.003:        # BTC 0.3% 이상 하락 = 알트 불리
            pts -= 1.0

        # BTC 급락 아님 (+2pt) — -0.3% 이내면 OK
        if b > -0.003:
            pts += 2.0
    elif not _nan(btc_trend):   # 폴백: 1h 추세 방향
        t = float(btc_trend)
        if t > 0:
            pts += 3.0
        elif t == 0:
            pts += 1.0

    # ② 전체 알트 활성도 (alt_total_volume_ratio — 현재 심볼의 전체 알트 대비 거래량 비율)
    alt_vol = feats.get("alt_total_volume_ratio", float("nan"))
    if not _nan(alt_vol) and float(alt_vol) > 0.02:
        pts += 1.0

    return _pts_to_pct(pts, max_pts=5.0)


# ══════════════════════════════════════════════════════════════════
# 하드 블록 체크
# ══════════════════════════════════════════════════════════════════

def _check_hard_blocks(feats: dict[str, float]) -> tuple[bool, str]:
    """GSQS 하드 블록 조건 확인. (blocked, reason) 반환."""
    reasons: list[str] = []

    # ① 스프레드 과대 (0.08% = 8bps)
    spread = feats.get("spread_bps", float("nan"))
    if not _nan(spread) and float(spread) > _MAX_SPREAD_BPS:
        reasons.append(f"spread_too_wide:{float(spread):.1f}bps>{_MAX_SPREAD_BPS}bps")

    # ② OB 유동성 부족
    depth = feats.get("ob_depth_1pct", float("nan"))
    if not _nan(depth) and float(depth) < _MIN_DEPTH_RATIO:
        reasons.append(f"low_ob_depth:{float(depth):.3f}<{_MIN_DEPTH_RATIO}")

    # ③ BTC 5분 급락 (-0.5% 이하) → 알트 진입 금지
    btc5 = feats.get("btc_ret_5m", float("nan"))
    if not _nan(btc5) and float(btc5) < _MAX_BTC_DROP_5M:
        reasons.append(f"btc_crash_5m:{float(btc5)*100:.2f}%<{_MAX_BTC_DROP_5M*100:.2f}%")

    # ④ ATR 과대 (변동성 폭발, > 3%)
    atr = feats.get("atr_14_1m_pct", float("nan"))
    if not _nan(atr) and float(atr) > _MAX_ATR_PCT:
        reasons.append(f"atr_too_high:{float(atr):.2f}%>{_MAX_ATR_PCT}%")

    # ⑤ 가짜 펌핑 감지 (volume_zscore > 4 AND upper_wick > 0.45 AND CVD↓)
    vzs = feats.get("quote_vol_zscore_20", float("nan"))
    uw  = feats.get("upper_wick_ratio",    float("nan"))
    cvd = feats.get("buy_sell_delta",      float("nan"))
    if not _nan(vzs) and not _nan(uw) and not _nan(cvd):
        if float(vzs) > 4.0 and float(uw) > 0.45 and float(cvd) < -0.1:
            reasons.append(
                f"fake_pump:z={float(vzs):.1f},wick={float(uw):.2f},cvd={float(cvd):.2f}"
            )

    # ⑥ 가격-CVD 분기 (가격 상승인데 CVD 강하게 하락 = 세력 출회 신호)
    ret1 = feats.get("ret_1m", float("nan"))
    if not _nan(ret1) and not _nan(cvd):
        if float(ret1) > 0.002 and float(cvd) < -0.30:  # +0.2% 상승인데 CVD -0.30 이하
            reasons.append(
                f"price_cvd_divergence:ret1={float(ret1)*100:.2f}%,cvd={float(cvd):.2f}"
            )

    # ⑦ 펀딩비 극단 과열
    fr = feats.get("funding_rate", float("nan"))
    if not _nan(fr) and float(fr) > _MAX_FUNDING_RATE:
        reasons.append(f"funding_overheated:{float(fr):.5f}>{_MAX_FUNDING_RATE}")

    if reasons:
        return True, " | ".join(reasons)
    return False, ""


# ══════════════════════════════════════════════════════════════════
# 메인 진입점
# ══════════════════════════════════════════════════════════════════

def compute_scalping_score(
    symbol: str,
    features: dict[str, float],
) -> ScalpingScore:
    """
    GSQS 계산 메인 함수.

    Args:
        symbol:   Binance 심볼 (예: "BTCUSDT")
        features: FeatureEngine.feature_dict() 결과 (57차원)

    Returns:
        ScalpingScore — score(0~100), decision, sub_scores, signals
    """
    notes: list[str] = []

    # 하드 블록 우선 체크
    blocked, block_reason = _check_hard_blocks(features)

    # 7개 서브스코어 계산 (0~100 각각)
    sub = {
        "trend":     _trend_score(features, notes),
        "volume":    _volume_score(features, notes),
        "orderbook": _orderbook_score(features, notes),
        "tradeflow": _tradeflow_score(features, notes),
        "futures":   _futures_score(features, notes),
        "risk":      _risk_score(features, notes),
        "market":    _market_score(features, notes),
    }

    # 가중 합산 → 최종 0~100 GSQS 점수
    final = sum(sub[k] * _WEIGHTS[k] for k in _WEIGHTS)

    if blocked:
        final = 0.0
        decision = "BLOCKED"
    elif final >= SCORE_STRONG_BUY:
        decision = "STRONG_BUY"
    elif final >= SCORE_BUY_CANDIDATE:
        decision = "BUY_CANDIDATE"
    elif final >= SCORE_WATCH:
        decision = "WATCH"
    else:
        decision = "NO_TRADE"

    signals = {
        "ret_1m":                features.get("ret_1m"),
        "ret_5m":                features.get("ret_5m"),
        "ema_gap_1m":            features.get("ema_gap_1m"),
        "vwap_deviation":        features.get("vwap_deviation"),
        "breakout_score":        features.get("breakout_score"),
        "relative_strength_rank": features.get("relative_strength_rank"),
        "volume_zscore":         features.get("quote_vol_zscore_20"),
        "upper_wick_ratio":      features.get("upper_wick_ratio"),
        "up_vol_ratio_10":       features.get("up_vol_ratio_10"),
        "trade_acceleration":    features.get("trade_acceleration"),
        "ob_imbalance":          features.get("ob_imbalance_l5", features.get("ob_imbalance")),
        "bid_ask_depth_ratio":   features.get("bid_ask_depth_ratio"),
        "spread_bps":            features.get("spread_bps"),
        "taker_buy_ratio":       features.get("taker_buy_ratio"),
        "buy_sell_delta":        features.get("buy_sell_delta"),
        "funding_rate":          features.get("funding_rate"),
        "oi_change_pct":         features.get("oi_change_pct"),
        "long_short_ratio":      features.get("long_short_ratio"),
        "atr_14_1m_pct":         features.get("atr_14_1m_pct"),
        "btc_ret_5m":            features.get("btc_ret_5m"),
    }

    return ScalpingScore(
        symbol=symbol,
        score=round(final, 2),
        decision=decision,
        blocked=blocked,
        block_reason=block_reason,
        sub_scores=sub,
        signals=signals,
        notes=notes,
    )


def score_from_live_state(
    live_state_path: str,
    symbol: str,
) -> ScalpingScore | None:
    """
    live_state.json에서 직접 GSQS 계산.

    Args:
        live_state_path: outputs/binance_stream/live_state.json 경로
        symbol:          Binance 심볼 (예: "BTCUSDT")
    """
    import json
    from pathlib import Path

    path = Path(live_state_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None

    try:
        from deepsignal.market_data.feature_engine.engine import FeatureEngine

        eng = FeatureEngine(output_dir=str(path.parent))
        eng.ingest_live_state(payload)
        feats = eng.feature_dict(symbol)
    except Exception:
        return None

    return compute_scalping_score(symbol, feats)
