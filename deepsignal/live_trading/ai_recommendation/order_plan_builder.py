"""Build live-approve compatible order plans from AI recommendations."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    RecommendationConfig,
    RecommendationResult,
)


def build_ai_live_order_plan(
    recommendations: list[RecommendationResult],
    *,
    config: RecommendationConfig,
    account_context: AccountContext,
    generated_at: str | None = None,
) -> dict[str, Any]:
    ts = generated_at or datetime.now().isoformat(timespec="seconds")
    date_part = ts[:10]
    orders: list[dict[str, Any]] = []
    warnings = [
        "AI recommendation only; live-approve was not called.",
        "Plan status is PENDING_APPROVAL and requires human review.",
        "Only LIMIT BUY orders are included for live-approve compatibility.",
        "SELL/REDUCE candidates are excluded from the order plan by default.",
        "Market orders are prohibited.",
    ]
    if config.allow_sell_candidates:
        warnings.append(
            "--allow-sell-candidates was set, but SELL orders remain excluded because current live-approve validates BUY only."
        )

    for rec in recommendations:
        if not rec.allowed_for_plan:
            continue
        if rec.action not in {"BUY", "INCREASE"}:
            continue
        if rec.suggested_limit_price is None or rec.suggested_limit_price <= 0:
            continue
        if rec.suggested_quantity <= 0 or rec.estimated_order_value <= 0:
            continue
        if not math.isfinite(float(rec.suggested_limit_price)):
            continue
        ai_reasons = [f"{rec.action}: {rec.reason}"]
        ai_reasons.extend(str(n) for n in rec.risk_notes[:3] if str(n).strip())
        orders.append(
            {
                "symbol": rec.symbol,
                "side": "BUY",
                "order_type": "LIMIT",
                "limit_price": rec.suggested_limit_price,
                "target_weight": rec.target_weight,
                "target_value": rec.current_value + rec.estimated_order_value,
                "estimated_price": rec.suggested_limit_price,
                "estimated_qty": rec.suggested_quantity,
                "estimated_order_value": rec.estimated_order_value,
                "reason": f"{rec.action}: {rec.reason}",
                "ai_confidence": int(rec.priority),
                "ai_reasons": ai_reasons,
                "warnings": list(rec.risk_notes),
                "source_action": rec.action,
            }
        )

    return {
        "date": date_part,
        "status": "PENDING_APPROVAL",
        "approval_required": True,
        "generated_by": "ai_live_recommendation",
        "dry_run": True,
        "capital": float(config.capital_limit or account_context.withdrawable_cash or account_context.cash or 0.0),
        "investable_cash": float(config.capital_limit or account_context.withdrawable_cash or account_context.cash or 0.0),
        "cash_buffer": 0.0,
        "currency": config.currency,
        "broker": config.broker,
        "orders": orders,
        "warnings": warnings,
        "safety_boundary": {
            "live_approve_called": False,
            "execute_called": False,
            "kis_order_cash_post_called": False,
            "market_orders_allowed": False,
            "human_final_approval_required": True,
        },
    }
