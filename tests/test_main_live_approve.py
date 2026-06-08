"""main.py live-approve CLI."""

from __future__ import annotations

import json
from pathlib import Path

import main as main_mod
from deepsignal.live_trading.live_order_plan import LiveOrderItem, LiveOrderPlan, plan_to_json_dict


def _plan_path(tmp_path: Path) -> Path:
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
        warnings=[],
        status="PENDING_APPROVAL",
        approval_required=True,
        dry_run=True,
    )
    p = tmp_path / "live_order_plan_20260515.json"
    p.write_text(json.dumps(plan_to_json_dict(plan), ensure_ascii=False), encoding="utf-8")
    return p


def test_main_live_approve_dry_run_smoke(tmp_path: Path) -> None:
    p = _plan_path(tmp_path)
    rc = main_mod.main(
        [
            "live-approve",
            "--plan",
            str(p),
            "--approved",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    audits = list(tmp_path.glob("live_approval_audit_*.json"))
    assert len(audits) == 1
    data = json.loads(audits[0].read_text(encoding="utf-8"))
    assert data.get("status") == "DRY_RUN_COMPLETED"
    assert data.get("실제_주문_없음") is True


def test_main_live_approve_no_dry_run_exits_nonzero(tmp_path: Path) -> None:
    p = _plan_path(tmp_path)
    rc = main_mod.main(
        [
            "live-approve",
            "--plan",
            str(p),
            "--approved",
            "--no-dry-run",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 1


def test_main_live_approve_execute_exits_nonzero(tmp_path: Path) -> None:
    p = _plan_path(tmp_path)
    rc = main_mod.main(
        [
            "live-approve",
            "--plan",
            str(p),
            "--approved",
            "--execute",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 1
    audits = list(tmp_path.glob("live_approval_audit_*.json"))
    assert len(audits) >= 1
    data = json.loads(sorted(audits)[-1].read_text(encoding="utf-8"))
    assert data.get("status") == "EXECUTE_REQUIRES_KIS_BROKER"
