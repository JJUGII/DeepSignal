from __future__ import annotations

import json
from pathlib import Path

import main as main_mod
import pytest

from deepsignal.live_trading.ai_recommendation.plan_order_diagnostics import (
    adjust_operational_risk_for_test_plan,
    apply_allow_test_plan_order,
    format_plan_diagnostic_console,
    has_global_operational_block,
    plan_exclusion_reasons,
    is_test_plan_mode_active,
)
from deepsignal.live_trading.ai_recommendation.recommendation_engine import build_recommendations
from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    OperationalRiskContext,
    RecommendationConfig,
    RecommendationResult,
    RecommendationRunResult,
)
from deepsignal.live_trading.telegram_approval import APPROVAL_STATUS_BLOCKED, create_telegram_approval_request, TelegramApprovalConfig
from deepsignal.collector.market.market_data import MarketData
from deepsignal.scoring.signal_scorer import SignalResult
from deepsignal.storage.database import init_database, insert_market_prices, insert_signal_result


def _rec(**kwargs) -> RecommendationResult:
    base = dict(
        symbol="005930",
        action="SKIP",
        action_label="제외",
        confidence=0.1,
        priority=0,
        reason="low confidence",
        risk_notes=[],
        current_quantity=0,
        current_value=0.0,
        current_weight=0.0,
        target_weight=0.0,
        suggested_quantity=0,
        suggested_limit_price=50_000.0,
        estimated_order_value=0.0,
        source_signal_score=30.0,
        macro_context={},
        account_context={"stale_snapshot": False},
        blocked_reasons=["suggested_quantity_zero"],
        allowed_for_plan=False,
    )
    base.update(kwargs)
    return RecommendationResult(**base)


def test_plan_exclusion_reasons_lists_blockers() -> None:
    reasons = plan_exclusion_reasons(_rec())
    assert "allowed_for_plan=False" in reasons
    assert any("action_not_buy_increase" in r for r in reasons)


def test_debug_plan_console_shows_blocked_rows() -> None:
    report = {
        "plan_order_count": 0,
        "recommendation_count": 1,
        "allowed_for_plan_count": 0,
        "capital_limit": 100_000.0,
        "safety_audit_status": "OK",
        "reconcile_status": "OK",
        "global_operational_blocked": False,
        "recommendations": [
            {
                "symbol": "005930",
                "action": "SKIP",
                "allowed_for_plan": False,
                "suggested_quantity": 0,
                "suggested_limit_price": 50000,
                "estimated_order_value": 0,
                "priority": 0,
                "market_price_available": True,
                "plan_exclusion_reasons": ["allowed_for_plan=False"],
                "blocked_reasons": ["suggested_quantity_zero"],
            }
        ],
    }
    text = format_plan_diagnostic_console(report, debug=True)
    assert "Plan Orders Diagnosis" in text
    assert "005930" in text
    assert "plan_exclusion" in text


def test_allow_test_plan_order_injects_one_buy(tmp_path: Path) -> None:
    db = str(tmp_path / "t.db")
    init_database(db)
    insert_signal_result(
        db,
        SignalResult(
            symbol="005930",
            signal_date="2026-05-17",
            technical_score=30.0,
            news_score=None,
            macro_score=None,
            final_score=30.0,
            action="BUY_CANDIDATE",
            confidence=0.1,
            reason="weak",
            raw={},
        ),
    )
    insert_market_prices(
        db,
        [
            MarketData(
                symbol="005930",
                trade_date="2026-05-17",
                open=50_000,
                high=50_000,
                low=50_000,
                close=50_000,
                adjusted_close=50_000,
                volume=1,
                source="yfinance",
                raw={},
            )
        ],
    )
    account = AccountContext(cash=100_000, withdrawable_cash=100_000, total_equity=100_000)
    cfg = RecommendationConfig(output_dir=str(tmp_path), capital_limit=100_000.0, allow_test_plan_order=True)
    recs = build_recommendations(
        db_path=db,
        account=account,
        macro_context={"market_regime": "neutral", "macro_score": 0},
        risk_context=OperationalRiskContext(),
        config=cfg,
    )
    assert not any(r.allowed_for_plan for r in recs)
    updated, note = apply_allow_test_plan_order(recs, config=cfg, account=account, risk_context=OperationalRiskContext())
    assert note and "injected_test_plan_order" in note
    assert any(r.allowed_for_plan and r.suggested_quantity == 1 for r in updated)


