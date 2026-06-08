"""K-GSQS (Korean Stock General Signal Quality Score) 채점 엔진.

6개 서브스코어 × 가중치 → 0~100점 총점
  trend     0.20 — 추세 정렬 (MA/VWAP)
  volume    0.20 — 거래량 이상 (거래량 배수, 매수비율)
  orderbook 0.20 — 호가 불균형 (잔량 비율, 스프레드)
  momentum  0.20 — 모멘텀 (단기 수익률, 체결강도)
  market    0.10 — 시장 상대강도 (KOSPI 대비, 섹터)
  risk      0.10 — 리스크 게이트 (ATR, 갭, 거래정지)

임계값:
  NOTIFY    ≥ 72pt  (알림)
  AUTO      ≥ 82pt  (자동매수 후보)
  STRONG    ≥ 88pt  (강한 매수)
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any

# ─────────────────────────────────────────────────────────
# 서브스코어 가중치
# ─────────────────────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "trend": 0.20,
    "volume": 0.20,
    "orderbook": 0.20,
    "momentum": 0.20,
    "market": 0.10,
    "risk": 0.10,
}

# 신호 임계값 (암호화폐보다 높게: 국내주식 거래비용 0.63% 고려)
THRESHOLD_NOTIFY = 72.0
THRESHOLD_AUTO = 82.0
THRESHOLD_STRONG = 88.0


# ─────────────────────────────────────────────────────────
# 데이터 컨테이너
# ─────────────────────────────────────────────────────────

@dataclass
class KStockFeatures:
    """K-GSQS 채점에 필요한 피처 집합."""

    symbol: str
    ts_ms: int

    # 가격
    price: int = 0
    open_price: int = 0
    high_price: int = 0
    low_price: int = 0
    prev_close: int = 0      # 전일 종가

    # 이동평균 (실시간 봉에서 계산)
    ma5_1m: float = 0.0      # 1분봉 5개 평균
    ma20_1m: float = 0.0     # 1분봉 20개 평균
    vwap_today: float = 0.0  # 당일 VWAP

    # 수익률
    ret_1m: float = 0.0      # 직전 1분 대비
    ret_5m: float = 0.0      # 5분 대비
    ret_15m: float = 0.0     # 15분 대비
    ret_1d: float = 0.0      # 전일 대비

    # 거래량
    vol_ratio_5m: float = 1.0  # 현재 거래량 / 5분 평균
    vol_ratio_20m: float = 1.0 # 현재 / 20분 평균
    buy_ratio_5m: float = 0.5  # 최근 5분 매수비율 (0~1)
    acml_vol_ratio: float = 1.0  # 누적거래량 / 전일 평균 (시간 보정)

    # 호가
    bid_ask_ratio: float = 1.0   # 총매수잔량 / 총매도잔량
    spread_bps: float = 5.0      # 스프레드 bps
    ob_depth_bid: int = 0        # 매수 5단계 잔량 합
    ob_depth_ask: int = 0        # 매도 5단계 잔량 합

    # 체결강도
    strength: float = 100.0    # 체결강도 (KIS 기준 100 = 보합)

    # 시장 상대강도
    kospi_ret_5m: float = 0.0  # KOSPI 5분 수익률
    sector_ret_5m: float = 0.0 # 섹터 5분 수익률
    market_regime: str = "neutral"  # "bull" / "bear" / "neutral"

    # ATR/변동성
    atr_pct: float = 0.0       # ATR(14) / 가격 (%)
    gap_pct: float = 0.0       # 갭 (시가 - 전일종가) / 전일종가

    # 하드블록 플래그
    is_halt: bool = False       # 거래정지
    is_limit_up: bool = False   # 상한가
    is_limit_down: bool = False # 하한가
    is_admin: bool = False      # 관리종목


@dataclass
class KStockSignal:
    """K-GSQS 채점 결과."""

    symbol: str
    ts_ms: int
    total_score: float
    sub_scores: dict[str, float] = field(default_factory=dict)
    action: str = "HOLD"           # STRONG_BUY / BUY / HOLD / SKIP
    hard_blocked: bool = False
    blocked_reason: str = ""
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ─────────────────────────────────────────────────────────
# 서브스코어 함수 (각 0~100점)
# ─────────────────────────────────────────────────────────

def score_trend(f: KStockFeatures) -> float:
    """추세 정렬 점수 — 순수 구조 신호 (MA 정배열 + VWAP 위치).

    [DM4 공선성 제거] ret_5m 방향항을 삭제했다. ret_5m은 trend·momentum·market
    3개 서브스코어를 동시 구동해 실효가중 ~0.5의 단일 모멘텀 팩터를 만들었고,
    IC 분해상 그 모멘텀은 부호가 틀려(평균회귀) 총점을 반예측적으로 만들었다.
    trend은 '추세 구조'(MA/VWAP)만, 수익률은 momentum이 담당하도록 분리한다.

    기준 (만점 100):
      - price > ma5 > ma20: +60 (부분정배열 30/18)
      - VWAP 위치: 최대 +40
    """
    score = 0.0
    if f.price <= 0 or f.ma5_1m <= 0 or f.ma20_1m <= 0:
        return 50.0  # 데이터 없을 때 중립

    # MA 정배열 (완전 정배열 60 → 구조만으로 만점 100 도달)
    if f.price > f.ma5_1m > f.ma20_1m:
        score += 60.0
    elif f.price > f.ma5_1m:
        score += 30.0
    elif f.price > f.ma20_1m:
        score += 18.0

    # VWAP 위 여부 (최대 +40)
    if f.vwap_today > 0:
        if f.price > f.vwap_today:
            excess_pct = (f.price - f.vwap_today) / f.vwap_today * 100
            # VWAP 위 0~2% 범위 = 40점, 2% 이상은 감점 (과열)
            if excess_pct <= 2.0:
                score += min(40.0, 20.0 + excess_pct * 10.0)
            else:
                score += max(7.0, 40.0 - (excess_pct - 2.0) * 6.7)
        else:
            # VWAP 아래지만 접근 중이면 일부 점수
            diff_pct = (f.vwap_today - f.price) / f.vwap_today * 100
            score += max(0.0, 13.0 - diff_pct * 4.0)

    return max(0.0, min(100.0, score))


def score_volume(f: KStockFeatures) -> float:
    """거래량 이상 점수.

    기준:
      - vol_ratio_5m > 2: +40, >1.5: +30, >1.2: +20
      - buy_ratio_5m > 0.55: +30, >0.5: +20, <0.45: -10
      - acml_vol_ratio > 1.2: +30 (시간 보정 누적량)
    """
    score = 0.0

    # 거래량 배수
    vr = f.vol_ratio_5m
    if vr >= 2.5:
        score += 40.0
    elif vr >= 2.0:
        score += 35.0
    elif vr >= 1.5:
        score += 25.0
    elif vr >= 1.2:
        score += 15.0
    elif vr >= 0.8:
        score += 5.0
    else:
        score -= 5.0

    # 매수비율
    br = f.buy_ratio_5m
    if br >= 0.60:
        score += 30.0
    elif br >= 0.55:
        score += 20.0
    elif br >= 0.50:
        score += 10.0
    elif br >= 0.45:
        score += 0.0
    else:
        score -= 15.0

    # 누적거래량 (당일 진행률 감안)
    avr = f.acml_vol_ratio
    if avr >= 1.5:
        score += 30.0
    elif avr >= 1.2:
        score += 20.0
    elif avr >= 1.0:
        score += 10.0

    return max(0.0, min(100.0, score))


def score_orderbook(f: KStockFeatures) -> float:
    """호가 불균형 점수.

    기준:
      - bid_ask_ratio > 1.5: 강한 매수 대기
      - spread_bps 낮을수록 좋음
      - ob_depth_bid/ask 비율
    """
    score = 0.0

    # 매수/매도 잔량 비율
    bar = f.bid_ask_ratio
    if bar >= 2.0:
        score += 50.0
    elif bar >= 1.5:
        score += 40.0
    elif bar >= 1.2:
        score += 30.0
    elif bar >= 1.0:
        score += 20.0
    elif bar >= 0.8:
        score += 10.0
    else:
        score -= 10.0

    # 스프레드 (낮을수록 유동성 좋음)
    sp = f.spread_bps
    if sp <= 3.0:
        score += 30.0
    elif sp <= 5.0:
        score += 20.0
    elif sp <= 10.0:
        score += 10.0
    elif sp <= 20.0:
        score += 0.0
    else:
        score -= 10.0

    # 호가 depth 비율 (bid > ask = 매수 대기)
    if f.ob_depth_ask > 0:
        depth_ratio = f.ob_depth_bid / f.ob_depth_ask
        if depth_ratio >= 1.5:
            score += 20.0
        elif depth_ratio >= 1.2:
            score += 10.0
        elif depth_ratio < 0.8:
            score -= 5.0

    return max(0.0, min(100.0, score))


def score_momentum(f: KStockFeatures) -> float:
    """모멘텀 점수.

    기준:
      - ret_1m, ret_5m, ret_15m 방향 정합
      - 체결강도 (100 기준)
      - 갭(시가 vs 전일종가) 방향
    """
    score = 0.0

    # 다중 타임프레임 수익률 정합
    pos_count = sum(1 for r in [f.ret_1m, f.ret_5m, f.ret_15m] if r > 0)
    if pos_count == 3:
        score += 40.0
    elif pos_count == 2:
        score += 25.0
    elif pos_count == 1:
        score += 5.0
    else:
        score -= 10.0

    # 5분 수익률 강도
    r5 = f.ret_5m
    if r5 >= 1.0:
        score += 30.0
    elif r5 >= 0.5:
        score += 20.0
    elif r5 >= 0.2:
        score += 10.0
    elif r5 < -0.5:
        score -= 15.0

    # 체결강도 (100 = 보합, >100 = 매수 강도)
    st = f.strength
    if st >= 130.0:
        score += 30.0
    elif st >= 110.0:
        score += 20.0
    elif st >= 100.0:
        score += 10.0
    elif st < 80.0:
        score -= 15.0

    return max(0.0, min(100.0, score))


def score_market(f: KStockFeatures) -> float:
    """시장 상대강도 점수.

    기준:
      - 종목 수익률 > KOSPI 수익률 (알파)
      - 섹터 상대강도
      - 시장 레짐
    """
    score = 50.0  # 기본값 50 (데이터 없을 때)

    # 알파 vs KOSPI
    alpha = f.ret_5m - f.kospi_ret_5m
    if alpha >= 0.5:
        score += 30.0
    elif alpha >= 0.2:
        score += 15.0
    elif alpha >= 0.0:
        score += 5.0
    elif alpha < -0.5:
        score -= 20.0
    else:
        score -= 10.0

    # 시장 레짐
    if f.market_regime == "bull":
        score += 10.0
    elif f.market_regime == "bear":
        score -= 15.0

    # 섹터 강도 (종목 - 섹터 알파)
    sector_alpha = f.ret_5m - f.sector_ret_5m
    if sector_alpha >= 0.3:
        score += 10.0
    elif sector_alpha < -0.3:
        score -= 10.0

    return max(0.0, min(100.0, score))


def score_risk(f: KStockFeatures) -> float:
    """리스크 게이트 점수.

    기준:
      - 낮은 ATR = 안정적 = 높은 점수
      - 갭 크기 (적당한 갭은 OK, 과도한 갭은 위험)
      - 하드블록 조건이면 0점
    """
    # 하드블록 → 0점 (채점 후 hard_blocked 처리)
    if f.is_halt or f.is_limit_up or f.is_limit_down or f.is_admin:
        return 0.0

    score = 70.0  # 기본값

    # ATR% — 너무 낮거나 높으면 감점
    atr = f.atr_pct
    if atr == 0.0:
        score -= 10.0  # 데이터 없음
    elif atr <= 0.5:
        score += 20.0  # 안정
    elif atr <= 1.0:
        score += 10.0
    elif atr <= 2.0:
        score += 0.0
    elif atr <= 3.0:
        score -= 10.0
    else:
        score -= 25.0  # 고변동성

    # 갭 크기 (전일대비 시가 갭)
    gap = abs(f.gap_pct)
    if gap <= 1.0:
        score += 10.0
    elif gap <= 2.0:
        score += 5.0
    elif gap <= 4.0:
        score -= 5.0
    else:
        score -= 20.0  # 과도한 갭

    return max(0.0, min(100.0, score))


# ─────────────────────────────────────────────────────────
# 하드블록 검사
# ─────────────────────────────────────────────────────────

def check_hard_block(f: KStockFeatures) -> tuple[bool, str]:
    """하드블록 조건 확인. (blocked, reason)"""
    if f.is_halt:
        return True, "거래정지"
    if f.is_limit_up:
        return True, "상한가(매수불가)"
    if f.is_limit_down:
        return True, "하한가"
    if f.is_admin:
        return True, "관리종목"
    if f.price <= 0:
        return True, "가격데이터없음"
    # 스프레드 극단값 (데이터 오류)
    if f.spread_bps > 500:
        return True, f"스프레드이상({f.spread_bps:.0f}bps)"
    return False, ""


# ─────────────────────────────────────────────────────────
# 총점 계산
# ─────────────────────────────────────────────────────────

def compute_kgsqs(f: KStockFeatures) -> KStockSignal:
    """K-GSQS 총점 및 신호 결정."""
    blocked, reason = check_hard_block(f)
    if blocked:
        return KStockSignal(
            symbol=f.symbol,
            ts_ms=f.ts_ms,
            total_score=0.0,
            sub_scores={k: 0.0 for k in WEIGHTS},
            action="SKIP",
            hard_blocked=True,
            blocked_reason=reason,
            features=_features_to_dict(f),
        )

    sub = {
        "trend": score_trend(f),
        "volume": score_volume(f),
        "orderbook": score_orderbook(f),
        "momentum": score_momentum(f),
        "market": score_market(f),
        "risk": score_risk(f),
    }

    total = sum(sub[k] * WEIGHTS[k] for k in WEIGHTS)
    total = max(0.0, min(100.0, total))

    # 신호 결정
    if total >= THRESHOLD_STRONG:
        action = "STRONG_BUY"
    elif total >= THRESHOLD_AUTO:
        action = "BUY"
    elif total >= THRESHOLD_NOTIFY:
        action = "NOTIFY"
    else:
        action = "HOLD"

    return KStockSignal(
        symbol=f.symbol,
        ts_ms=f.ts_ms,
        total_score=round(total, 2),
        sub_scores={k: round(v, 2) for k, v in sub.items()},
        action=action,
        hard_blocked=False,
        blocked_reason="",
        features=_features_to_dict(f),
    )


def _features_to_dict(f: KStockFeatures) -> dict[str, Any]:
    return {
        "price": f.price,
        "ret_1m": round(f.ret_1m, 4),
        "ret_5m": round(f.ret_5m, 4),
        "ret_15m": round(f.ret_15m, 4),
        "vol_ratio_5m": round(f.vol_ratio_5m, 3),
        "buy_ratio_5m": round(f.buy_ratio_5m, 3),
        "bid_ask_ratio": round(f.bid_ask_ratio, 3),
        "spread_bps": round(f.spread_bps, 2),
        "strength": round(f.strength, 1),
        "atr_pct": round(f.atr_pct, 4),
        "gap_pct": round(f.gap_pct, 4),
        "market_regime": f.market_regime,
        "kospi_ret_5m": round(f.kospi_ret_5m, 4),
        "hard_blocks": {
            "halt": f.is_halt,
            "limit_up": f.is_limit_up,
            "limit_down": f.is_limit_down,
            "admin": f.is_admin,
        },
    }
