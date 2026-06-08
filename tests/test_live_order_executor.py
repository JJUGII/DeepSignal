"""live_order_executor: 계획 로드·검증·dry-run 실행."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepsignal.live_trading.broker_interface import (
    BrokerInterface,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
)
from deepsignal.live_trading.dry_run_broker import DryRunBroker
from deepsignal.live_trading.live_order_executor import (
    build_broker_order_requests,
    execute_live_order_plan,
    load_live_order_plan,
    validate_live_order_plan,
    write_live_approval_audit_log,
)
from deepsignal.live_trading.live_order_plan import LiveOrderItem, LiveOrderPlan, live_order_plan_from_dict, plan_to_json_dict


def _valid_plan_dict() -> dict:
    plan = LiveOrderPlan(
        date="2026-05-15",
        capital=300_000.0,
        investable_cash=270_000.0,
        cash_buffer=30_000.0,
        currency="USD",
        orders=[
            LiveOrderItem(
                symbol="AAPL",
                side="BUY",
                target_weight=0.2,
                target_value=50_000.0,
                estimated_price=190.2,
                estimated_qty=2,
                estimated_order_value=380.4,
                reason="test",
            )
        ],
        warnings=["w1"],
        status="PENDING_APPROVAL",
        approval_required=True,
        dry_run=True,
    )
    return plan_to_json_dict(plan)


def test_load_and_build_requests(tmp_path: Path) -> None:
    p = tmp_path / "live_order_plan_20260515.json"
    p.write_text(json.dumps(_valid_plan_dict(), ensure_ascii=False), encoding="utf-8")
    plan = load_live_order_plan(p)
    reqs = build_broker_order_requests(plan, source_plan_id="live_order_plan_20260515")
    assert len(reqs) == 1
    assert isinstance(reqs[0], BrokerOrderRequest)
    assert reqs[0].symbol == "AAPL"
    assert reqs[0].quantity == 2
    assert reqs[0].limit_price == pytest.approx(190.2)
    assert reqs[0].order_type == "LIMIT"


def test_validate_rejects_sell() -> None:
    d = _valid_plan_dict()
    d["orders"][0]["side"] = "SELL"
    plan = live_order_plan_from_dict(d)
    ok, errs = validate_live_order_plan(plan)
    assert ok is False
    assert any("BUY" in e for e in errs)


def test_validate_rejects_bad_qty() -> None:
    d = _valid_plan_dict()
    d["orders"][0]["estimated_qty"] = 0
    plan = live_order_plan_from_dict(d)
    ok, errs = validate_live_order_plan(plan)
    assert ok is False
    assert any("estimated_qty" in e for e in errs)


def test_validate_empty_orders() -> None:
    d = _valid_plan_dict()
    d["orders"] = []
    plan = live_order_plan_from_dict(d)
    ok, errs = validate_live_order_plan(plan)
    assert ok is False
    assert any("empty" in e.lower() for e in errs)


def test_execute_rejects_without_approval(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_valid_plan_dict()), encoding="utf-8")
    r = execute_live_order_plan(p, DryRunBroker(), approved=False, execute=False, dry_run=True)
    assert r["success"] is False
    assert r["status"] == "REJECTED_NOT_APPROVED"


def test_execute_blocked_when_execute_true(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_valid_plan_dict()), encoding="utf-8")
    r = execute_live_order_plan(p, DryRunBroker(), approved=True, execute=True, dry_run=True)
    assert r["success"] is False
    assert r["status"] == "EXECUTE_REQUIRES_KIS_BROKER"


def test_execute_rejects_non_dry_run_broker(tmp_path: Path) -> None:
    class OtherBroker(BrokerInterface):
        def connect(self) -> None:
            return

        def submit_order(self, order):
            return {}

        def get_positions(self) -> list[BrokerPosition]:
            return []

        def place_order(self, request: BrokerOrderRequest, *, execute: bool = False) -> BrokerOrderResult:
            raise AssertionError("must not be called")

    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_valid_plan_dict()), encoding="utf-8")
    r = execute_live_order_plan(p, OtherBroker(), approved=True, execute=False, dry_run=True)
    assert r["success"] is False
    assert r["status"] == "BROKER_NOT_ALLOWED"


def test_execute_dry_run_completed(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_valid_plan_dict()), encoding="utf-8")
    r = execute_live_order_plan(p, DryRunBroker(), approved=True, execute=False, dry_run=True)
    assert r["success"] is True
    assert r["status"] == "DRY_RUN_COMPLETED"
    assert len(r["results"]) == 1
    assert r["results"][0]["status"] == "DRY_RUN_ACCEPTED"


def test_execute_kis_safe_mode_completed(tmp_path: Path) -> None:
    from deepsignal.live_trading.kis_broker import KISBroker
    from deepsignal.live_trading.kis_config import KISConfig

    cfg = KISConfig(
        app_key="a",
        app_secret="b",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="paper",
    )
    p = tmp_path / "plan.json"
    d = {
        "date": "2026-05-15",
        "status": "PENDING_APPROVAL",
        "approval_required": True,
        "dry_run": True,
        "capital": 1.0,
        "investable_cash": 1.0,
        "cash_buffer": 0.0,
        "currency": "KRW",
        "orders": [
            {
                "symbol": "005930",
                "side": "BUY",
                "target_weight": 0.1,
                "target_value": 700000.0,
                "estimated_price": 70000.0,
                "estimated_qty": 10,
                "estimated_order_value": 700000.0,
                "reason": "t",
                "warnings": [],
            }
        ],
        "warnings": [],
    }
    p.write_text(json.dumps(d), encoding="utf-8")
    r = execute_live_order_plan(p, KISBroker(cfg, safe_mode=True), approved=True, execute=False, dry_run=True)
    assert r["success"] is True
    assert r["status"] == "KIS_SAFE_MODE_COMPLETED"
    assert r["results"][0]["status"] == "KIS_SAFE_MODE_BLOCKED"


def test_write_audit_creates_json(tmp_path: Path) -> None:
    ap = write_live_approval_audit_log(
        {"plan_path": "x", "status": "TEST", "orders": [], "results": [], "warnings": []},
        output_dir=tmp_path,
    )
    assert ap.exists()
    data = json.loads(ap.read_text(encoding="utf-8"))
    assert "timestamp" in data