def test_allow_test_plan_order_respects_max_order_value() -> None:
    rec = _rec(suggested_limit_price=200_000.0, blocked_reasons=[])
    cfg = RecommendationConfig(capital_limit=100_000.0, allow_test_plan_order=True)
    updated, note = apply_allow_test_plan_order(
        [rec],
        config=cfg,
        account=AccountContext(cash=100_000, withdrawable_cash=100_000),
        risk_context=OperationalRiskContext(),
    )
    assert note and "exceeds_max_order_value" in note
    assert not any(r.allowed_for_plan for r in updated)


def test_allow_test_plan_order_blocked_by_global_safety_without_ignore_flag() -> None:
    rec = _rec(action="BUY", allowed_for_plan=False, suggested_quantity=1, estimated_order_value=50_000)
    risk = OperationalRiskContext(blocked_reasons=["safety_audit=BLOCKED"], safety_audit_status="BLOCKED")
    updated, note = apply_allow_test_plan_order(
        [rec],
        config=RecommendationConfig(allow_test_plan_order=True, capital_limit=100_000),
        account=AccountContext(cash=100_000, withdrawable_cash=100_000),
        risk_context=risk,
    )
    assert note and "global_operational_block" in note
    assert not any(r.allowed_for_plan for r in updated)


def test_ignore_safety_requires_both_flags() -> None:
    risk = OperationalRiskContext(blocked_reasons=["safety_audit=BLOCKED"], safety_audit_status="BLOCKED")
    cfg_allow_only = RecommendationConfig(allow_test_plan_order=True, ignore_safety_block_for_test=False)
    cfg_both = RecommendationConfig(allow_test_plan_order=True, ignore_safety_block_for_test=True)
    assert not is_test_plan_mode_active(cfg_allow_only)
    assert is_test_plan_mode_active(cfg_both)
    assert has_global_operational_block(risk, cfg_allow_only)
    assert not has_global_operational_block(risk, cfg_both)


def test_adjust_operational_risk_downgrades_safety_to_warning() -> None:
    risk = OperationalRiskContext(
        blocked_reasons=["safety_audit=BLOCKED", "reconcile=MISMATCH"],
        safety_audit_status="BLOCKED",
    )
    cfg = RecommendationConfig(allow_test_plan_order=True, ignore_safety_block_for_test=True)
    adjusted = adjust_operational_risk_for_test_plan(risk, cfg)
    assert not any(b.startswith("safety_audit=") for b in adjusted.blocked_reasons)
    assert any("reconcile=" in b for b in adjusted.blocked_reasons)
    assert any("downgraded" in w for w in adjusted.warnings)


def test_allow_test_plan_order_with_ignore_safety_injects_despite_blocked_audit() -> None:
    rec = _rec(
        action="SKIP",
        suggested_quantity=0,
        estimated_order_value=0.0,
        blocked_reasons=["safety_audit=BLOCKED", "suggested_quantity_zero"],
    )
    risk = OperationalRiskContext(blocked_reasons=["safety_audit=BLOCKED"], safety_audit_status="BLOCKED")
    cfg = RecommendationConfig(
        allow_test_plan_order=True,
        ignore_safety_block_for_test=True,
        capital_limit=100_000.0,
    )
    updated, note = apply_allow_test_plan_order(
        [rec],
        config=cfg,
        account=AccountContext(cash=100_000, withdrawable_cash=100_000),
        risk_context=adjust_operational_risk_for_test_plan(risk, cfg),
    )
    assert note and "injected_test_plan_order" in note
    assert any(r.allowed_for_plan and r.suggested_quantity == 1 for r in updated)


def _empty_plan(tmp_path: Path) -> Path:
    path = tmp_path / "empty_plan.json"
    path.write_text(
        json.dumps(
            {
                "date": "2026-05-19",
                "status": "PENDING_APPROVAL",
                "orders": [],
                "warnings": [],
                "dry_run": True,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_telegram_approval_request_blocked_when_zero_orders(tmp_path: Path) -> None:
    plan = _empty_plan(tmp_path)
    cfg = TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="123")
    request, _, _ = create_telegram_approval_request(plan, cfg)
    assert request.status == APPROVAL_STATUS_BLOCKED
    assert request.order_count == 0


def test_main_telegram_request_zero_orders_blocked(tmp_path: Path, capsys) -> None:
    plan = _empty_plan(tmp_path)
    rc = main_mod.main(
        [
            "telegram-approval-request",
            "--plan",
            str(plan),
            "--output-dir",
            str(tmp_path),
            "--allowed-chat-id",
            "1234",
        ]
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert "차단" in out
    assert "0건" in out or "debug-plan" in out
