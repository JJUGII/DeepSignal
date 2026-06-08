"""점수 기반 포트폴리오 배분 v1 (실주문·브로커 없음, paper 연동 준비용)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from deepsignal.portfolio.portfolio_models import PortfolioAllocation, PortfolioSnapshot
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS
from deepsignal.scoring.macro_scorer import MacroScoreResult

_PA = DEFAULT_ANALYSIS_CONDITIONS.portfolio

_MAX_NAMES = _PA.max_names
_MAX_WEIGHT = _PA.max_symbol_weight
_MIN_WEIGHT = _PA.min_symbol_weight
_MIN_CONFIDENCE = _PA.min_confidence


def _invest_cap_fraction(regime: str) -> float:
    r = (regime or "neutral").strip().lower()
    if r == "risk_off":
        return _PA.invest_cap_risk_off
    if r == "risk_on":
        return _PA.invest_cap_risk_on
    return _PA.invest_cap_neutral


def _normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, v) for v in scores.values())
    if total <= 0:
        return {}
    return {k: max(0.0, v) / total for k, v in scores.items()}


def _apply_max_weight(weights: dict[str, float], cap: float = _MAX_WEIGHT) -> dict[str, float]:
    """비중 합이 1이 되도록 유지하며 종목별 상한 `cap`을 반복 적용한다."""
    w = dict(weights)
    for _ in range(len(w) * 3 + 5):
        s = sum(w.values())
        if s <= 1e-12:
            return {}
        w = {k: v / s for k, v in w.items()}
        over = {k: v - cap for k, v in w.items() if v > cap + 1e-9}
        if not over:
            return w
        surplus = sum(over.values())
        for k in over:
            w[k] = cap
        rest = {k: v for k, v in w.items() if w[k] < cap - 1e-9}
        base = sum(rest.values())
        if base <= 1e-12 or surplus <= 1e-12:
            return w
        for k in rest:
            w[k] += surplus * (rest[k] / base)
    return w


def _apply_min_weight_drop(weights: dict[str, float], floor: float = _MIN_WEIGHT) -> dict[str, float]:
    """`floor` 미만 비중 종목을 제거한 뒤 재정규화한다."""
    w = dict(weights)
    while w:
        s = sum(w.values())
        if s <= 1e-12:
            return {}
        w = {k: v / s for k, v in w.items()}
        small = [k for k, v in w.items() if v < floor - 1e-9]
        if not small:
            return w
        for k in small:
            del w[k]
    return {}


class PortfolioEngine:
    """signals·거시 국면을 바탕으로 목표 배분안을 산출한다 (리밸런싱·주문 없음)."""

    def build_portfolio(
        self,
        signals: list[dict[str, Any]],
        total_cash: float,
        macro_result: MacroScoreResult | None = None,
    ) -> PortfolioSnapshot:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        regime = "neutral"
        if macro_result is not None:
            regime = str(macro_result.market_regime or "neutral")

        invest_cap = _invest_cap_fraction(regime)
        cash_total = max(0.0, float(total_cash))
        deployable = cash_total * invest_cap

        rows: list[dict[str, Any]] = []
        for r in signals:
            sym = str(r.get("symbol", "")).strip().upper()
            if not sym:
                continue
            action = str(r.get("action", "") or "").strip().upper()
            if action != "BUY_CANDIDATE":
                continue
            fs = r.get("final_score")
            try:
                fsv = float(fs) if fs is not None else None
            except (TypeError, ValueError):
                fsv = None
            if fsv is None or fsv <= 0:
                continue
            cf = r.get("confidence")
            try:
                cfv = float(cf) if cf is not None else None
            except (TypeError, ValueError):
                cfv = None
            if cfv is None or cfv < _MIN_CONFIDENCE:
                continue
            rows.append(dict(r, symbol=sym))

        rows.sort(key=lambda x: float(x.get("final_score") or 0.0), reverse=True)
        picked = rows[:_MAX_NAMES]

        scores = {str(p["symbol"]): float(p["final_score"]) for p in picked}
        inner = _normalize_scores(scores)
        inner = _apply_max_weight(inner)
        inner = _apply_min_weight_drop(inner)
        inner = _apply_max_weight(inner)

        allocations: list[PortfolioAllocation] = []
        for sym, iw in inner.items():
            tw = invest_cap * iw
            amt = cash_total * tw
            row = next((x for x in picked if x["symbol"] == sym), {})
            rationale = (
                f"BUY_CANDIDATE·final_score={float(row.get('final_score', 0)):.1f}·"
                f"confidence≥{_MIN_CONFIDENCE}·국면={regime}·투자상한={invest_cap:.0%}"
            )
            allocations.append(
                PortfolioAllocation(
                    symbol=sym,
                    final_score=float(row.get("final_score")) if row.get("final_score") is not None else None,
                    target_weight=tw,
                    target_amount=amt,
                    rationale=rationale,
                    raw={
                        "inner_weight": iw,
                        "action": row.get("action"),
                        "confidence": row.get("confidence"),
                        "technical_score": row.get("technical_score"),
                        "news_score": row.get("news_score"),
                        "macro_score": row.get("macro_score"),
                        "signal_date": row.get("signal_date"),
                    },
                )
            )

        allocations.sort(key=lambda a: a.target_weight, reverse=True)

        cash_buffer = 1.0 - sum(a.target_weight for a in allocations)
        raw: dict[str, Any] = {
            "invest_cap_fraction": invest_cap,
            "deployable_cash": deployable,
            "cash_buffer_fraction": max(0.0, cash_buffer),
            "macro": {
                "market_regime": regime,
                "macro_score": getattr(macro_result, "macro_score", None),
                "confidence": getattr(macro_result, "confidence", None),
            }
            if macro_result
            else {"market_regime": regime},
            "allocations_for_paper": [
                {
                    "symbol": a.symbol,
                    "target_weight": a.target_weight,
                    "target_amount": a.target_amount,
                    "rationale": a.rationale,
                }
                for a in allocations
            ],
            "rules": {
                "max_symbols": _MAX_NAMES,
                "max_weight": _MAX_WEIGHT,
                "min_weight_inner": _MIN_WEIGHT,
                "min_confidence": _MIN_CONFIDENCE,
                "action_filter": "BUY_CANDIDATE",
            },
        }

        return PortfolioSnapshot(
            analyzed_at=now,
            total_cash=cash_total,
            market_regime=regime,
            allocations=allocations,
            raw=raw,
        )
