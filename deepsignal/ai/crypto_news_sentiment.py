"""LLM 기반 코인 뉴스/이벤트 감성 분석 + 캐시.

흐름:
  refresh_crypto_news_sentiment(markets)  ← 주기적(예: 30분)으로 실행
    각 코인: DB 최근 뉴스 → LLM 분류(감성·이벤트·리스크) → 캐시 파일에 기록
  load_news_sentiment(market)  ← 스코어링/게이트가 핫패스에서 빠르게 읽음

캐시: outputs/crypto_news_sentiment.json
  { "KRW-BTC": {score, event, risk, summary, ts, n_news}, ... }

실패/무뉴스/키없음 → None 또는 중립. 절대 거래를 막지 않는다(fail-open).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))
_CACHE = "crypto_news_sentiment.json"
_CACHE_TTL_MIN = 45  # 이보다 오래된 캐시는 '없음' 취급

# LLM이 반환할 이벤트 유형 (악재 → 매수 차단 후보)
_BLOCK_EVENTS = {"hack", "exploit", "delisting", "deposit_suspended", "withdrawal_suspended",
                 "regulatory_action", "lawsuit", "depeg", "insolvency", "rug_pull"}

_SYSTEM_PROMPT = (
    "You are a crypto risk analyst. Given recent news headlines about a coin, "
    "assess sentiment and detect risk events. Respond ONLY with a JSON object: "
    '{"sentiment": <int -100..100>, "event": "<one of: none, hack, exploit, delisting, '
    "deposit_suspended, withdrawal_suspended, regulatory_action, lawsuit, partnership, "
    'listing, upgrade, depeg, insolvency, rug_pull, other>", '
    '"risk": "<none|warn|block>", "summary_ko": "<short Korean reason, <=60 chars>"}. '
    "Use risk=block only for clearly severe negative events (hack, delisting, deposit/withdrawal "
    "suspension, regulatory action, depeg, insolvency). Use warn for mild negatives. "
    "If no relevant news, sentiment=0, event=none, risk=none."
)


def cache_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / _CACHE


def _coin_name(market: str) -> str:
    return str(market).replace("KRW-", "").strip().upper()


def analyze_one_market(
    market: str,
    *,
    db_path: str | None,
    llm,  # LLMClient
    lookback_hours: int = 24,
    max_news: int = 12,
) -> dict[str, Any] | None:
    """단일 코인 뉴스 → LLM 분류. 뉴스 없거나 실패 시 None."""
    try:
        from deepsignal.storage.database import fetch_recent_news_items
    except Exception:
        return None
    sym = _coin_name(market)
    try:
        rows = fetch_recent_news_items(db_path, symbol=sym, limit=max_news)
    except Exception:
        rows = []
    if not rows:
        return None
    # 최근 lookback_hours 이내만
    cutoff = datetime.now(_KST) - timedelta(hours=lookback_hours)
    headlines = []
    for r in rows:
        title = str(r.get("title") or "").strip()
        if not title:
            continue
        headlines.append(f"- {title} ({r.get('published_at') or ''})")
    if not headlines:
        return None
    user = f"Coin: {sym}\nRecent news:\n" + "\n".join(headlines[:max_news])
    out = llm.chat_json(system=_SYSTEM_PROMPT, user=user, max_tokens=400)
    if not isinstance(out, dict):
        return None
    try:
        score = max(-100, min(100, int(float(out.get("sentiment", 0)))))
    except (TypeError, ValueError):
        score = 0
    event = str(out.get("event") or "none").strip().lower()
    risk = str(out.get("risk") or "none").strip().lower()
    if risk not in ("none", "warn", "block"):
        risk = "none"
    # 안전장치: 차단 이벤트면 risk=block 강제
    if event in _BLOCK_EVENTS and risk != "block":
        risk = "block"
    return {
        "score": score,
        "event": event,
        "risk": risk,
        "summary": str(out.get("summary_ko") or "")[:80],
        "n_news": len(headlines),
        "ts": datetime.now(_KST).isoformat(timespec="seconds"),
    }


def refresh_crypto_news_sentiment(
    markets: list[str],
    *,
    output_dir: str | Path = "outputs",
    db_path: str | None = None,
    max_markets: int = 40,
) -> dict[str, Any]:
    """여러 코인의 뉴스 감성을 LLM으로 분석해 캐시에 기록. 요약 dict 반환."""
    from deepsignal.ai.llm_client import get_llm_client, llm_news_enabled

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = {"analyzed": 0, "block": 0, "warn": 0, "skipped_no_news": 0, "enabled": llm_news_enabled()}
    if not llm_news_enabled():
        summary["reason"] = "CRYPTO_LLM_NEWS_ENABLED=off"
        return summary
    llm = get_llm_client()
    if llm is None:
        summary["reason"] = "OPENAI_API_KEY 없음"
        return summary
    # 기존 캐시 로드(부분 갱신)
    cache: dict[str, Any] = {}
    p = cache_path(out)
    if p.exists():
        try:
            cache = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    for mk in markets[:max_markets]:
        res = analyze_one_market(mk, db_path=db_path, llm=llm)
        if res is None:
            summary["skipped_no_news"] += 1
            continue
        cache[mk] = res
        summary["analyzed"] += 1
        if res["risk"] == "block":
            summary["block"] += 1
        elif res["risk"] == "warn":
            summary["warn"] += 1
    cache["_updated_at"] = datetime.now(_KST).isoformat(timespec="seconds")
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def load_news_sentiment(market: str, *, output_dir: str | Path = "outputs") -> dict[str, Any] | None:
    """핫패스용: 캐시에서 코인 1개의 감성 읽기. 만료/없음 → None."""
    p = cache_path(output_dir)
    if not p.exists():
        return None
    try:
        cache = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    rec = cache.get(market)
    if not isinstance(rec, dict):
        return None
    # TTL 체크
    try:
        ts = datetime.fromisoformat(str(rec.get("ts")))
        if datetime.now(_KST) - ts > timedelta(minutes=_CACHE_TTL_MIN):
            return None
    except Exception:
        return None
    return rec


def news_score_for_market(market: str, *, output_dir: str | Path = "outputs") -> float | None:
    """스코어링 주입용 news_score(-100..100). 캐시 없으면 None(중립)."""
    rec = load_news_sentiment(market, output_dir=output_dir)
    if rec is None:
        return None
    try:
        return float(rec.get("score"))
    except (TypeError, ValueError):
        return None


# 호재(긍정 촉매) 이벤트 — 랭킹 우선 + 사이즈업 대상
_POSITIVE_EVENTS = {"listing", "partnership", "upgrade"}


def news_boost_for_market(market: str, *, output_dir: str | Path = "outputs") -> tuple[float, float]:
    """호재 부스트: (랭킹 가산점, 주문 사이즈 배수). 호재 없으면 (0, 1.0).

    강한 호재(긍정 이벤트 + 높은 감성 + risk=none)일수록 우선 매수·약간 큰 주문.
    상한: 랭킹 +20, 사이즈 ×1.3 (과도한 추격 방지).
    """
    rec = load_news_sentiment(market, output_dir=output_dir)
    if rec is None or rec.get("risk") == "block":
        return 0.0, 1.0
    try:
        score = float(rec.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    event = str(rec.get("event") or "none").lower()
    if score < 40 and event not in _POSITIVE_EVENTS:
        return 0.0, 1.0
    # 감성 기반 기본 부스트 + 긍정 이벤트 가산
    rank_bonus = min(20.0, max(0.0, (score - 40.0) * 0.4))
    size_mult = 1.0 + min(0.20, max(0.0, (score - 40.0) / 300.0))
    if event in _POSITIVE_EVENTS:
        rank_bonus = min(20.0, rank_bonus + 8.0)   # 촉매 이벤트 우선
        size_mult = min(1.30, size_mult + 0.10)
    return round(rank_bonus, 1), round(size_mult, 3)
