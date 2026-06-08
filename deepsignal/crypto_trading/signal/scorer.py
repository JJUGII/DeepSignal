"""Crypto market scoring — technical / macro / final (aligned with stock SignalScorer)."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig, evaluate_crypto_buy_quality
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitTicker
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS
from deepsignal.scoring.macro_scorer import MacroScorer
from deepsignal.scoring.signal_scorer import SignalScorer

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto
_SCORE = DEFAULT_ANALYSIS_CONDITIONS.score
_TECH = DEFAULT_ANALYSIS_CONDITIONS.technical
_SIGNAL_SCORER = SignalScorer()


@dataclass
class CryptoMarketScore:
    market: str
    display_name: str
    technical_score: float
    macro_score: float | None
    final_score: float | None
    macro_regime: str
    technical_components: dict[str, Any] = field(default_factory=dict)
    quality_diag: dict[str, Any] = field(default_factory=dict)
    quality_ok: bool = True
    quality_reason: str = "quality_ok"
    size_multiplier: float = 1.0

    def to_signal_dict(self, macro_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "symbol": self.market,
            "technical_score": self.technical_score,
            "news_score": None,
            "macro_score": self.macro_score,
            "final_score": self.final_score,
            "macro_regime": self.macro_regime,
            **macro_context,
        }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def load_crypto_macro_context(db_path: str | Path | None = None) -> dict[str, Any]:
    """VIX/DXY/TNX from deepsignal DB; fallback neutral if missing."""
    if db_path:
        try:
            from deepsignal.storage.database import fetch_latest_economic_indicators

            result = MacroScorer().calculate_macro_score(
                fetch_latest_economic_indicators(str(db_path))
            )
            return {
                "macro_score": result.macro_score,
                "market_regime": str(result.market_regime or "neutral"),
                "macro_confidence": result.confidence,
                "macro_reason": result.reason,
                "macro_source": "economic_indicators",
            }
        except Exception as exc:
            _LOG.debug("[macro_context] DB 조회 실패 (%s): %s", db_path, exc)
    return {
        "macro_score": None,
        "market_regime": "neutral",
        "macro_confidence": 0.0,
        "macro_reason": "거시 DB 없음 — technical only",
        "macro_source": "fallback",
    }


def compute_crypto_technical_score(
    ticker: UpbitTicker,
    quality_diag: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    """Rule-based technical score -100..+100 (stock-compatible scale)."""
    parts: list[str] = []
    # Baseline so liquid majors with mild momentum land near stock-like BUY_CANDIDATE range.
    score = 35.0
    parts.append("baseline +35pt")

    chg_pct = float(ticker.signed_change_rate or 0) * 100.0
    mom_pts = _clamp(chg_pct * 12.0, -30.0, 30.0)
    score += mom_pts
    parts.append(f"24h 변동 {chg_pct:+.2f}% → {mom_pts:+.0f}pt")

    rsi = quality_diag.get("rsi_14")
    if isinstance(rsi, (int, float)):
        rsi_f = float(rsi)
        if rsi_f <= _TECH.rsi_oversold_mild:
            score += float(_TECH.rsi_oversold_mild_bonus)
            parts.append(f"RSI {rsi_f:.1f} 과매도 가점")
        elif rsi_f >= _TECH.rsi_overbought_mild:
            pen = float(_TECH.rsi_overbought_mild_penalty)
            score += pen
            parts.append(f"RSI {rsi_f:.1f} 과열 감점")
        elif rsi_f >= 50:
            score += 5.0
            parts.append(f"RSI {rsi_f:.1f} 중립 상단")

    vol_ratio = quality_diag.get("volume_ratio")
    if isinstance(vol_ratio, (int, float)):
        vr = float(vol_ratio)
        if vr >= float(_CRYPTO.min_volume_ratio):
            score += min(15.0, (vr - 0.5) * 10.0)
            parts.append(f"거래량비율 {vr:.2f} 가점")
        else:
            score -= 10.0
            parts.append(f"거래량비율 {vr:.2f} 감점")

    atr = quality_diag.get("atr_pct")
    if isinstance(atr, (int, float)) and float(atr) > float(_CRYPTO.max_atr_pct):
        score -= 8.0
        parts.append(f"ATR {float(atr):.1f}% 변동성 감점")

    gc_1d = quality_diag.get("gc_1d")
    if gc_1d == "golden_cross":
        score += 10.0
        parts.append("일봉 골든크로스 +10pt")
    elif gc_1d == "above":
        score += 6.0
        parts.append("일봉 EMA50>200 +6pt")
    elif gc_1d == "dead_cross":
        score -= 10.0
        parts.append("일봉 데드크로스 -10pt")
    elif gc_1d == "below":
        score -= 6.0
        parts.append("일봉 EMA50<200 -6pt")

    final_tech = _clamp(score, _SCORE.score_min, _SCORE.score_max)
    return final_tech, {
        "momentum_pct": chg_pct,
        "momentum_points": mom_pts,
        "rsi_14": rsi,
        "volume_ratio": vol_ratio,
        "atr_pct": atr,
        "notes": parts,
    }


def score_crypto_market(
    broker: UpbitBroker,
    ticker: UpbitTicker,
    *,
    display_name: str,
    macro_context: dict[str, Any],
    buy_quality: CryptoBuyQualityConfig | None = None,
    realtime_features: dict[str, Any] | None = None,
) -> CryptoMarketScore:
    """
    코인 시장 스코어 계산.

    Args:
        realtime_features: FeatureEngine.feature_dict() 결과 (선택).
                           제공 시 스캘핑 스코어를 blending하여 final_score에 반영.
    """
    cfg = buy_quality or CryptoBuyQualityConfig()
    ok, q_reason, mult, diag = evaluate_crypto_buy_quality(broker, ticker.market, ticker, cfg=cfg)
    tech, tech_comp = compute_crypto_technical_score(ticker, diag)
    macro_score = macro_context.get("macro_score")
    if macro_score is not None:
        try:
            macro_f = float(macro_score)
            if not math.isfinite(macro_f):
                macro_f = None
        except (TypeError, ValueError):
            macro_f = None
    else:
        macro_f = None

    # ── LLM 뉴스 감성 주입 (캐시에서 읽음, 활성화+캐시有 시에만) ──
    news_f: float | None = None
    try:
        import os as _o_news
        if _o_news.environ.get("CRYPTO_LLM_NEWS_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
            from deepsignal.ai.crypto_news_sentiment import news_score_for_market
            _mk = getattr(ticker, "market", None)
            if _mk:
                news_f = news_score_for_market(str(_mk))
    except Exception:
        news_f = None

    # ── 기본 final score (일봉 기반) ──
    final = _SIGNAL_SCORER.score_final(tech, news_score=news_f, macro_score=macro_f)

    # ── 실시간 스캘핑 스코어 블렌딩 ──
    # realtime_features가 있으면: final = daily * 0.5 + scalping_norm * 0.5
    # scalping_score 0~100 → -100~+100 스케일로 변환 후 블렌딩
    scalping_diag: dict[str, Any] = {}
    if realtime_features:
        try:
            from deepsignal.crypto_trading.signal.scalping_scorer import compute_scalping_score

            # Binance 심볼 → Upbit 마켓 매핑 (KRW-BTC → BTCUSDT)
            binance_sym = ticker.market.replace("KRW-", "") + "USDT"
            sc = compute_scalping_score(binance_sym, realtime_features)
            scalping_diag = sc.to_dict()

            if not sc.blocked:
                # 0~100 → -100~+100 변환
                sc_norm = (sc.score - 50.0) * 2.0
                # 일봉 50% + 단타 50% 블렌딩
                final = _clamp(final * 0.5 + sc_norm * 0.5,
                               _SCORE.score_min, _SCORE.score_max)
            else:
                # 하드 블록이면 final score를 낮춤
                final = min(final, -20.0)
        except Exception as _e:
            _LOG.debug("[scalping_blend] 스캘핑 스코어 계산 실패: %s", _e)

    # ── 김치프리미엄 페널티 ──────────────────────────────────────────────────
    kimchi_diag: dict[str, Any] = {}
    try:
        from deepsignal.crypto_trading.kimchi_premium import get_premium, score_penalty

        sym = ticker.market.replace("KRW-", "")
        kp = get_premium(sym)
        if kp is not None:
            penalty, reason = score_penalty(kp.premium_pct)
            kimchi_diag = {
                "premium_pct": kp.premium_pct,
                "level": kp.level,
                "penalty": penalty,
                "usd_krw": kp.usd_krw_rate,
            }
            if penalty < 0:
                final = _clamp(final + penalty, _SCORE.score_min, _SCORE.score_max)
                _LOG.info("[kimchi] %s %s → final %.1f", ticker.market, reason, final)
                tech_comp.setdefault("notes", []).append(reason)
    except Exception as _e:
        _LOG.debug("[kimchi] 프리미엄 계산 실패: %s", _e)

    return CryptoMarketScore(
        market=ticker.market,
        display_name=display_name,
        technical_score=tech,
        macro_score=macro_f,
        final_score=final,
        macro_regime=str(macro_context.get("market_regime") or "neutral"),
        technical_components={**tech_comp, "scalping": scalping_diag, "kimchi": kimchi_diag},
        quality_diag=diag,
        quality_ok=ok,
        quality_reason=q_reason,
        size_multiplier=mult,
    )


def build_crypto_score_breakdown(
    market_score: CryptoMarketScore,
    macro_context: dict[str, Any],
) -> dict[str, Any]:
    """Same shape as stock `build_score_breakdown`."""

    def _fmt(v: Any) -> str:
        if v is None:
            return "n/a"
        try:
            return f"{float(v):+.1f}"
        except (TypeError, ValueError):
            return "n/a"

    return {
        "technical_score": market_score.technical_score,
        "news_score": None,
        "macro_score": market_score.macro_score,
        "final_score": market_score.final_score,
        "macro_regime": market_score.macro_regime or macro_context.get("market_regime"),
        "technical_components": dict(market_score.technical_components),
        "quality_diag": dict(market_score.quality_diag),
        "display": {
            "technical": _fmt(market_score.technical_score),
            "news": "n/a",
            "macro": _fmt(market_score.macro_score),
            "final": _fmt(market_score.final_score),
            "macro_regime": str(market_score.macro_regime or "n/a"),
        },
    }
