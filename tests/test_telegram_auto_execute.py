from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deepsignal.live_trading import telegram_approval as tg
from deepsignal.live_trading.approved_execution import ApprovedExecutionResult
from deepsignal.live_trading.telegram_auto_execute import (
    auto_execute_after_telegram_approval,
    format_approval_request_telegram_text,
    poll_telegram_approval_until_done,
)


def _write_plan(tmp_path: Path) -> Path:
    plan = {
        "date": "2026-05-19",
        "status": "PENDING_APPROVAL",
        "orders": [
            {
                "symbol": "005930",
                "side": "BUY",
                "limit_price": 50000,
                "estimated_price": 50000,
                "estimated_qty": 1,
                "estimated_order_value": 50000,
                "ai_confidence": 78,
                "ai_reasons": ["단기 모멘텀 상승", "거래량 증가"],
                "reason": "BUY signal",
            }
        ],
    }
    path = tmp_path / "live_order_plan_ai_latest.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_format_approval_request_telegram_text(tmp_path: Path) -> None:
    plan_path = _write_plan(tmp_path)
    request = tg.TelegramApprovalRequest(
        token="tok",
        plan_path=plan_path.as_posix(),
        plan_hash=tg.plan_sha256(plan_path),
        created_at="2026-05-19T10:00:00",
        expires_at="2026-05-19T10:10:00",
        status=tg.APPROVAL_STATUS_PENDING,
        order_count=1,
        total_order_value=70200,
        max_total_order_value=100_000,
        max_single_order_value=50_000,
        max_orders=1,
        allowed_chat_id="1234",
    )
    text = format_approval_request_telegram_text(request, plan_path=plan_path)
    assert "[DeepSignal AI 매매 승인]" in text
    assert "삼성전자" in text
    assert "매수" in text
    assert "AI score" not in text
    assert "판단 이유" in text


def test_poll_approve_triggers_auto_execute(tmp_path: Path, monkeypatch) -> None:
    plan_path = _write_plan(tmp_path)
    cfg = tg.TelegramApprovalConfig(
        output_dir=str(tmp_path),
        allowed_chat_id="1234",
        send=False,
    )
    request, _, _ = tg.create_telegram_approval_request(plan_path, cfg)
    state = json.loads((tmp_path / "TELEGRAM_APPROVAL_STATE.json").read_text(encoding="utf-8"))

    def fake_updates(**kwargs):
        return {
            "ok": True,
            "result": [
                {
                    "callback_query": {
                        "id": "cb1",
                        "data": f"tgapprove:approve:{state['token']}",
                        "message": {"chat": {"id": 1234}},
                    }
                }
            ],
        }

    monkeypatch.setattr(
        "deepsignal.live_trading.telegram_auto_execute.telegram_get_updates",
        fake_updates,
    )
    monkeypatch.setattr(tg, "telegram_answer_callback", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        "deepsignal.live_trading.telegram_auto_execute.telegram_api_post",
        lambda *a, **k: {"ok": True, "network_called": True},
    )

    mock_result = ApprovedExecutionResult(
        request_id=state["token"],
        success=True,
        status="EXECUTED",
        errors=[],
        warnings=[],
        audit_json_path=str(tmp_path / "execute_approved_audit_x.json"),
        audit_markdown_path=str(tmp_path / "execute_approved_audit_x.md"),
        live_approval_audit_path=str(tmp_path / "live_approval_audit_x.json"),
        execution_result={
            "success": True,
            "status": "DRY_RUN_COMPLETED",
            "results": [{"symbol": "005930", "status": "KIS_ORDER_SUBMITTED", "broker_order_id": "OID1", "message": "ok"}],
        },
    )
    runner = MagicMock(return_value=mock_result)

    outcome = poll_telegram_approval_until_done(
        tmp_path,
        db_path=":memory:",
        wait_seconds=5.0,
        poll_interval=0.1,
        auto_execute=True,
        execute_runner=runner,
    )

    assert outcome.outcome == "executed"
    runner.assert_called_once()
    audits = list(tmp_path.glob("telegram_approval_audit_*.json"))
    assert audits
    audit = json.loads(audits[0].read_text(encoding="utf-8"))
    assert audit.get("auto_executed") is True
    assert audit.get("status") == "TELEGRAM_APPROVAL_AUTO_EXECUTED"


def test_auto_execute_after_approval_updates_audit(tmp_path: Path, monkeypatch) -> None:
    plan_path = _write_plan(tmp_path)
    cfg = tg.TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234")
    _, _, _ = tg.create_telegram_approval_request(plan_path, cfg)
    state = json.loads((tmp_path / "TELEGRAM_APPROVAL_STATE.json").read_text(encoding="utf-8"))
    audit, audit_path = tg.handle_telegram_action(
        state=state,
        action=tg.ACTION_APPROVE,
        token=state["token"],
        chat_id="1234",
        output_dir=tmp_path,
    )
    mock_result = ApprovedExecutionResult(
        request_id=state["token"],
        success=False,
        status="BLOCKED",
        errors=["session closed"],
        warnings=[],
        audit_json_path=str(tmp_path / "execute_approved_audit_y.json"),
        audit_markdown_path=str(tmp_path / "execute_approved_audit_y.md"),
        execution_result={"success": False, "status": "LIVE_ORDER_BLOCKED", "errors": ["session closed"]},
    )
    result = auto_execute_after_telegram_approval(
        state=state,
        audit=audit,
        audit_path=audit_path,
        output_dir=tmp_path,
        db_path=":memory:",
        config=cfg,
        execute_runner=MagicMock(return_value=mock_result),
    )
    assert result.success is False
    saved = json.loads(audit_path.read_text(encoding="utf-8"))
    assert saved["status"] == "TELEGRAM_APPROVAL_AUTO_EXECUTION_FAILED"
    assert saved["manual_live_approve_required"] is False
