"""Tests for inactive-window auto execution without Telegram approval."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deepsignal.live_trading.approved_execution import ApprovedExecutionResult
from deepsignal.live_trading.inactive_auto_execute import (
    execute_kis_plan_inactive_auto,
    try_execute_pending_kis_in_inactive_window,
)
from deepsignal.live_trading.operator_inactive_window import OperatorInactiveConfig
from deepsignal.live_trading.telegram_approval import APPROVAL_STATUS_PENDING, TelegramApprovalConfig


def _write_plan(tmp_path: Path) -> Path:
    plan = {
        "status": "PENDING_APPROVAL",
        "approval_required": True,
        "orders": [
            {
                "symbol": "005930",
                "side": "BUY",
                "estimated_qty": 1,
                "estimated_price": 70000.0,
                "estimated_order_value": 70000.0,
            }
        ],
    }
    path = tmp_path / "live_order_plan_ai_test.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_execute_kis_plan_inactive_auto(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path)
    monkeypatch.setattr(
        "deepsignal.live_trading.inactive_auto_execute.validate_plan_limits",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.inactive_auto_execute.load_kis_config_from_env",
        lambda: MagicMock(env="live"),
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.inactive_auto_execute.validate_kis_config",
        lambda _c: ([], []),
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.inactive_auto_execute.KISBroker",
        lambda *_a, **_k: MagicMock(),
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.inactive_auto_execute.execute_live_order_plan",
        lambda *_a, **_k: {"success": True, "status": "KIS_LIVE_ORDER_COMPLETED", "actual_order_attempted": True},
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.inactive_auto_execute.write_live_approval_audit_log",
        lambda _p, **_: tmp_path / "live_audit.json",
    )

    tg = TelegramApprovalConfig(output_dir=str(tmp_path))
    result = execute_kis_plan_inactive_auto(
        plan_path,
        db_path=str(tmp_path / "x.db"),
        output_dir=tmp_path,
        tg_config=tg,
    )
    assert result.success is True
    assert result.status == "INACTIVE_AUTO_COMPLETED"


def test_try_execute_pending_in_inactive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path)
    state = {
        "token": "tok123",
        "status": APPROVAL_STATUS_PENDING,
        "plan_path": plan_path.as_posix(),
        "expires_at": datetime(2099, 1, 1).isoformat(),
    }
    (tmp_path / "TELEGRAM_APPROVAL_STATE.json").write_text(json.dumps(state), encoding="utf-8")

    cfg = OperatorInactiveConfig(enabled=True)
    monkeypatch.setattr(
        "deepsignal.live_trading.inactive_auto_execute.is_inactive_auto_execute_active",
        lambda **_k: True,
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.inactive_auto_execute.execute_kis_plan_inactive_auto",
        lambda *_a, **_k: ApprovedExecutionResult(
            request_id="inactive_auto",
            success=True,
            status="INACTIVE_AUTO_COMPLETED",
            errors=[],
            warnings=[],
            audit_json_path="",
            audit_markdown_path="",
            execution_result={},
        ),
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.inactive_auto_execute.notify_inactive_kis_execution",
        lambda **_k: {"ok": True},
    )

    out = try_execute_pending_kis_in_inactive_window(
        tmp_path,
        db_path=str(tmp_path / "x.db"),
        tg_config=TelegramApprovalConfig(output_dir=str(tmp_path)),
        inactive_cfg=cfg,
    )
    assert out is not None
    assert out.success is True
