from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.daily_ai_status_reader import NOT_AVAILABLE, read_daily_ai_workflow_status


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_daily_ai_status_reader_not_available(tmp_path: Path) -> None:
    status = read_daily_ai_workflow_status(tmp_path)

    assert status.plan_status == NOT_AVAILABLE
    assert status.approval_status == NOT_AVAILABLE
    assert "daily-ai-trade-plan" in status.next_action
    assert status.warnings


def test_daily_ai_status_reader_detects_files_and_next_action(tmp_path: Path) -> None:
    ts = "2026-05-19T08:00:00+09:00"
    (tmp_path / "AI_DAILY_TRADE_PLAN.md").write_text("# plan", encoding="utf-8")
    _write_json(
        tmp_path / "ai_daily_trade_plan_20260519_020000.json",
        {"status": "AI_DAILY_TRADE_PLAN_READY", "generated_at": ts},
    )
    _write_json(tmp_path / "live_order_plan_ai_latest.json", {"orders": [], "generated_at": ts})
    status = read_daily_ai_workflow_status(tmp_path)
    assert status.plan_status == "AI_DAILY_TRADE_PLAN_READY"
    assert "telegram-approval-request" in status.next_action

    _write_json(tmp_path / "telegram_approval_request_20260519_020100.json", {"status": "PENDING", "generated_at": ts})
    status = read_daily_ai_workflow_status(tmp_path)
    assert status.approval_request_status == "PENDING"
    assert "execute-last-approved" in status.next_action

    _write_json(
        tmp_path / "telegram_approval_audit_20260519_020200.json",
        {"status": "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED", "generated_at": ts},
    )
    status = read_daily_ai_workflow_status(tmp_path)
    assert status.approval_status == "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED"
    assert "execute-last-approved" in status.next_action

    _write_json(tmp_path / "execute_approved_audit_20260519_020300.json", {"status": "EXECUTE_APPROVED_COMPLETED", "generated_at": ts})
    status = read_daily_ai_workflow_status(tmp_path)
    assert status.execution_status == "EXECUTE_APPROVED_COMPLETED"
    assert "daily-ai-trade-report" in status.next_action

    (tmp_path / "AI_DAILY_TRADE_REPORT.md").write_text("# report", encoding="utf-8")
    _write_json(tmp_path / "ai_daily_trade_report_20260519_020400.json", {"status": "AI_DAILY_TRADE_REPORT_READY", "generated_at": ts})
    status = read_daily_ai_workflow_status(tmp_path, freshness_date="2026-05-19")
    assert status.report_status == "AI_DAILY_TRADE_REPORT_READY"
    assert "daily-ai-status" in status.next_action
    assert status.freshness.get("plan", {}).get("status") == "FRESH"


def test_daily_status_stale_plan_suggests_regenerate(tmp_path: Path) -> None:
    (tmp_path / "AI_DAILY_TRADE_PLAN.md").write_text("# plan", encoding="utf-8")
    _write_json(
        tmp_path / "ai_daily_trade_plan_20260519_020000.json",
        {"status": "AI_DAILY_TRADE_PLAN_READY", "generated_at": "2026-05-18T08:00:00+09:00"},
    )
    _write_json(tmp_path / "live_order_plan_ai_latest.json", {"generated_at": "2026-05-18T08:00:00+09:00"})

    status = read_daily_ai_workflow_status(tmp_path, freshness_date="2026-05-19")

    assert "daily-ai-trade-plan" in status.next_action
