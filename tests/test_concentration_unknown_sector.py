"""섹터맵 비었을 때 UNKNOWN 종목이 집중도로 과차단되지 않는지 (상승장 0매수 버그)."""

from __future__ import annotations

from deepsignal.live_trading.ai_recommendation.recommendation_quality import apply_portfolio_risk_gates
from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    RecommendationConfig,
    RecommendationResult,
)


def _rec(sym: str, score: float, px: float) -> RecommendationResult:
    return RecommendationResult(
        symbol=sym, action="BUY", action_label="매수 후보", confidence=0.7,
        priority=int(score), reason=f"BUY, final_score={score}", risk_notes=[],
        current_quantity=0, current_value=0.0, current_weight=0.0, target_weight=0.0,
        suggested_quantity=1, suggested_limit_price=px, estimated_order_value=px,
        source_signal_score=score, macro_context={}, account_context={},
        blocked_reasons=[], allowed_for_plan=True, score_breakdown={}, quality_gates={},
    )


def _acct() -> AccountContext:
    return AccountContext(
        broker="kis", snapshot_time="", cash=1_000_000.0, withdrawable_cash=1_000_000.0,
        total_market_value=0, total_equity=1_000_000.0, positions=[],
        stale_snapshot=False, snapshot_age_minutes=0.0, source="test",
    )


def test_unknown_sector_not_concentration_blocked():
    # 섹터맵에 없는(UNKNOWN) 8종목 — 빈 섹터맵에선 전부 한 바구니로 묶이던 버그.
    recs = [_rec(f"{i:06d}", 90.0, 5000.0) for i in range(1, 9)]
    cfg = RecommendationConfig(capital_limit=1_000_000.0, enable_quality_gates=True)
    out = apply_portfolio_risk_gates(recs, account=_acct(), prices_by_day={}, latest_day=None, config=cfg)
    allowed = [r for r in out if r.allowed_for_plan]
    # 수정 전: 2개만 통과 / 수정 후: 집중도로 안 막힘 → 2개 초과 통과
    assert len(allowed) > 2, f"UNKNOWN 종목이 여전히 과차단됨: {len(allowed)}"
    assert all("portfolio_risk_concentration" not in r.blocked_reasons for r in allowed)
