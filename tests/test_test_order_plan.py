"""test_order_plan.py — generate-test-order-plan."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepsignal.live_trading.live_order_executor import load_live_order_plan, validate_live_order_plan
from deepsignal.live_trading.telegram_approval import TelegramApprovalConfig, create_telegram_approval_request, validate_plan_limits
from deepsignal.live_trading.test_order_plan import (
    DEFAULT_OUTPUT_NAME,
    SmallLiveOrderPlanInput,
    build_test_order_plan_payload,
    validate_test_order_plan_inputs,
    write_test_order_plan,
)


def test_write_test_live_order_plan_json(tmp_path: Path) -> None:
    path = write_test_order_plan(
        SmallLiveOrderPlanInput(
            symbol="005930",
            quantity=1,
            limit_price=70_000.0,
            max_order_value=100_000.0,
            output_dir=str(tmp_path),
        )
    )

    assert path.name == DEFAULT_OUTPUT_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "PENDING_APPROVAL"
    assert data["approval_required"] is True
    assert data["dry_run"] is True
    assert data["generated_by"] == "test_order_plan"
    assert len(data["orders"]) == 1

    order = data["orders"][0]
    assert order["symbol"] == "005930"
    assert order["side"] == "BUY"
    assert order["order_type"] == "LIMIT"
    assert order["quantity"] == 1
    assert order["limit_price"] == 70_000.0
    assert order["estimated_qty"] == 1
    assert order["estimated_price"] == 70_000.0
    assert order["estimated_order_value"] == 70_000.0


def test_validate_live_order_plan_accepts_generated_plan(tmp_path: Path) -> None:
    path = write_test_order_plan(
        SmallLiveOrderPlanInput(symbol="005930", quantity=1, limit_price=70_000.0, output_dir=str(tmp_path))
    )
    plan = load_live_order_plan(path)
    ok, errors = validate_live_order_plan(plan)

    assert ok is True
    assert errors == []


def test_telegram_approval_request_reads_schema(tmp_path: Path) -> None:
    path = write_test_order_plan(
        SmallLiveOrderPlanInput(symbol="005930", quantity=1, limit_price=70_000.0, output_dir=str(tmp_path))
    )
    cfg = TelegramApprovalConfig(
        output_dir=str(tmp_path),
        allowed_chat_id="1234",
        max_orders=1,
        max_single_order_value=100_000.0,
        max_total_order_value=100_000.0,
        send=False,
    )

    req, json_path, md_path = create_telegram_approval_request(path, cfg)

    assert req.status == "PENDING"
    assert req.order_count == 1
    assert req.total_order_value == 70_000.0
    assert json_path.exists()
    assert md_path.exists()
    assert validate_plan_limits(path, cfg) == []


def test_max_order_value_exceed_fails() -> None:
    errors = validate_test_order_plan_inputs(
        symbol="005930",
        quantity=1,
        limit_price=70_000.0,
        max_order_value=50_000.0,
    )

    assert errors
    assert any("exceeds" in e for e in errors)


def test_write_raises_when_over_max_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceeds"):
        write_test_order_plan(
            SmallLiveOrderPlanInput(
                symbol="005930",
                quantity=1,
                limit_price=70_000.0,
                max_order_value=50_000.0,
                output_dir=str(tmp_path),
            )
        )


def test_limit_price_required() -> None:
    errors = validate_test_order_plan_inputs(
        symbol="005930",
        quantity=1,
        limit_price=0,
        max_order_value=100_000.0,
    )
    assert any("limit_price" in e for e in errors)
