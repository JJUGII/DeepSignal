"""기술지표 기반 기본 점수화 (후보 기록용, 주문 아님)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from deepsignal.analyzer.technical.technical_analyzer import TechnicalIndicator
from deepsignal.scoring.analysis_conditions import (
    DEFAULT_ANALYSIS_CONDITIONS,
    AnalysisConditions,
)


@dataclass
class SignalResult:
    """종목·일자별 점수화 결과 (signals 테이블 매핑)."""

    symbol: str
    signal_date: str
    technical_score: float | None
    news_score: float | None
    macro_score: float | None
    final_score: float | None
    action: str
    confidence: float | None
    reason: str
    raw: dict[str, Any]
    strategy_name: str = "technical_v1"


class SignalScorer:
    """technical_v1 규칙: 추세·RSI·종가/EMA 가중, -100~+100."""

    def __init__(self, conditions: AnalysisConditions | None = None) -> None:
        self.conditions = conditions or DEFAULT_ANALYSIS_CONDITIONS

    def score_final(
        self,
        technical_score: float | None,
        news_score: float | None = None,
        macro_score: float | None = None,
    ) -> float | None:
        """technical·news·macro 가중 합산 (기본 0.6 / 0.2 / 0.2).

        news·macro 중 일부가 None이면 해당 가중치를 제외하고 나머지만으로 정규화한다.
        둘 다 없으면 technical만 반환한다.
        """
        sw = self.conditions.score
        if technical_score is None:
            return None
        if news_score is None and macro_score is None:
            return float(technical_score)
        w_t, w_n, w_m = sw.technical_weight, sw.news_weight, sw.macro_weight
        weights: list[float] = [w_t]
        values: list[float] = [float(technical_score)]
        if news_score is not None:
            weights.append(w_n)
            values.append(float(news_score))
        if macro_score is not None:
            weights.append(w_m)
            values.append(float(macro_score))
        denom = sum(weights)
        if denom <= 0:
            return None
        blended = sum(w * v for w, v in zip(weights, values, strict=True)) / denom
        return max(sw.score_min, min(sw.score_max, blended))

    def decide_action(self, final_score: float | None, confidence: float | None = None) -> str:
        _ = confidence  # 추후 임계값 조정용
        sw = self.conditions.score
        if final_score is None:
            return "INSUFFICIENT_DATA"
        if final_score >= sw.buy_candidate_min:
            return "BUY_CANDIDATE"
        if final_score <= sw.sell_candidate_max:
            return "SELL_CANDIDATE"
        return "HOLD"

    def score_technical(self, indicator: TechnicalIndicator) -> float | None:
        score, _ = self._score_technical_with_reason(indicator)
        return score

    def _score_technical_with_reason(
        self, indicator: TechnicalIndicator
    ) -> tuple[float | None, list[str]]:
        th = self.conditions.technical
        sw = self.conditions.score
        parts: list[str] = []
        used = False
        score = 0.0

        ts = indicator.trend_score
        if ts is not None:
            used = True
            delta = th.trend_delta(float(ts))
            score += float(delta)
            if ts == 1.0:
                parts.append("강한 상승 추세 반영")
            elif ts == 0.5:
                parts.append("완만한 상승 추세 반영")
            elif ts == -0.5:
                parts.append("완만한 하락 추세 반영")
            elif ts == -1.0:
                parts.append("강한 하락 추세 반영")
            elif ts == 0.0:
                parts.append("추세 중립 구간")

        rsi = indicator.rsi_14
        if rsi is not None and not (isinstance(rsi, float) and math.isnan(rsi)):
            used = True
            r = float(rsi)
            if r >= th.rsi_overbought_severe:
                score += th.rsi_overbought_severe_penalty
                parts.append("RSI 과열 구간으로 일부 감점")
            elif r >= th.rsi_overbought_mild:
                score += th.rsi_overbought_mild_penalty
                parts.append("RSI 다소 과열로 소폭 감점")
            elif r <= th.rsi_oversold_severe:
                score += th.rsi_oversold_severe_bonus
                parts.append("RSI 과매도 구간으로 일부 가점")
            elif r <= th.rsi_oversold_mild:
                score += th.rsi_oversold_mild_bonus
                parts.append("RSI 다소 과매도로 소폭 가점")

        c = indicator.close
        ema12 = indicator.ema_12
        if c is not None and ema12 is not None and not math.isnan(c) and not math.isnan(ema12):
            used = True
            if c > ema12:
                score += th.close_above_ema_fast_bonus
                parts.append("종가가 12일 EMA 위에 위치")
            elif c < ema12:
                score += th.close_below_ema_fast_penalty
                parts.append("종가가 12일 EMA 아래에 위치")

        if not used:
            return None, ["지표 부족으로 판단 보류"]

        score = max(sw.score_min, min(sw.score_max, float(score)))
        if not parts:
            parts.append("기술 지표 기반 점수 산출")
        return score, parts

    def score_latest(
        self,
        symbol: str,
        indicators: list[TechnicalIndicator],
        *,
        news_score: float | None = None,
        macro_score: float | None = None,
        extra_raw: dict[str, Any] | None = None,
    ) -> SignalResult | None:
        """가장 최근 봉(리스트 마지막) 기준으로 점수화한다."""
        if not indicators:
            return None
        latest = indicators[-1]
        sym = symbol.strip().upper()
        tech, reason_parts = self._score_technical_with_reason(latest)
        final = self.score_final(tech, news_score, macro_score)
        action = self.decide_action(final)
        if tech is None:
            confidence = None
        else:
            confidence = min(1.0, abs(tech) / 100.0)
        if action == "INSUFFICIENT_DATA":
            confidence = None
        base_reason = " ".join(reason_parts) if reason_parts else "기술 지표 기반 점수 산출"
        reason = base_reason
        if news_score is not None:
            reason = f"{reason} 뉴스 감성 점수 {float(news_score):.2f} 반영"
        if macro_score is not None:
            reason = f"{reason} 거시 점수 {float(macro_score):.2f} 반영"
        raw: dict[str, Any] = {
            "trend_score": latest.trend_score,
            "rsi_14": latest.rsi_14,
            "close": latest.close,
            "ema_12": latest.ema_12,
            "ema_26": latest.ema_26,
            "analysis_conditions": "DEFAULT_ANALYSIS_CONDITIONS",
        }
        if extra_raw:
            raw.update(extra_raw)
        return SignalResult(
            symbol=sym,
            signal_date=str(latest.trade_date),
            technical_score=tech,
            news_score=news_score,
            macro_score=macro_score,
            final_score=final,
            action=action,
            confidence=confidence,
            reason=reason,
            raw=raw,
            strategy_name="technical_v1",
        )
