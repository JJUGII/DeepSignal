from __future__ import annotations

from deepsignal.live_trading.ai_recommendation.order_plan_builder import build_ai_live_order_plan
from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    RecommendationConfig,
    RecommendationResult,
)
from deepsignal.live_trading.live_order_executor import validate_live_order_plan
from deepsignal.live_trading.live_order_plan import live_order_plan_from_dict


def _rec(action: str = "BUY", *, allowed: bool = True) -> RecommendationResult:
    return RecommendationResult(
        symbol="005930",
        action=action,
        action_label=action,
        confidence=0.8,
        priority=90,
        reason="test",
        risk_notes=[],
        current_quantity=0,
        current_value=0.0,
        current_weight=0.0,
        target_weight=0.2,
        suggested_quantity=3,
        suggested_limit_price=100.0,
        estimated_order_value=300.0,
        source_signal_score=80.0,
        macro_context={},
        account_context={},
        blocked_reasons=[],
        allowed_for_plan=allowed,
    )


def test_ai_order_plan_is_live_approve_compatible_buy_limit_only() -> None:
    plan = build_ai_live_order_plan(
        [_rec("BUY")],
        config=RecommendationConfig(capital_limit=1000.0),
        account_context=AccountContext(cash=1000.0, withdrawable_cash=1000.0),
        generated_at="2026-05-17T12:00:00",
    )

    assert plan["status"] == "PENDING_APPROVAL"
    assert plan["approval_required"] is True
    assert plan["dry_run"] is True
    assert plan["generated_by"] == "ai_live_recommendation"
    assert plan["orders"][0]["side"] == "BUY"
    assert plan["orders"][0]["order_type"] == "LIMIT"
    ok, errors = validate_live_order_plan(live_order_plan_from_dict(plan))
    assert ok is True
    assert errors == []


def test_sell_candidates_are_not_included_even_when_allowed_for_reporting() -> None:
    plan = build_ai_live_order_plan(
        [_rec("SELL")],
        config=RecommendationConfig(capital_limit=1000.0, allow_sell_candidates=True),
        account_context=AccountContext(cash=1000.0, withdrawable_cash=1000.0),
        generated_at="2026-05-17T12:00:00",
    )

    assert plan["orders"] == []
    assert any("SELL orders remain excluded" in warning for warning in plan["warnings"])


def test_blocked_recommendations_are_excluded_from_order_plan() -> None:
    plan = build_ai_live_order_plan(
        [_rec("BUY", allowed=False)],
        config=RecommendationConfig(capital_limit=1000.0),
        account_context=AccountContext(cash=1000.0, withdrawable_cash=1000.0),
        generated_at="2026-05-17T12:00:00",
    )

    assert plan["orders"] == []


def test_safety_boundary_records_no_execution_calls() -> None:
    plan = build_ai_live_order_plan(
        [_rec("BUY")],
        config=RecommendationConfig(capital_limit=1000.0),
        account_context=AccountContext(cash=1000.0, withdrawable_cash=1000.0),
        generated_at="2026-05-17T12:00:00",
    )

    boundary = plan["safety_boundary"]
    assert boundary["live_approve_called"] is False
    assert boundary["execute_called"] is False
    assert boundary["kis_order_cash_post_called"] is False
    assert boundary["market_orders_allowed"] is False
