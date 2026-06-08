"""종목별 통합 신호(기술·뉴스·거시·밸류) 생성 — score-symbol·paper-step 공용."""

from __future__ import annotations

from typing import Any

from deepsignal.analyzer.technical.technical_analyzer import TechnicalAnalyzer
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS
from deepsignal.scoring.signal_scorer import SignalResult, SignalScorer


def build_symbol_signal(
    db_path: str,
    symbol: str,
    *,
    include_news: bool = True,
    include_macro: bool = True,
    include_valuation: bool = True,
) -> tuple[SignalResult | None, dict[str, Any]]:
    """SignalResult와 메타 dict 반환. insert 하지 않음."""
    sym = symbol.strip().upper()
    meta: dict[str, Any] = {
        "symbol": sym,
        "news_score": None,
        "news_count": 0,
        "macro_score": None,
        "valuation_score": None,
        "mispricing_pct": None,
    }

    analyzer = TechnicalAnalyzer()
    indicators = analyzer.analyze_symbol_from_db(db_path, sym, source="yfinance", limit=120)
    if not indicators:
        return None, meta

    news_score_val: float | None = None
    news_sentiment_block: dict[str, Any] = {"available": False}
    if include_news:
        try:
            from deepsignal.analyzer.sentiment.sentiment_analyzer import SentimentAnalyzer
            from deepsignal.storage.database import fetch_recent_news_items

            news_rows = fetch_recent_news_items(db_path, symbol=sym, limit=100)
            sent = SentimentAnalyzer().analyze_news_items(sym, news_rows)
            news_score_val = sent.news_score
            meta["news_count"] = int(sent.news_count)
            news_sentiment_block = {
                "available": True,
                "news_count": sent.news_count,
                "news_score": sent.news_score,
                "confidence": sent.confidence,
                "sentiment_reason": sent.reason,
                "trajectory": (sent.raw or {}).get("trajectory"),
            }
        except Exception:
            news_sentiment_block = {"available": False, "skipped": True}

    macro_score_val: float | None = None
    macro_block: dict[str, Any] = {"available": False}
    if include_macro:
        try:
            from deepsignal.scoring.macro_scorer import MacroScorer
            from deepsignal.storage.database import fetch_latest_economic_indicators

            econ_rows = fetch_latest_economic_indicators(db_path)
            macro_res = MacroScorer().calculate_macro_score(econ_rows)
            macro_score_val = macro_res.macro_score
            macro_block = {
                "available": macro_res.macro_score is not None,
                "macro_score": macro_res.macro_score,
                "market_regime": macro_res.market_regime,
            }
        except Exception:
            macro_block = {"available": False, "skipped": True}

    valuation_block: dict[str, Any] = {"available": False}
    valuation_score: float | None = None
    if include_valuation:
        try:
            from deepsignal.analyzer.valuation.valuation_analyzer import ValuationAnalyzer

            val = ValuationAnalyzer().analyze_symbol(sym)
            valuation_score = val.valuation_score
            meta["valuation_score"] = val.valuation_score
            meta["mispricing_pct"] = val.mispricing_pct
            valuation_block = val.to_dict()
            valuation_block["available"] = val.intrinsic_value is not None
        except Exception:
            valuation_block = {"available": False, "skipped": True}

    scorer = SignalScorer()
    signal = scorer.score_latest(
        sym,
        indicators,
        news_score=news_score_val,
        macro_score=macro_score_val,
        extra_raw={
            "news_sentiment": news_sentiment_block,
            "macro": macro_block,
            "valuation": valuation_block,
        },
    )
    if signal is None:
        return None, meta

    vw = DEFAULT_ANALYSIS_CONDITIONS.score
    val_w = getattr(vw, "valuation_weight", 0.1)
    if valuation_score is not None and signal.final_score is not None and val_w > 0:
        base = float(signal.final_score)
        blended = (1.0 - val_w) * base + val_w * float(valuation_score)
        signal.final_score = max(vw.score_min, min(vw.score_max, blended))
        signal.action = scorer.decide_action(signal.final_score, signal.confidence)
        signal.reason = f"{signal.reason} 밸류 점수 {valuation_score:.1f} 반영"

    meta["technical_score"] = signal.technical_score
    meta["news_score"] = signal.news_score
    meta["macro_score"] = signal.macro_score
    meta["final_score"] = signal.final_score
    meta["action"] = signal.action
    return signal, meta
