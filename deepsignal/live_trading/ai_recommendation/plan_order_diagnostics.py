"""Diagnose why AI recommendations do not become plan orders."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

SAFETY_BLOCK_PREFIX = "safety_audit="

from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    OperationalRiskContext,
    RecommendationConfig,
    RecommendationResult,
    RecommendationRunResult,
)

GLOBAL_FATAL_MARKERS = (
    "safety_audit=",
    "reconcile=",
    "partial_fill",
)

PER_REC_FATAL_MARKERS = (
    "safety_audit=",
    "reconcile=",
    "partial_fill",
    "stale_account_snapshot",
    "duplicate_order_risk:",
    "missing_limit_price",
)


def is_test_plan_mode_active(config: RecommendationConfig) -> bool:
    return bool(config.allow_test_plan_order and config.ignore_safety_block_for_test)


def adjust_operational_risk_for_test_plan(
    risk_context: OperationalRiskContext,
    config: RecommendationConfig,
) -> OperationalRiskContext:
    """Downgrade safety_audit BLOCKED to warning for test plan only (both flags required)."""
    if not is_test_plan_mode_active(config):
        return risk_context
    blocked = list(risk_context.blocked_reasons)
    warnings = list(risk_context.warnings)
    safety_downgraded = False
    kept_blocked: list[str] = []
    for reason in blocked:
        if reason.startswith(SAFETY_BLOCK_PREFIX):
            safety_downgraded = True
            warnings.append(f"[test-plan] {reason} downgraded to warning (plan only, execute still guarded)")
            continue
        kept_blocked.append(reason)
    if safety_downgraded or "BLOCKED" in risk_context.safety_audit_status.upper():
        warnings.append(
            f"[test-plan] safety_audit_status={risk_context.safety_audit_status} "
            "(ignored for plan generation; live execute keeps safety/session guards)"
        )
    return replace(risk_context, blocked_reasons=kept_blocked, warnings=warnings)


def has_global_operational_block(
    risk_context: OperationalRiskContext,
    config: RecommendationConfig | None = None,
) -> bool:
    blocked = list(risk_context.blocked_reasons)
    if config is not None and is_test_plan_mode_active(config):
        blocked = [b for b in blocked if not b.startswith(SAFETY_BLOCK_PREFIX)]
    return bool(blocked)


def is_per_rec_fatal_blocked(
    blocked_reasons: list[str],
    config: RecommendationConfig | None = None,
) -> bool:
    markers = PER_REC_FATAL_MARKERS
    if config is not None and is_test_plan_mode_active(config):
        markers = tuple(m for m in PER_REC_FATAL_MARKERS if m != SAFETY_BLOCK_PREFIX)
    return any(any(marker in reason for marker in markers) for reason in blocked_reasons)


def plan_exclusion_reasons(rec: RecommendationResult) -> list[str]:
    reasons: list[str] = []
    if not rec.allowed_for_plan:
        reasons.append("allowed_for_plan=False")
    if rec.action not in {"BUY", "INCREASE"}:
        reasons.append(f"action_not_buy_increase:{rec.action}")
    if rec.suggested_limit_price is None or rec.suggested_limit_price <= 0:
        reasons.append("missing_or_invalid_limit_price")
    if rec.suggested_quantity <= 0:
        reasons.append("suggested_quantity_zero")
    if rec.estimated_order_value <= 0:
        reasons.append("estimated_order_value_zero")
    if rec.blocked_reasons:
        reasons.append(f"blocked_reasons={list(rec.blocked_reasons)}")
    if not reasons:
        reasons.append("included_in_plan")
    return reasons


def diagnose_recommendation_row(
    rec: RecommendationResult,
    *,
    config: RecommendationConfig,
    account: AccountContext,
    risk_context: OperationalRiskContext,
) -> dict[str, Any]:
    acct = rec.account_context if isinstance(rec.account_context, dict) else {}
    px = rec.suggested_limit_price
    return {
        "symbol": rec.symbol,
        "action": rec.action,
        "allowed_for_plan": rec.allowed_for_plan,
        "blocked_reasons": list(rec.blocked_reasons),
        "plan_exclusion_reasons": plan_exclusion_reasons(rec),
        "estimated_order_value": rec.estimated_order_value,
        "suggested_quantity": rec.suggested_quantity,
        "suggested_limit_price": px,
        "market_price_available": bool(px and px > 0),
        "confidence": rec.confidence,
        "priority": rec.priority,
        "source_signal_score": rec.source_signal_score,
        "capital_limit": config.capital_limit,
        "max_order_value": config.capital_limit,
        "safety_audit_status": risk_context.safety_audit_status,
        "reconcile_status": risk_context.reconcile_status,
        "risk_status": risk_context.risk_status,
        "global_operational_blocked_reasons": list(risk_context.blocked_reasons),
        "account_stale_snapshot": bool(account.stale_snapshot),
        "account_snapshot_age_minutes": account.snapshot_age_minutes,
        "account_snapshot_time": account.snapshot_time,
        "account_source": account.source,
        "recommendation_snapshot_stale": bool(acct.get("stale_snapshot")),
    }


def build_plan_order_diagnostic_report(result: RecommendationRunResult) -> dict[str, Any]:
    config = result.config
    account = result.account_context
    risk = result.operational_risk_context
    orders = list((result.order_plan or {}).get("orders") or [])
    rows = [diagnose_recommendation_row(rec, config=config, account=account, risk_context=risk) for rec in result.recommendations]
    return {
        "plan_order_count": len(orders),
        "recommendation_count": len(result.recommendations),
        "allowed_for_plan_count": sum(1 for r in result.recommendations if r.allowed_for_plan),
        "capital_limit": config.capital_limit,
        "max_order_value": config.capital_limit,
        "allow_test_plan_order": config.allow_test_plan_order,
        "ignore_safety_block_for_test": config.ignore_safety_block_for_test,
        "is_test_plan_mode_active": is_test_plan_mode_active(config),
        "global_operational_blocked": has_global_operational_block(risk, config),
        "global_operational_blocked_reasons": list(risk.blocked_reasons),
        "safety_audit_status": risk.safety_audit_status,
        "reconcile_status": risk.reconcile_status,
        "risk_status": risk.risk_status,
        "account_stale_snapshot": account.stale_snapshot,
        "account_snapshot_age_minutes": account.snapshot_age_minutes,
        "recommendations": rows,
        "test_plan_note": (result.order_plan or {}).get("test_plan_note"),
    }


def format_plan_diagnostic_console(report: dict[str, Any], *, debug: bool = False) -> str:
    lines = [
        "=== Plan Orders Diagnosis ===",
        f"Plan orders: {report.get('plan_order_count', 0)}",
        f"Recommendations: {report.get('recommendation_count', 0)}",
        f"allowed_for_plan: {report.get('allowed_for_plan_count', 0)}",
        f"capital_limit / max_order_value: {report.get('capital_limit')}",
        f"safety_audit: {report.get('safety_audit_status')}",
        f"reconcile: {report.get('reconcile_status')}",
        f"global operational blocked: {report.get('global_operational_blocked')}",
    ]
    if report.get("global_operational_blocked_reasons"):
        lines.append(f"global blocked_reasons: {report.get('global_operational_blocked_reasons')}")
    if report.get("account_stale_snapshot"):
        lines.append(f"account stale_snapshot: {report.get('account_stale_snapshot')} age={report.get('account_snapshot_age_minutes')}")
    if report.get("test_plan_note"):
        lines.append(f"test_plan_note: {report.get('test_plan_note')}")
    if int(report.get("plan_order_count") or 0) == 0:
        lines.append("Plan Orders가 0건입니다. daily-ai-trade-plan --debug-plan 으로 상세 확인하세요.")
    for row in report.get("recommendations") or []:
        if not debug and row.get("allowed_for_plan"):
            continue
        lines.append(
            f"- {row.get('symbol')}: action={row.get('action')} allowed={row.get('allowed_for_plan')} "
            f"qty={row.get('suggested_quantity')} px={row.get('suggested_limit_price')} "
            f"est_val={row.get('estimated_order_value')} priority={row.get('priority')} "
            f"price_ok={row.get('market_price_available')}"
        )
        lines.append(f"  plan_exclusion: {row.get('plan_exclusion_reasons')}")
        if row.get("blocked_reasons"):
            lines.append(f"  blocked: {row.get('blocked_reasons')}")
        if debug:
            lines.append(
                f"  account: stale={row.get('account_stale_snapshot')} "
                f"rec_stale={row.get('recommendation_snapshot_stale')} "
                f"confidence={row.get('confidence')} score={row.get('source_signal_score')}"
            )
    return "\n".join(lines)


def format_plan_diagnostic_markdown(report: dict[str, Any]) -> str:
    lines = [
        "## Plan Orders Diagnosis",
        "",
        f"- Plan orders: {report.get('plan_order_count', 0)}",
        f"- Recommendations: {report.get('recommendation_count', 0)}",
        f"- allowed_for_plan count: {report.get('allowed_for_plan_count', 0)}",
        f"- capital_limit / max_order_value: `{report.get('capital_limit')}`",
        f"- allow_test_plan_order: `{report.get('allow_test_plan_order')}`",
        f"- ignore_safety_block_for_test: `{report.get('ignore_safety_block_for_test')}`",
        f"- is_test_plan_mode_active: `{report.get('is_test_plan_mode_active')}`",
        f"- safety_audit_status: `{report.get('safety_audit_status')}`",
        f"- reconcile_status: `{report.get('reconcile_status')}`",
        f"- risk_status: `{report.get('risk_status')}`",
        f"- global operational blocked: `{report.get('global_operational_blocked')}`",
        f"- account stale_snapshot: `{report.get('account_stale_snapshot')}`",
        "",
        "| Symbol | Action | Allowed | Qty | Limit | Est value | Priority | Price OK | Plan exclusion |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report.get("recommendations") or []:
        lines.append(
            f"| {row.get('symbol')} | {row.get('action')} | {row.get('allowed_for_plan')} | "
            f"{row.get('suggested_quantity')} | {row.get('suggested_limit_price')} | "
            f"{row.get('estimated_order_value')} | {row.get('priority')} | {row.get('market_price_available')} | "
            f"`{row.get('plan_exclusion_reasons')}` |"
        )
    if int(report.get("plan_order_count") or 0) == 0:
        lines.extend(
            [
                "",
                "> Plan Orders가 0건입니다. `python main.py daily-ai-trade-plan --debug-plan` 으로 상세 확인하세요.",
            ]
        )
    if report.get("test_plan_note"):
        lines.append(f"- test_plan_note: `{report.get('test_plan_note')}`")
    return "\n".join(lines) + "\n"


def apply_allow_test_plan_order(
    recommendations: list[RecommendationResult],
    *,
    config: RecommendationConfig,
    account: AccountContext,
    risk_context: OperationalRiskContext,
) -> tuple[list[RecommendationResult], str | None]:
    if not config.allow_test_plan_order:
        return recommendations, None
    if has_global_operational_block(risk_context, config):
        return recommendations, "global_operational_block: reconcile/partial_fill 등 치명 차단 유지"
    if any(r.allowed_for_plan for r in recommendations):
        return recommendations, None

    max_val = float(config.capital_limit or account.withdrawable_cash or account.cash or 0.0)
    if max_val <= 0:
        return recommendations, "max_order_value/capital_limit not positive"

    candidates: list[RecommendationResult] = []
    for rec in recommendations:
        if is_per_rec_fatal_blocked(rec.blocked_reasons, config):
            continue
        if account.stale_snapshot:
            continue
        px = rec.suggested_limit_price
        if px is None or px <= 0:
            continue
        if rec.action not in {"BUY", "INCREASE", "HOLD", "SKIP"}:
            continue
        candidates.append(rec)

    if not candidates:
        return recommendations, "no_candidate_with_price_without_fatal_block"

    pick = sorted(
        candidates,
        key=lambda r: (r.priority, r.source_signal_score or 0.0, r.confidence),
        reverse=True,
    )[0]
    px = float(pick.suggested_limit_price or 0.0)
    qty = 1
    order_value = px * qty
    if order_value > max_val:
        return recommendations, f"min_qty_1_exceeds_max_order_value:{order_value:.0f}>{max_val:.0f}"

    action = "BUY" if pick.action in {"BUY", "INCREASE", "HOLD", "SKIP"} else pick.action
    new_blocked = [
        b
        for b in pick.blocked_reasons
        if b != "suggested_quantity_zero" and not (is_test_plan_mode_active(config) and b.startswith(SAFETY_BLOCK_PREFIX))
    ]
    test_notes = ["MVP: score/confidence 완화로 테스트 주문안 1건 포함"]
    if is_test_plan_mode_active(config):
        test_notes.append("MVP: safety_audit BLOCKED는 주문안 생성용 warning으로만 downgrade (실행 가드 유지)")
    test_notes.append("장외시간: Telegram 승인만 가능, 실주문은 장중 execute path에서만")
    updated = replace(
        pick,
        action=action,
        action_label="매수 후보 (test-plan)",
        suggested_quantity=qty,
        estimated_order_value=order_value,
        allowed_for_plan=True,
        blocked_reasons=new_blocked,
        priority=max(int(pick.priority), 1),
        reason=f"{pick.reason} [allow-test-plan-order]",
        risk_notes=[*pick.risk_notes, *test_notes],
    )
    out = [updated if r.symbol == pick.symbol else r for r in recommendations]
    return out, f"injected_test_plan_order:{pick.symbol}:qty={qty}:value={order_value:.0f}"
