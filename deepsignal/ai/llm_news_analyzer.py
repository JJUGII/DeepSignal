"""LLM 기반 뉴스·공시 촉매 분석 — 키워드 카운팅을 대체하는 진짜 AI 분석.

기존 sentiment_analyzer는 긍정/부정 '단어 세기'였다. 이건 OpenAI로 헤드라인+요약을
*읽고* 구조화된 촉매 신호를 낸다:
  - catalyst_type: 실적/계약·수주/공시/규제/테마/M&A/기타
  - polarity: -1(악재) ~ +1(호재)
  - strength: 0~1 (주가 영향 강도)
  - half_life_hours: 촉매 유효 시간(빨리 식는가)
  - news_score: 0~100 (기존 news_score 인터페이스 호환; 50=중립)
  - confidence, reason, affected 여부

env:
  NEWS_LLM_ENABLED (기본 false — 켜야 LLM 호출)
  OPENAI_API_KEY (필수)
  NEWS_LLM_MODEL (기본 gpt-4o-mini — 싸고 빠름)

키 없거나 비활성/실패면 None 반환(graceful) → 호출측은 기존 키워드 분석으로 폴백.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import requests

_API_URL = "https://api.openai.com/v1/chat/completions"


def llm_news_enabled() -> bool:
    return os.environ.get("NEWS_LLM_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _model() -> str:
    return os.environ.get("NEWS_LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def _api_key() -> str | None:
    k = (os.environ.get("OPENAI_API_KEY") or "").strip()
    return k or None


@dataclass
class LLMCatalystResult:
    """종목/뉴스 묶음에 대한 LLM 촉매 분석 결과."""

    symbol: str
    analyzed_at: str
    news_count: int
    news_score: float | None          # 0~100 (50=중립) — 기존 news_score 호환
    polarity: float                    # -1 ~ +1
    strength: float                    # 0 ~ 1
    catalyst_type: str
    half_life_hours: float | None
    confidence: float | None
    reason: str
    model: str
    raw: dict[str, Any] = field(default_factory=dict)


_SYSTEM = (
    "당신은 한국·미국 주식 단타 트레이더를 위한 뉴스 촉매 분석가다. "
    "주어진 종목의 최근 뉴스/공시 헤드라인과 요약을 읽고, 그 종목 주가에 대한 "
    "촉매(catalyst)를 냉정하게 평가한다. 과장 금지. 이미 다 알려져 주가에 반영됐을 "
    "법한 뉴스는 strength를 낮춘다. 반드시 JSON만 출력한다."
)

_SCHEMA_HINT = (
    '{"polarity": -1.0~1.0 (악재 음수/호재 양수), '
    '"strength": 0.0~1.0 (주가 영향 강도; 이미 반영된 구뉴스는 낮게), '
    '"catalyst_type": "실적|계약수주|공시|규제|테마|M&A|인사|소송|기타|없음", '
    '"half_life_hours": 촉매 유효시간(숫자, 빨리 식으면 작게 예: 6, 길면 72), '
    '"confidence": 0.0~1.0, '
    '"reason": "한 줄 근거(한국어, 40자 이내)"}'
)


def _build_user_prompt(symbol: str, name: str | None, news_rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [f"종목: {name or ''} ({symbol})", "최근 뉴스/공시:"]
    for i, n in enumerate(news_rows[:12], 1):
        title = str(n.get("title") or "").strip()
        summ = str(n.get("summary") or n.get("body_text") or "").strip()
        # HTML 태그 대충 제거 + 길이 제한
        summ = summ.replace("<p>", " ").replace("</p>", " ")
        if "<" in summ:
            import re
            summ = re.sub(r"<[^>]+>", " ", summ)
        summ = " ".join(summ.split())[:200]
        pub = str(n.get("published_at") or n.get("created_at") or "")[:16]
        lines.append(f"{i}. [{pub}] {title} — {summ}")
    lines.append("")
    lines.append(f"위 뉴스로 이 종목의 촉매를 평가해 JSON으로만 답하라. 형식: {_SCHEMA_HINT}")
    return "\n".join(lines)


def analyze_symbol_news(
    symbol: str,
    news_rows: Sequence[Mapping[str, Any]],
    *,
    name: str | None = None,
    timeout: float = 20.0,
) -> LLMCatalystResult | None:
    """종목의 뉴스 묶음을 LLM으로 분석. 비활성/무키/무뉴스/실패 → None(폴백 유도)."""
    if not llm_news_enabled():
        return None
    key = _api_key()
    if not key or not news_rows:
        return None
    from datetime import datetime, timezone

    model = _model()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_user_prompt(symbol, name, news_rows)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "max_tokens": 300,
    }
    try:
        resp = requests.post(
            _API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
    except Exception:
        return None

    try:
        polarity = max(-1.0, min(1.0, float(data.get("polarity") or 0.0)))
        strength = max(0.0, min(1.0, float(data.get("strength") or 0.0)))
        conf = data.get("confidence")
        conf = max(0.0, min(1.0, float(conf))) if conf is not None else None
        half = data.get("half_life_hours")
        half = float(half) if half not in (None, "") else None
    except (TypeError, ValueError):
        return None

    # news_score(0~100): 50 중립 기준, polarity×strength로 편차. (기존 인터페이스 호환)
    news_score = round(50.0 + polarity * strength * 50.0, 1)
    return LLMCatalystResult(
        symbol=symbol,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
        news_count=len(news_rows),
        news_score=news_score,
        polarity=polarity,
        strength=strength,
        catalyst_type=str(data.get("catalyst_type") or "기타"),
        half_life_hours=half,
        confidence=conf,
        reason=str(data.get("reason") or "")[:80],
        model=model,
        raw=data,
    )
