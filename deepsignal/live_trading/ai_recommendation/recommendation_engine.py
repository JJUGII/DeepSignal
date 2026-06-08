"""AI live trade recommendation engine.

This module creates recommendations and PENDING_APPROVAL order-plan files only.
It never invokes live-approve, --execute, or KIS order-cash POST.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from deepsignal.live_trading.ai_recommendation.live_account_context import (
    load_local_account_context,
    load_network_account_context,
)
from deepsignal.live_trading.ai_recommendation.order_plan_builder import build_ai_live_order_plan
from deepsignal.live_trading.ai_recommendation.plan_order_diagnostics import (
    adjust_operational_risk_for_test_plan,
    apply_allow_test_plan_order,
    build_plan_order_diagnostic_report,
    is_test_plan_mode_active,
)
from deepsignal.live_trading.ai_recommendation.performance_context import load_operational_risk_context
from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    OperationalRiskContext,
    RecommendationConfig,
    RecommendationResult,
    RecommendationRunResult,
)
from deepsignal.live_trading.ai_recommendation.recommendation_quality import (
    apply_recommendation_quality_gates,
    build_score_breakdown,
)
from deepsignal.live_trading.ai_recommendation.recommendation_report import render_recommendation_markdown


ACTION_LABELS = {
    "BUY": "매수 후보",
    "SELL": "매도 후보",
    "HOLD": "보유",
    "REDUCE": "축소 후보",
    "INCREASE": "증액 후보",
    "SKIP": "제외",
}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _symbol_set(symbols: list[str] | None) -> set[str] | None:
    if not symbols:
        return None
    out = {str(s).strip().upper() for s in symbols if str(s).strip()}
    return out or None


def _position_map(account: AccountContext) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in account.positions:
        sym = str(row.get("symbol") or "").strip().upper()
        if sym:
            out[sym] = row
    return out


def _macro_context(db_path: str) -> dict[str, Any]:
    from deepsignal.scoring.macro_scorer import MacroScorer
    from deepsignal.storage.database import fetch_latest_economic_indicators

    result = MacroScorer().calculate_macro_score(fetch_latest_economic_indicators(db_path))
    return {
        "macro_score": getattr(result, "macro_score", None),
        "market_regime": getattr(result, "market_regime", "unknown"),
        "confidence": getattr(result, "confidence", None),
        "reason": getattr(result, "reason", ""),
    }


def _is_risk_off(macro: dict[str, Any]) -> bool:
    regime = str(macro.get("market_regime") or "").lower()
    score = _float(macro.get("macro_score"), 0.0)
    return "risk_off" in regime or "risk-off" in regime or score <= -40.0


def _latest_price(db_path: str, symbol: str, position: dict[str, Any] | None) -> float | None:
    from deepsignal.storage.database import fetch_latest_market_price

    row = fetch_latest_market_price(db_path, symbol, source="yfinance")
    if row and row.get("close") is not None:
        px = _float(row.get("close"), 0.0)
        if px > 0:
            return px
    if position:
        px = _float(position.get("current_price"), 0.0)
        if px > 0:
            return px
    return None


def _buy_score_floor() -> float:
    """매수 판정 점수 기준 — 투자공격성 다이얼(DEEPSIGNAL_STOCK_MIN_SCORE) 연동. 기본 60."""
    import os as _o
    try:
        return float(_o.environ.get("DEEPSIGNAL_STOCK_MIN_SCORE", "60") or "60")
    except ValueError:
        return 60.0


def _base_action(signal_action: str, score: float | None, current_qty: int, current_weight: float, target_weight: float, confidence: float) -> str:
    action_u = str(signal_action or "").upper()
    sc = None if score is None else float(score)
    if confidence < 0.25:
        return "HOLD" if current_qty > 0 else "SKIP"
    if action_u == "SELL_CANDIDATE" or (sc is not None and sc <= -60.0):
        if current_qty <= 0:
            return "SKIP"
        return "SELL" if sc is not None and sc <= -75.0 else "REDUCE"
    if action_u == "BUY_CANDIDATE" or (sc is not None and sc >= _buy_score_floor()):
        if current_qty <= 0:
            return "BUY"
        if current_weight + 0.02 < target_weight:
            return "INCREASE"
        return "HOLD"
    return "HOLD" if current_qty > 0 else "SKIP"


def build_recommendations(
    *,
    db_path: str,
    account: AccountContext,
    macro_context: dict[str, Any],
    risk_context: OperationalRiskContext,
    config: RecommendationConfig,
) -> list[RecommendationResult]:
    from deepsignal.storage.database import (
        fetch_distinct_market_symbols,
        fetch_latest_signals,
        load_recent_real_orders,
    )

    scan_limit = max(100, int(getattr(config, "signal_scan_limit", 500) or 500))
    raw_signals = fetch_latest_signals(db_path, limit=scan_limit)
    selected = _symbol_set(config.symbols)
    positions = _position_map(account)
    signals = {str(s.get("symbol") or "").strip().upper(): s for s in raw_signals if str(s.get("symbol") or "").strip()}
    symbols = set(signals) | set(positions)
    if getattr(config, "include_market_price_universe", False):
        symbols |= set(
            fetch_distinct_market_symbols(
                db_path,
                limit=int(getattr(config, "market_price_universe_limit", 2000) or 2000),
                lookback_days=int(getattr(config, "market_price_lookback_days", 30) or 30),
            )
        )
    extra = _symbol_set(getattr(config, "extra_symbols", None))
    if extra:
        symbols |= extra
    if selected is not None:
        symbols &= selected

    # 국내 전용 필터: 한국주식은 숫자코드(6자리)만. 미국종목(MSFT/AAPL 등)이
    # market_prices 유니버스·signals를 통해 섞여 들어오는 것을 계획 단계에서 제외한다.
    # (실행 가드도 6자리만 허용하므로 미국종목은 어차피 주문 불가 → 슬롯 낭비·혼란 방지)
    # 보유 종목은 매도 판단을 위해 항상 유지. KIS_STOCK_DOMESTIC_ONLY=false 면 비활성.
    import os as _os_dom
    import re as _re_dom
    if _os_dom.environ.get("KIS_STOCK_DOMESTIC_ONLY", "true").strip().lower() not in ("false", "0", "no"):
        _hold = set(positions)
        symbols = {
            s for s in symbols
            if s in _hold or _re_dom.fullmatch(r"\d{1,6}", str(s).strip())
        }

    equity = _float(account.total_equity, 0.0)
    if equity <= 0:
        equity = _float(account.cash, 0.0) + sum(_float(p.get("market_value"), 0.0) for p in positions.values())
    equity = max(equity, 1.0)
    risk_off = _is_risk_off(macro_context)
    capital_remaining = max(0.0, _float(config.capital_limit, _float(account.withdrawable_cash, _float(account.cash, 0.0))))

    out: list[RecommendationResult] = []
    for symbol in sorted(symbols):
        signal = signals.get(symbol, {})
        position = positions.get(symbol, {})
        score = signal.get("final_score")
        score_f = None if score is None else _float(score, 0.0)
        confidence = max(0.0, min(1.0, _float(signal.get("confidence"), 0.0)))
        qty = int(_float(position.get("quantity"), 0.0))
        current_value = _float(position.get("market_value"), 0.0)
        if current_value <= 0 and qty > 0:
            current_value = qty * _float(position.get("current_price"), 0.0)
        current_weight = current_value / equity if equity > 0 else 0.0
        target_weight = 0.0
        if score_f is not None and score_f > 0:
            target_weight = min(config.max_position_weight, (score_f / 100.0) * config.max_position_weight)
        if risk_off and target_weight > 0:
            target_weight *= 0.5

        action = _base_action(str(signal.get("action") or ""), score_f, qty, current_weight, target_weight, confidence)
        px = _latest_price(db_path, symbol, position)
        desired_value = max(0.0, target_weight * equity)
        order_value = 0.0
        suggested_qty = 0
        if action in {"BUY", "INCREASE"} and px and px > 0:
            order_value = min(max(0.0, desired_value - current_value), capital_remaining)
            suggested_qty = int(math.floor(order_value / px))
            order_value = float(suggested_qty) * float(px)
            capital_remaining = max(0.0, capital_remaining - order_value)
        elif action == "REDUCE":
            suggested_qty = max(1, int(math.floor(qty * 0.5))) if qty > 0 else 0
            order_value = float(suggested_qty) * float(px or _float(position.get("current_price"), 0.0))
        elif action == "SELL":
            suggested_qty = max(0, qty)
            order_value = float(suggested_qty) * float(px or _float(position.get("current_price"), 0.0))

        blocked = list(risk_context.blocked_reasons)
        risk_notes = list(risk_context.warnings)
        recent_orders = load_recent_real_orders(db_path, broker=config.broker, symbol=symbol, since_minutes=config.recent_order_minutes)
        if recent_orders:
            blocked.append(f"duplicate_order_risk:{symbol}")
        if account.stale_snapshot:
            blocked.append("stale_account_snapshot")
        if "BLOCKED" in risk_context.safety_audit_status.upper() and not is_test_plan_mode_active(config):
            blocked.append(f"safety_audit={risk_context.safety_audit_status}")
        if px is None and action in {"BUY", "INCREASE", "REDUCE", "SELL"}:
            blocked.append("missing_limit_price")
        if action in {"BUY", "INCREASE"} and suggested_qty <= 0:
            blocked.append("suggested_quantity_zero")
        if risk_off and action in {"BUY", "INCREASE"}:
            risk_notes.append("macro risk_off: 신규/증액 BUY 규모를 50% 축소")

        allowed = action in {"BUY", "INCREASE"} and not blocked and suggested_qty > 0 and bool(px and px > 0)
        priority = int(max(0.0, min(100.0, (score_f or 0.0) + confidence * 20.0)))
        if risk_context.warnings:
            priority = max(0, priority - 10)
        if risk_off and action in {"BUY", "INCREASE"}:
            priority = max(0, priority - 15)
        if blocked:
            priority = 0

        reason = str(signal.get("action") or "NO_SIGNAL")
        if score_f is not None:
            reason += f", final_score={score_f:.1f}"
        if signal.get("signal_date"):
            reason += f", signal_date={signal.get('signal_date')}"
        if not signal:
            reason = "No latest signal; position/account context only"

        out.append(
            RecommendationResult(
                symbol=symbol,
                action=action,
                action_label=ACTION_LABELS.get(action, action),
                confidence=confidence,
                priority=priority,
                reason=reason,
                risk_notes=risk_notes,
                current_quantity=qty,
                current_value=float(current_value),
                current_weight=float(current_weight),
                target_weight=float(target_weight),
                suggested_quantity=int(suggested_qty),
                suggested_limit_price=float(px) if px is not None else None,
                estimated_order_value=float(order_value),
                source_signal_score=score_f,
                macro_context=dict(macro_context),
                account_context={
                    "snapshot_time": account.snapshot_time,
                    "stale_snapshot": account.stale_snapshot,
                    "snapshot_age_minutes": account.snapshot_age_minutes,
                },
                blocked_reasons=sorted(set(blocked)),
                allowed_for_plan=allowed,
                score_breakdown=build_score_breakdown(signal, macro_context),
            )
        )

    out = apply_recommendation_quality_gates(
        out,
        db_path=db_path,
        account=account,
        signals=signals,
        macro_context=macro_context,
        config=config,
    )
    out.sort(key=lambda r: (r.allowed_for_plan, r.priority, r.estimated_order_value), reverse=True)
    return out[: max(1, int(config.max_recommendations))]


def _write_outputs(result: RecommendationRunResult, output_dir: str | Path) -> tuple[Path, Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    ts = datetime.fromisoformat(result.generated_at).strftime("%Y%m%d_%H%M%S")
    rec_json = root / f"ai_live_trade_recommendation_{ts}.json"
    plan_json = root / f"live_order_plan_ai_{ts}.json"
    md_path = root / "AI_LIVE_TRADE_RECOMMENDATION.md"
    result.output_files.update(
        {
            "recommendation_json": rec_json.name,
            "order_plan_json": plan_json.name,
            "markdown": md_path.name,
        }
    )
    rec_json.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from deepsignal.live_trading.time_utils import stamp_daily_ai_payload

    plan_body = result.order_plan if isinstance(result.order_plan, dict) else {"order_plan": result.order_plan}
    plan_json.write_text(json.dumps(stamp_daily_ai_payload(plan_body), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_recommendation_markdown(result), encoding="utf-8")
    return rec_json, plan_json, md_path


def run_ai_live_recommendation(
    db_path: str,
    *,
    config: RecommendationConfig,
    network: bool = False,
) -> tuple[RecommendationRunResult, Path, Path, Path]:
    if network:
        account = load_network_account_context(
            db_path,
            broker=config.broker,
            output_dir=config.output_dir,
            stale_minutes=config.stale_snapshot_minutes,
        )
        account.source = "kis_network_safe_mode"
    else:
        account = load_local_account_context(db_path, broker=config.broker, stale_minutes=config.stale_snapshot_minutes)
    macro = _macro_context(db_path)
    risk_context = adjust_operational_risk_for_test_plan(
        load_operational_risk_context(config.output_dir),
        config,
    )
    recs = build_recommendations(
        db_path=db_path,
        account=account,
        macro_context=macro,
        risk_context=risk_context,
        config=config,
    )
    test_note: str | None = None
    recs, test_note = apply_allow_test_plan_order(
        recs,
        config=config,
        account=account,
        risk_context=risk_context,
    )
    generated_at = datetime.now().isoformat(timespec="seconds")
    plan = build_ai_live_order_plan(recs, config=config, account_context=account, generated_at=generated_at)
    if test_note:
        plan["test_plan_note"] = test_note
        if config.allow_test_plan_order:
            plan["warnings"] = list(plan.get("warnings") or []) + [f"allow-test-plan-order: {test_note}"]
    if is_test_plan_mode_active(config):
        plan["test_plan_mode"] = {
            "allow_test_plan_order": True,
            "ignore_safety_block_for_test": True,
            "execution_requires_trading_session": True,
            "safety_downgraded_for_plan_only": True,
            "note": "장외시간 Telegram 승인 가능; 실주문은 장중 execute path에서만",
        }
    status = "AI_RECOMMENDATION_READY" if plan.get("orders") else "AI_RECOMMENDATION_NO_PLAN_ORDERS"
    result = RecommendationRunResult(
        generated_at=generated_at,
        status=status,
        config=config,
        account_context=account,
        macro_context=macro,
        operational_risk_context=risk_context,
        recommendations=recs,
        order_plan=plan,
        output_files={},
        plan_diagnostics=None,
    )
    result.plan_diagnostics = build_plan_order_diagnostic_report(result)
    paths = _write_outputs(result, config.output_dir)
    try:
        from deepsignal.live_trading.ai_recommendation.recommendation_outcomes import (
            outcomes_db_path,
            record_recommendation_run,
            refresh_recommendation_outcomes,
        )

        odb = outcomes_db_path(config.output_dir)
        record_recommendation_run(result, outcomes_db=odb)
        refresh_recommendation_outcomes(db_path, odb)
    except Exception:
        pass
    return result, *paths


def format_ai_recommendation_console(result: RecommendationRunResult, rec_json: Path, plan_json: Path, md_path: Path) -> str:
    return "\n".join(
        [
            "DeepSignal AI live recommendation created",
            f"Status: {result.status}",
            f"Recommendations: {len(result.recommendations)}",
            f"Plan Orders: {len(result.order_plan.get('orders') or [])}",
            f"Markdown: {md_path.as_posix()}",
            f"Recommendation JSON: {rec_json.as_posix()}",
            f"Order Plan JSON: {plan_json.as_posix()}",
            "Note: no live-approve call, no --execute call, no KIS order-cash POST, and no market orders.",
        ]
    )
