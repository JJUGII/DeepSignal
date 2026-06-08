"""뉴스 제목·요약 키워드 기반 감성 분석 v1 (외부 AI·전문 수집 없음)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

_POSITIVE_KWS: tuple[str, ...] = (
    "beat",
    "beats",
    "growth",
    "rally",
    "surge",
    "upgrade",
    "record",
    "profit",
    "strong",
    "optimistic",
    "bullish",
)
_NEGATIVE_KWS: tuple[str, ...] = (
    "miss",
    "misses",
    "fall",
    "drop",
    "plunge",
    "downgrade",
    "loss",
    "weak",
    "bearish",
    "investigation",
    "lawsuit",
    "warning",
)
_POSITIVE_KO: tuple[str, ...] = (
    "상승",
    "급등",
    "호실적",
    "흑자",
    "수주",
    "신고가",
    "매수",
    "성장",
    "호조",
    "서프라이즈",
)
_NEGATIVE_KO: tuple[str, ...] = (
    "하락",
    "급락",
    "적자",
    "손실",
    "경고",
    "리콜",
    "소송",
    "규제",
    "매도",
    "부진",
    "하향",
)


@dataclass
class NewsSentimentResult:
    """종목별 뉴스 감성 요약."""

    symbol: str
    analyzed_at: str
    news_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    news_score: float | None
    confidence: float | None
    reason: str
    raw: dict[str, Any] = field(default_factory=dict)


class SentimentAnalyzer:
    """영어 키워드 규칙 기반. 제목+요약만 사용 (전문·API 없음)."""

    def __init__(
        self,
        *,
        positive_keywords: tuple[str, ...] = _POSITIVE_KWS,
        negative_keywords: tuple[str, ...] = _NEGATIVE_KWS,
        positive_keywords_ko: tuple[str, ...] = _POSITIVE_KO,
        negative_keywords_ko: tuple[str, ...] = _NEGATIVE_KO,
    ) -> None:
        self._pos = tuple(k.lower() for k in positive_keywords)
        self._neg = tuple(k.lower() for k in negative_keywords)
        self._pos_ko = tuple(positive_keywords_ko)
        self._neg_ko = tuple(negative_keywords_ko)

    def analyze_text(self, text: str) -> tuple[float, str]:
        """단일 텍스트에 대해 -1.0 / 0.0 / 1.0 과 한 줄 이유(한국어)."""
        lower = (text or "").lower()
        blob = text or ""
        pos_hits = sum(1 for kw in self._pos if kw in lower)
        neg_hits = sum(1 for kw in self._neg if kw in lower)
        pos_hits += sum(1 for kw in self._pos_ko if kw in blob)
        neg_hits += sum(1 for kw in self._neg_ko if kw in blob)
        if pos_hits == 0 and neg_hits == 0:
            return 0.0, "감성 키워드 없음(중립)"
        if pos_hits > neg_hits:
            return 1.0, "긍정 키워드가 우세"
        if neg_hits > pos_hits:
            return -1.0, "부정 키워드가 우세"
        return 0.0, "긍정·부정 키워드 균형(중립)"

    def analyze_news_items(
        self, symbol: str, news_rows: list[Mapping[str, Any]]
    ) -> NewsSentimentResult:
        """뉴스 행 목록(제목·요약)으로 종목 감성 점수·신뢰도 산출."""
        sym = symbol.strip().upper()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not news_rows:
            return NewsSentimentResult(
                symbol=sym,
                analyzed_at=now,
                news_count=0,
                positive_count=0,
                negative_count=0,
                neutral_count=0,
                news_score=None,
                confidence=None,
                reason="해당 심볼 관련 뉴스가 없습니다.",
                raw={"rows": 0},
            )

        sentiments: list[float] = []
        pos_c = neg_c = neu_c = 0
        per_row: list[dict[str, Any]] = []
        for row in news_rows:
            title = (row.get("title") or "") if isinstance(row, Mapping) else ""
            summary = (row.get("summary") or "") if isinstance(row, Mapping) else ""
            blob = f"{title} {summary}".strip()
            s, r = self.analyze_text(blob)
            sentiments.append(s)
            if s > 0:
                pos_c += 1
            elif s < 0:
                neg_c += 1
            else:
                neu_c += 1
            rid = row.get("id") if isinstance(row, Mapping) else None
            per_row.append({"id": rid, "sentiment": s, "detail": r})

        nonzero = sum(1 for x in sentiments if x != 0.0)
        avg = sum(sentiments) / len(sentiments)
        news_score = float(avg * 100.0)
        confidence = float(nonzero / len(sentiments)) if sentiments else None

        if pos_c > neg_c:
            reason = "긍정 뉴스가 부정 뉴스보다 많습니다."
        elif neg_c > pos_c:
            reason = "부정 뉴스가 긍정 뉴스보다 많습니다."
        elif pos_c == neg_c == 0:
            reason = "감성 키워드가 검출된 뉴스가 없습니다."
        else:
            reason = "긍정과 부정 신호가 비슷합니다."

        trajectory = self._compute_trajectory(news_rows, sentiments)
        if trajectory.get("label") == "deteriorating":
            news_score = float(news_score * 0.85 - 5.0)
            reason = f"{reason} (최근 뉴스 감성 악화 추세)"
        elif trajectory.get("label") == "improving":
            news_score = float(news_score * 0.85 + 5.0)
            reason = f"{reason} (최근 뉴스 감성 개선 추세)"

        raw = {
            "rows": len(news_rows),
            "per_row": per_row[:50],
            "avg_sentiment": avg,
            "nonzero_ratio": confidence,
            "trajectory": trajectory,
        }
        return NewsSentimentResult(
            symbol=sym,
            analyzed_at=now,
            news_count=len(news_rows),
            positive_count=pos_c,
            negative_count=neg_c,
            neutral_count=neu_c,
            news_score=news_score,
            confidence=confidence,
            reason=reason,
            raw=raw,
        )

    @staticmethod
    def _compute_trajectory(
        news_rows: list[Mapping[str, Any]],
        sentiments: list[float],
    ) -> dict[str, Any]:
        """시간순 뉴스를 전반/후반으로 나눠 감성 궤적 라벨 산출."""
        if len(sentiments) < 4:
            return {"label": "insufficient", "recent_avg": None, "older_avg": None}
        pairs: list[tuple[str, float]] = []
        for row, s in zip(news_rows, sentiments, strict=False):
            pub = ""
            if isinstance(row, Mapping):
                pub = str(row.get("published_at") or row.get("collected_at") or "")
            pairs.append((pub, s))
        pairs.sort(key=lambda x: x[0])
        mid = len(pairs) // 2
        older = [s for _, s in pairs[:mid]]
        recent = [s for _, s in pairs[mid:]]
        if not older or not recent:
            return {"label": "insufficient", "recent_avg": None, "older_avg": None}
        older_avg = sum(older) / len(older)
        recent_avg = sum(recent) / len(recent)
        delta = recent_avg - older_avg
        if delta <= -0.25:
            label = "deteriorating"
        elif delta >= 0.25:
            label = "improving"
        else:
            label = "stable"
        return {
            "label": label,
            "recent_avg": recent_avg,
            "older_avg": older_avg,
            "delta": delta,
        }
