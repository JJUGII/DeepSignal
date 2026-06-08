from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path

from deepsignal.live_trading.live_order_plan import LiveOrderItem, LiveOrderPlan, plan_to_json_dict
from deepsignal.live_trading.telegram_approval import (
    ACTION_APPROVE,
    ACTION_HALT_TODAY,
    ACTION_REJECT,
    APPROVAL_STATUS_PENDING,
    TelegramApprovalConfig,
    create_telegram_approval_request,
    handle_telegram_action,
    load_latest_request,
    parse_update_action,
    plan_sha256,
    validate_plan_limits,
    verify_approval_action,
)


def _plan_path(tmp_path: Path, *, order_value: float = 10_000.0, orders: int = 1) -> Path:
    plan = LiveOrderPlan(
        date="2026-05-19",
        capital=300_000.0,
        investable_cash=270_000.0,
        cash_buffer=30_000.0,
        currency="USD",
        orders=[
            LiveOrderItem(
                symbol=f"TST{i:03d}",
                side="BUY",
                target_weight=0.1,
                target_value=order_value,
                estimated_price=100.0,
                estimated_qty=int(order_value / 100.0),
                estimated_order_value=order_value,
                reason="test",
            )
            for i in range(orders)
        ],
        warnings=["operator review required"],
        status="PENDING_APPROVAL",
        approval_required=True,
        dry_run=True,
    )
    path = tmp_path / "live_order_plan_ai_20260519_010101.json"
    path.write_text(json.dumps(plan_to_json_dict(plan), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_plan_sha256_uses_exact_file_bytes(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(b'{"orders":[]}\n')

    assert plan_sha256(path) == "bfc78645fff96da7048eef69ec1296336e0fb7b59a16927f4ae72726fca12591"


def test_create_request_writes_state_markdown_and_dry_run_payload(tmp_path: Path) -> None:
    plan = _plan_path(tmp_path)
    cfg = TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234", send=False)

    req, json_path, md_path = create_telegram_approval_request(plan, cfg)

    assert req.status == APPROVAL_STATUS_PENDING
    assert req.telegram_result["network_called"] is False
    assert json_path.exists()
    assert md_path.exists()
    assert (tmp_path / "TELEGRAM_APPROVAL_STATE.json").exists()
    latest = load_latest_request(tmp_path)
    assert latest["token"] == req.token
    assert latest["plan_hash"] == plan_sha256(plan)
    assert latest["safety_boundary"]["telegram_approval_does_not_replace_final_confirm"] is True
    assert latest["safety_boundary"]["listener_never_calls_execute_live_order_plan"] is True
    assert latest["manual_live_approve_command"] == f"python main.py execute-last-approved --output-dir {tmp_path.as_posix()}"
    assert "+09:00" in latest["generated_at"]
    assert latest["generated_date"]
    assert latest["timezone"] == "Asia/Seoul"
    assert "생성 시각" in md_path.read_text(encoding="utf-8")


def test_validate_plan_limits_blocks_count_and_values(tmp_path: Path) -> None:
    plan = _plan_path(tmp_path, order_value=60_000.0, orders=2)
    cfg = TelegramApprovalConfig(output_dir=str(tmp_path), max_orders=1, max_single_order_value=50_000.0, max_total_order_value=100_000.0)

    errors = validate_plan_limits(plan, cfg)

    assert any("order count" in e for e in errors)
    assert any("max_single_order_value" in e for e in errors)
    assert any("max_total_order_value" in e for e in errors)


def test_verify_approval_checks_token_chat_expiry_and_hash(tmp_path: Path) -> None:
    plan = _plan_path(tmp_path)
    cfg = TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234")
    req, _, _ = create_telegram_approval_request(plan, cfg)
    state = load_latest_request(tmp_path)
    from deepsignal.live_trading.time_utils import now_kst

    state["expires_at"] = (now_kst() - timedelta(minutes=1)).isoformat(timespec="seconds")

    errors = verify_approval_action(state, action=ACTION_APPROVE, token="bad", chat_id="9999", output_dir=tmp_path)

    assert any("token mismatch" in e for e in errors)
    assert any("chat_id" in e for e in errors)
    assert any("expired" in e for e in errors)
    assert req.token


def test_verify_approval_blocks_plan_hash_mismatch(tmp_path: Path) -> None:
    plan = _plan_path(tmp_path)
    cfg = TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234")
    req, _, _ = create_telegram_approval_request(plan, cfg)
    plan.write_text(plan.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    state = load_latest_request(tmp_path)

    errors = verify_approval_action(state, action=ACTION_APPROVE, token=req.token, chat_id="1234", output_dir=tmp_path)

    assert any("plan hash mismatch" in e for e in errors)


def test_handle_approve_consumes_token_and_never_calls_executor(tmp_path: Path) -> None:
    plan = _plan_path(tmp_path)
    cfg = TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234")
    req, _, _ = create_telegram_approval_request(plan, cfg)
    state = load_latest_request(tmp_path)

    audit, audit_path = handle_telegram_action(
        state=state,
        action=ACTION_APPROVE,
        token=req.token,
        chat_id="1234",
        output_dir=tmp_path,
    )

    assert audit["status"] == "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED"
    assert audit["one_time_token_consumed"] is True
    assert audit["final_confirm_supplied"] is False
    assert audit["manual_live_approve_required"] is True
    assert audit["kis_post_called"] is False
    assert audit["live_execution_result"] is None
    assert "final_confirm_source" not in audit
    assert audit["manual_live_approve_command"] == f"python main.py execute-last-approved --output-dir {tmp_path.as_posix()}"
    assert audit_path.exists()
    updated = load_latest_request(tmp_path)
    assert updated["status"] == "APPROVED"


def test_today_halt_blocks_future_approvals(tmp_path: Path) -> None:
    plan = _plan_path(tmp_path)
    cfg = TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234")
    req, _, _ = create_telegram_approval_request(plan, cfg)
    state = load_latest_request(tmp_path)

    handle_telegram_action(
        state=state,
        action=ACTION_HALT_TODAY,
        token=req.token,
        chat_id="1234",
        output_dir=tmp_path,
    )
    errors = verify_approval_action(load_latest_request(tmp_path), action=ACTION_APPROVE, token=req.token, chat_id="1234", output_dir=tmp_path)

    assert any("today halt" in e for e in errors)


def test_reject_consumes_token_and_writes_audit(tmp_path: Path) -> None:
    plan = _plan_path(tmp_path)
    cfg = TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234")
    req, _, _ = create_telegram_approval_request(plan, cfg)
    state = load_latest_request(tmp_path)

    audit, audit_path = handle_telegram_action(
        state=state,
        action=ACTION_REJECT,
        token=req.token,
        chat_id="1234",
        output_dir=tmp_path,
    )

    assert audit["status"] == "TELEGRAM_APPROVAL_REJECTED"
    assert audit["one_time_token_consumed"] is True
    assert audit["kis_post_called"] is False
    assert audit_path.exists()
    assert load_latest_request(tmp_path)["status"] == "REJECTED"


def test_request_send_calls_telegram_only_when_send_true(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import telegram_approval as tg

    plan = _plan_path(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_post(method: str, payload: dict[str, object], **kwargs):
        calls.append({"method": method, "payload": payload, **kwargs})
        return {"ok": True, "status": "mocked", "network_called": True}

    monkeypatch.setattr(tg, "telegram_api_post", fake_post)
    create_telegram_approval_request(
        plan,
        TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234", bot_token="token", send=False),
    )
    assert calls == []

    create_telegram_approval_request(
        plan,
        TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234", bot_token="token", send=True),
    )
    assert calls and calls[-1]["method"] == "sendMessage"
    payload = calls[-1]["payload"]
    assert "종목 수" in str(payload.get("text"))
    assert payload.get("reply_markup", {}).get("inline_keyboard")


def test_parse_callback_update() -> None:
    update = {
        "callback_query": {
            "id": "cb1",
            "data": "tgapprove:approve:tok123",
            "message": {"chat": {"id": 1234}},
        }
    }

    action, token, chat_id, callback_id = parse_update_action(update)

    assert action == ACTION_APPROVE
    assert token == "tok123"
    assert chat_id == "1234"
    assert callback_id == "cb1"
