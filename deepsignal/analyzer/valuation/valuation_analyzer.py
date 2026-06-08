"""yfinance 기반 단순 밸류에이션 v1 (DCF 근사 + 멀티플). 외부 LLM 없음."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ValuationResult:
    symbol: str
    analyzed_at: str
    market_price: float | None
    intrinsic_value: float | None
    mispricing_pct: float | None
    valuation_score: float | None
    pe_ratio: float | None
    forward_pe: float | None
    price_to_book: float | None
    revenue_growth: float | None
    reason: str
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "analyzed_at": self.analyzed_at,
            "market_price": self.market_price,
            "intrinsic_value": self.intrinsic_value,
            "mispricing_pct": self.mispricing_pct,
            "valuation_score": self.valuation_score,
            "pe_ratio": self.pe_ratio,
            "forward_pe": self.forward_pe,
            "price_to_book": self.price_to_book,
            "revenue_growth": self.revenue_growth,
            "reason": self.reason,
            "raw": dict(self.raw),
        }


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        if not math.isfinite(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _score_from_mispricing(mispricing_pct: float | None) -> float | None:
    """저평가(+mispricing)일수록 높은 점수 -100~100."""
    if mispricing_pct is None:
        return None
    # +30% undervalued → ~60, -30% overvalued → ~-60
    return max(-100.0, min(100.0, float(mispricing_pct) * 200.0))


class ValuationAnalyzer:
    """규칙 기반 내재가치 추정. 네트워크 필요(yfinance)."""

    def analyze_symbol(self, symbol: str) -> ValuationResult:
        sym = symbol.strip().upper()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            import yfinance as yf
        except ImportError:
            return ValuationResult(
                symbol=sym,
                analyzed_at=now,
                market_price=None,
                intrinsic_value=None,
                mispricing_pct=None,
                valuation_score=None,
                pe_ratio=None,
                forward_pe=None,
                price_to_book=None,
                revenue_growth=None,
                reason="yfinance 패키지 없음",
                raw={"error": "import yfinance"},
            )

        try:
            ticker = yf.Ticker(sym)
            info = ticker.info or {}
            hist = ticker.history(period="5d")
        except Exception as exc:
            return ValuationResult(
                symbol=sym,
                analyzed_at=now,
                market_price=None,
                intrinsic_value=None,
                mispricing_pct=None,
                valuation_score=None,
                pe_ratio=None,
                forward_pe=None,
                price_to_book=None,
                revenue_growth=None,
                reason=f"yfinance 조회 실패: {type(exc).__name__}",
                raw={"error": str(exc)[:200]},
            )

        market_price = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
        if market_price is None and hist is not None and not hist.empty:
            market_price = _safe_float(hist["Close"].iloc[-1])

        pe = _safe_float(info.get("trailingPE"))
        fpe = _safe_float(info.get("forwardPE"))
        pb = _safe_float(info.get("priceToBook"))
        rev_g = _safe_float(info.get("revenueGrowth"))
        eps = _safe_float(info.get("trailingEps"))
        bv = _safe_float(info.get("bookValue"))

        estimates: list[float] = []
        reasons: list[str] = []

        if eps and eps > 0 and fpe and fpe > 0:
            fair_pe = (pe or fpe) * 0.9 if pe else fpe * 0.85
            if fair_pe > 0:
                estimates.append(eps * fair_pe)
                reasons.append("EPS×조정PER 근사")

        if bv and bv > 0 and pb and pb > 0:
            fair_pb = max(1.0, pb * 0.85)
            estimates.append(bv * fair_pb)
            reasons.append("BPS×조정PBR 근사")

        if market_price and rev_g is not None and rev_g > 0.05:
            estimates.append(market_price * (1.0 + min(rev_g, 0.25)))
            reasons.append("성장 프리미엄 단순 조정")

        intrinsic: float | None = None
        if estimates:
            intrinsic = sum(estimates) / len(estimates)

        mispricing: float | None = None
        if intrinsic is not None and market_price and market_price > 0:
            mispricing = (intrinsic - market_price) / market_price

        v_score = _score_from_mispricing(mispricing)
        if mispricing is None:
            reason = "내재가치 추정 불가(데이터 부족)"
        elif mispricing >= 0.15:
            reason = f"저평가 추정 ({mispricing:.1%}) — " + ", ".join(reasons)
        elif mispricing <= -0.15:
            reason = f"고평가 추정 ({mispricing:.1%}) — " + ", ".join(reasons)
        else:
            reason = f"적정가 근처 ({mispricing:.1%}) — " + ", ".join(reasons)

        return ValuationResult(
            symbol=sym,
            analyzed_at=now,
            market_price=market_price,
            intrinsic_value=intrinsic,
            mispricing_pct=mispricing,
            valuation_score=v_score,
            pe_ratio=pe,
            forward_pe=fpe,
            price_to_book=pb,
            revenue_growth=rev_g,
            reason=reason,
            raw={
                "estimates": estimates,
                "methods": reasons,
                "info_keys_used": ["trailingPE", "forwardPE", "priceToBook", "revenueGrowth", "trailingEps", "bookValue"],
            },
        )
