"""[Phase2] 코인-네이티브 4장세 레짐 판정.

기존 DM6 regime_gate는 S&P500 일봉이라 코인 단타엔 둔하다. 여기선 BTC 5분봉 자체로
판정한다: VWAP·EMA20/60 추세 + market_breadth(업비트 KRW 종목 중 5분 양봉 비율).

레짐:
  UP_TREND   — close>VWAP & EMA20>EMA60 & breadth 높음  → 모멘텀 돌파 허용
  RANGE      — 추세 불명확                              → 평균회귀
  DOWN_TREND — close<VWAP & EMA20<EMA60 & breadth 낮음  → 반등 셋업만
  PANIC      — 급락 + breadth 극저                       → 무매매(or 마이크로 반등)

순수 함수 — 입력 봉/breadth만으로 판정(look-ahead 없음, 외부 의존 없음).
"""

from __future__ import annotations

from enum import Enum
from typing import Sequence


class Regime(str, Enum):
    UP_TREND = "UP_TREND"
    RANGE = "RANGE"
    DOWN_TREND = "DOWN_TREND"
    PANIC = "PANIC"


def ema(values: Sequence[float], period: int) -> float | None:
    """마지막 EMA 값. 표본이 period 미만이면 None."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < period or period <= 0:
        return None
    k = 2.0 / (period + 1.0)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1.0 - k)
    return e


def vwap(bars: Sequence[dict]) -> float | None:
    """typical price(고저종 평균) × 거래량 가중평균. 거래량 합 0이면 종가 평균."""
    num = 0.0
    den = 0.0
    closes: list[float] = []
    for b in bars:
        c = float(b.get("close", 0) or 0)
        h = float(b.get("high", c) or c)
        low = float(b.get("low", c) or c)
        vol = float(b.get("volume", 0) or 0)
        tp = (h + low + c) / 3.0
        num += tp * vol
        den += vol
        closes.append(c)
    if den > 0:
        return num / den
    return sum(closes) / len(closes) if closes else None


def _closes(bars: Sequence[dict]) -> list[float]:
    return [float(b.get("close", 0) or 0) for b in bars]


def classify_btc_regime(
    bars_5m: Sequence[dict],
    breadth: float,
    *,
    panic_window: int = 3,
    panic_drop_pct: float = -3.0,
    panic_breadth: float = 0.20,
    down_breadth: float = 0.35,
    up_breadth: float = 0.55,
) -> Regime:
    """BTC 5분봉 + breadth(0~1)로 4장세 판정.

    Args:
        bars_5m: [{open,high,low,close,volume}, ...] 시간 오름차순. 최소 ~60개 권장.
        breadth: 업비트 KRW 종목 중 5분 수익률 양수 비율 (0~1).
    """
    closes = _closes(bars_5m)
    if len(closes) < 60:
        return Regime.RANGE  # 데이터 부족 → 중립(매매 유보는 상위에서)

    last = closes[-1]
    vw = vwap(bars_5m[-60:]) or last
    e20 = ema(closes[-60:], 20)
    e60 = ema(closes[-60:], 60)
    if e20 is None or e60 is None:
        return Regime.RANGE

    # 최근 panic_window봉 누적 변동률(%)
    base = closes[-1 - panic_window] if len(closes) > panic_window else closes[0]
    recent_chg = (last - base) / base * 100.0 if base > 0 else 0.0

    # PANIC: 급락 + breadth 극저
    if recent_chg <= panic_drop_pct and breadth <= panic_breadth:
        return Regime.PANIC

    down = last < vw and e20 < e60 and breadth < down_breadth
    up = last > vw and e20 > e60 and breadth > up_breadth
    if down:
        return Regime.DOWN_TREND
    if up:
        return Regime.UP_TREND
    return Regime.RANGE


def allows_new_long_by_default(regime: Regime) -> bool:
    """레짐 기본 신규 롱 허용 여부. DOWN/PANIC은 기본 차단(예외 셋업은 상위에서)."""
    return regime in (Regime.UP_TREND, Regime.RANGE)
