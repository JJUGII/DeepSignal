"""레짐 기반 ETF 선택기 — 인버스(P2)·레버리지(P3).

시장 레짐(추세 강도)에 따라 거래할 ETF와 노출 배율을 고른다.
기존 추세추종(regime_trend)을 현물 ETF만으로 확장:
  강한 상승 → 레버리지 ETF(2~3x)
  약한 상승 → 일반 ETF(1x)
  하락      → 현금 (또는 인버스 ETF, 옵션)

백테스트(strategy_lab) 결과 근거:
  - 레버리지(특히 나스닥)는 추세구간에서 수익·샤프 모두 개선 → 검증 시 활성 가능.
  - 인버스(숏)는 지수 장기상승 탓에 엣지 없음 → 기본 OFF(검증 통과 전엔 현금 유지).

모든 공격 옵션은 env로 기본 꺼짐. 켜도 EDGE_GATE 검증을 통과해야 라이브.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# 시장별 ETF 매핑 (국내 상장 = KIS 현물로 매수 가능)
ETF_MAP = {
    "sp500": {
        "normal":     "360750",   # TIGER 미국S&P500 (1x)
        "leverage":   "418660",   # TIGER 미국S&P500레버리지(합성) 2x  ※검증 후 사용
        "inverse":    "",          # 국내 S&P500 인버스 미흡 → 해외 SH 등 필요
    },
    "nasdaq": {
        "normal":     "133690",   # TIGER 미국나스닥100 (1x)
        "leverage":   "409820",   # KODEX 미국나스닥100레버리지 (2x, 베타·상관 검증 완료, 1일 시차)
        "inverse":    "",
    },
    "kospi": {
        "normal":     "069500",   # KODEX 200 (1x)
        "leverage":   "122630",   # KODEX 레버리지 (2x)
        "inverse":    "114800",   # KODEX 인버스 (-1x)
        "inverse2x":  "252670",   # KODEX 200선물인버스2X (-2x)
    },
}


def leverage_enabled() -> bool:
    return os.environ.get("REGIME_LEVERAGE_ENABLED", "").strip().lower() in ("1", "true", "yes")


def inverse_enabled() -> bool:
    return os.environ.get("REGIME_INVERSE_ENABLED", "").strip().lower() in ("1", "true", "yes")


def max_leverage() -> float:
    try:
        return max(1.0, min(3.0, float(os.environ.get("REGIME_MAX_LEVERAGE", "2"))))
    except ValueError:
        return 2.0


@dataclass
class RegimeDecision:
    regime: str          # strong_up | mild_up | down
    exposure: float      # 목표 배율 (+2, +1, 0, -1 ...)
    etf: str             # 거래 대상 ETF (빈 문자열이면 현금)
    label: str           # 사람용 설명
    leverage_used: bool
    inverse_used: bool


def classify_regime(close: float, sma200: float, sma50: float, ret_20d: float) -> str:
    if close <= sma200:
        return "down"
    if close > sma50 and ret_20d > 0:
        return "strong_up"
    return "mild_up"


def select_etf(
    market: str,
    close: float, sma200: float, sma50: float, ret_20d: float,
) -> RegimeDecision:
    """레짐 → (ETF, 배율) 결정. 공격 옵션은 env 게이트로 제어."""
    m = ETF_MAP.get(market, ETF_MAP["kospi"])
    regime = classify_regime(close, sma200, sma50, ret_20d)
    lev_on = leverage_enabled()
    inv_on = inverse_enabled()
    lev = max_leverage()

    if regime == "strong_up":
        if lev_on and m.get("leverage"):
            return RegimeDecision(regime, lev, m["leverage"],
                                  f"강한 상승 → 레버리지 {lev:.0f}x ETF", True, False)
        return RegimeDecision(regime, 1.0, m["normal"], "강한 상승 → 일반 ETF(레버리지 OFF)", False, False)
    if regime == "mild_up":
        return RegimeDecision(regime, 1.0, m["normal"], "약한 상승 → 일반 ETF", False, False)
    # down
    if inv_on and m.get("inverse"):
        return RegimeDecision(regime, -1.0, m["inverse"], "하락 → 인버스 ETF", False, True)
    return RegimeDecision(regime, 0.0, "", "하락 → 현금(인버스 OFF/미검증)", False, False)
