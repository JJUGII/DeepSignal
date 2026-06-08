from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.approved_execution import ApprovedExecutionResult
from deepsignal.live_trading.telegram_operator_messages import (
    collect_judgment_reasons,
    display_symbol_name,
    format_operator_approval_request_text,
    format_operator_daily_report_text,
    format_operator_execution_fail_text,
    format_operator_execution_result_text,
    format_operator_no_orders_text,
    humanize_execution_error,
    humanize_judgment_reason,
    load_symbol_name_map,
)


def test_symbol_name_mapping_from_json() -> None:
    names = load_symbol_name_map()
    assert display_symbol_name("005930", names) == "삼성전자"
    assert display_symbol_name("AAPL", names) == "애플"
    assert display_symbol_name("NVDA", names) == "엔비디아"
    assert display_symbol_name("999999", names) == "999999"


def test_humanize_judgment_filters_debug() -> None:
    assert humanize_judgment_reason("AI score: 0.82") is None
    assert humanize_judgment_reason("safety_audit=BLOCKED downgraded") is None
    assert humanize_judgment_reason("allow-test-plan-order injected") is None
    assert humanize_judgment_reason("BUY: BUY_CANDIDATE, final_score=80") is None
    assert humanize_judgment_reason("단기 모멘텀 상승") is not None


def test_approval_message_snapshot(tmp_path: Path) -> None:
    plan = {
        "orders": [
            {
                "symbol": "005930",
                "side": "BUY",
                "order_type": "LIMIT",
                "limit_price": 276000,
                "estimated_price": 276000,
                "estimated_qty": 1,
                "estimated_order_value": 276000,
                "ai_reasons": [
                    "단기 모멘텀 상승",
                    "BUY: final_score=82",
                    "MVP: test-plan injected",
                ],
            }
        ]
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    request = type("Req", (), {"token": "t"})()
    text = format_operator_approval_request_text(request, plan_path=path)
    assert "[DeepSignal AI 매매 승인]" in text
    assert "삼성전자" in text
    assert "005930" not in text
    assert "매수" in text
    assert "276,000원 이하" in text
    assert "약 27만 6천원" in text
    assert "AI score" not in text
    assert "final_score" not in text
    assert "test-plan" not in text
    assert "downgraded" not in text
    assert "승인하면 실제 주문이 실행됩니다" in text


def test_execution_success_and_fail_korean() -> None:
    ok = format_operator_execution_result_text(
        execution=ApprovedExecutionResult(
            request_id="t",
            success=True,
            status="EXECUTED",
            errors=[],
            warnings=[],
            audit_json_path="a.json",
            audit_markdown_path="a.md",
            execution_result={
                "results": [{"status": "KIS_ORDER_SUBMITTED", "broker_order_id": "ORD123"}],
            },
        ),
        plan_context={"first_order": {"symbol": "NVDA", "estimated_qty": 2}},
    )
    assert "주문 실행 완료" in ok
    assert "엔비디아" in ok
    assert "ORD123" in ok
    assert "접수" in ok

    fail = format_operator_execution_fail_text(
        reason="trading session closed: after hours",
    )
    assert "주문 실패" in fail
    assert "장이 마감" in fail
    assert humanize_execution_error("duplicate_order_risk") == "중복 주문이 의심되어 차단되었습니다"


def test_daily_report_no_internal_status_codes() -> None:
    report = type(
        "R",
        (),
        {
            "summary": {
                "ai_recommendation_status": "AI_RECOMMENDATION_READY",
                "telegram_approval_status": "TELEGRAM_APPROVAL_AUTO_EXECUTED",
                "execution_status": "KIS_ORDER_SUBMITTED",
                "fill_status": "NOT_AVAILABLE",
                "order_submitted": True,
                "cash": 1_000_000,
            }
        },
    )()
    text = format_operator_daily_report_text(report)
    assert "오늘 매매 요약" in text
    assert "AI_RECOMMENDATION" not in text
    assert "TELEGRAM_APPROVAL" not in text
    assert "분석 완료" in text or "주문 실행 완료" in text
    assert "n/a" not in text


def test_no_orders_message() -> None:
    text = format_operator_no_orders_text()
    assert "오늘은 주문하지 않습니다" in text
    assert "debug" not in text.lower()


def test_collect_judgment_reasons_defaults_when_only_debug() -> None:
    reasons = collect_judgment_reasons(
        {"ai_reasons": ["allow-test-plan-order", "safety_audit downgraded"], "reason": "final_score=1"}
    )
    assert len(reasons) >= 2
    assert all("test-plan" not in r for r in reasons)
