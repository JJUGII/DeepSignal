"""거시 점수 macro_score v1 (규칙 기반, API 키 없음)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from deepsignal.scoring.analysis_conditions import (
    DEFAULT_ANALYSIS_CONDITIONS,
    AnalysisConditions,
)


@dataclass
class MacroScoreResult:
    analyzed_at: str
    macro_score: float | None
    market_regime: str
    confidence: float
    reason: str
    raw: dict[str, Any] = field(default_factory=dict)


class MacroScorer:
    """VIX·DXY·TNX 규칙으로 risk-on / neutral / risk-off 성격의 점수를 산출한다."""

    _EXPECTED = ("VIX", "DXY", "TNX")

    def __init__(self, conditions: AnalysisConditions | None = None) -> None:
        self.conditions = conditions or DEFAULT_ANALYSIS_CONDITIONS

    def calculate_macro_score(
        self,
        indicators: list[dict[str, Any]],
    ) -> MacroScoreResult:
        """``fetch_latest_economic_indicators`` 형태의 dict 목록을 받아 점수화한다."""
        mh = self.conditions.macro
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        by_name: dict[str, float] = {}
        for row in indicators:
            name = str(row.get("indicator_name", "")).strip().upper()
            val = row.get("value")
            if not name or val is None:
                continue
            try:
                fv = float(val)
            except (TypeError, ValueError):
                continue
            if math.isnan(fv):
                continue
            by_name[name] = fv

        if not by_name:
            return MacroScoreResult(
                analyzed_at=now,
                macro_score=None,
                market_regime="neutral",
                confidence=0.0,
                reason="저장된 거시 지표가 없어 거시 점수를 산출하지 못했습니다.",
                raw={"by_name": by_name, "contributions": {}},
            )

        contributions: dict[str, float] = {}
        reason_parts: list[str] = []

        vix = by_name.get("VIX")
        if vix is not None:
            c, msg = self._score_vix(vix)
            contributions["VIX"] = c
            if msg:
                reason_parts.append(msg)

        dxy = by_name.get("DXY")
        if dxy is not None:
            c, msg = self._score_dxy(dxy)
            contributions["DXY"] = c
            if msg:
                reason_parts.append(msg)

        tnx = by_name.get("TNX")
        if tnx is not None:
            c, msg = self._score_tnx(tnx)
            contributions["TNX"] = c
            if msg:
                reason_parts.append(msg)

        total = sum(contributions.values())
        score = max(mh.score_min, min(mh.score_max, float(total)))
        regime = self._regime(score)
        used = sum(1 for k in self._EXPECTED if k in by_name)
        confidence = min(1.0, used / 3.0)

        if not reason_parts:
            if score >= mh.narrative_positive_min:
                reason_parts.append("저변동성·완화된 금리·달러 압력 완화 등 긍정적 거시 신호")
            elif score <= mh.narrative_negative_max:
                reason_parts.append("변동성·달러·금리 등 복합 거시 압력")
            else:
                reason_parts.append("거시 지표가 중립 구간에 가깝습니다.")

        return MacroScoreResult(
            analyzed_at=now,
            macro_score=score,
            market_regime=regime,
            confidence=confidence,
            reason="\n".join(f"- {p}" for p in reason_parts),
            raw={
                "by_name": by_name,
                "contributions": contributions,
                "total_before_clamp": total,
            },
        )

    def _regime(self, score: float) -> str:
        mh = self.conditions.macro
        if score >= mh.regime_risk_on_min:
            return "risk_on"
        if score <= mh.regime_risk_off_max:
            return "risk_off"
        return "neutral"

    def _score_vix(self, vix: float) -> tuple[float, str]:
        mh = self.conditions.macro
        if vix >= mh.vix_high:
            return mh.vix_high_penalty, "VIX 상승으로 위험회피 심리 강화"
        if vix >= mh.vix_elevated:
            return mh.vix_elevated_penalty, "VIX가 높아 위험자산 부담"
        if vix < mh.vix_low:
            return mh.vix_low_bonus, "저변동성 환경"
        return 0.0, ""

    def _score_dxy(self, dxy: float) -> tuple[float, str]:
        mh = self.conditions.macro
        if dxy >= mh.dxy_strong:
            return mh.dxy_strong_penalty, "달러 강세 및 금리 부담"
        if dxy < mh.dxy_weak:
            return mh.dxy_weak_bonus, "달러 약세로 유동성 환경 완화"
        return 0.0, ""

    def _score_tnx(self, tnx: float) -> tuple[float, str]:
        mh = self.conditions.macro
        if tnx >= mh.tnx_high:
            return mh.tnx_high_penalty, "장기 금리 부담 확대"
        if tnx <= mh.tnx_low:
            return mh.tnx_low_bonus, "장기 금리 완화 구간"
        return 0.0, ""
