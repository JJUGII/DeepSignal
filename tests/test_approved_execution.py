from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path

from deepsignal.live_trading.approved_execution import (
    APPROVED_STATUS,
    execute_approved_by_request_id,
    execute_approved_request,
    execute_last_approved,
    load_latest_telegram_approval,
    run_approved_execution,
    validate_telegram_approval,
)
from deepsignal.live_trading.kis_config import KISConfig
from deepsignal.live_trading.live_order_plan import LiveOrderItem, LiveOrderPlan, plan_to_json_dict
from deepsignal.live_trading.telegram_approval import plan_sha256


def _plan_path(tmp_path: Path, *, symbol: str = "005930", value: float = 50_000.0) -> Path:
    plan = LiveOrderPlan(
        date="2026-05-19",
        capital=300_000.0,
        investable_cash=270_000.0,
        cash_buffer=30_000.0,
        currency="KRW",
        orders=[
            LiveOrderItem(
                symbol=symbol,
                side="BUY",
                target_weight=0.1,
                target_value=value,
                estimated_price=value,
                estimated_qty=1,
                estimated_order_value=value,
                reason="test",
            )
        ],
        warnings=[],
        status="PENDING_APPROVAL",
        approval_required=True,
        dry_run=True,
    )
    path = tmp_path / "live_order_plan_ai_20260519_010101.json"
    payload = plan_to_json_dict(plan)
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest = tmp_path / "live_order_plan_ai_latest.json"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _approval(tmp_path: Path, *, status: str = APPROVED_STATUS, expires_delta_minutes: int = 10) -> tuple[str, Path, Path]:
    request_id = "REQ123"
    plan = _plan_path(tmp_path)
    state = {
        "token": request_id,
        "status": "APPROVED",
        "plan_path": plan.as_posix(),
        "plan_hash": plan_sha256(plan),
        "expires_at": (datetime.now() + timedelta(minutes=expires_delta_minutes)).isoformat(timespec="seconds"),
        "max_total_order_value": 100_000.0,
        "max_single_order_value": 50_000.0,
        "max_orders": 1,
        "allowed_chat_id": "1234",
    }
    state_path = tmp_path / f"telegram_approval_state_{request_id}.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (tmp_path / "TELEGRAM_APPROVAL_STATE.json").write_text(state_path.read_text(encoding="utf-8"), encoding="utf-8")
    audit = {
        "token": request_id,
        "status": status,
        "approval_channel": "telegram",
        "telegram_chat_id_verified": True,
        "one_time_token_consumed": True,
        "plan_hash_verified": True,
        "chat_id": "1234",
        "errors": [],
    }
    audit_path = tmp_path / "telegram_approval_audit_20260519_010101.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request_id, state_path, audit_path


def _kis_config() -> KISConfig:
    return KISConfig(
        app_key="k" * 10,
        app_secret="s" * 10,
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="live",
    )


def _fake_execute(**result):
    def inner(*args, **kwargs):
        return {
            "success": True,
            "status": "KIS_LIVE_ORDER_COMPLETED",
            "orders": [{"symbol": "005930", "estimated_value": 50_000.0}],
            "results": [{"status": "KIS_ORDER_SUBMITTED"}],
            **result,
        }

    return inner


def test_load_latest_and_request_id_lookup(tmp_path: Path) -> None:
    request_id, _, _ = _approval(tmp_path)

    latest = load_latest_telegram_approval(tmp_path)
    by_id = execute_approved_request(tmp_path, request_id=request_id)

    assert latest.request_id == request_id
    assert by_id.request_id == request_id
    assert by_id.approval_status == APPROVED_STATUS


def test_run_approved_execution_calls_wrapper_and_writes_audit(tmp_path: Path) -> None:
    request_id, _, _ = _approval(tmp_path)
    req = execute_approved_request(tmp_path, request_id=request_id)
    called: dict[str, object] = {}

    def fake_execute(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        return {
            "success": True,
            "status": "KIS_LIVE_ORDER_COMPLETED",
            "orders": [{"symbol": "005930", "estimated_value": 50_000.0}],
            "results": [{"status": "KIS_ORDER_SUBMITTED"}],
        }

    result = run_approved_execution(
        req,
        output_dir=tmp_path,
        db_path=str(tmp_path / "test.db"),
        execute_func=fake_execute,
        kis_config_loader=_kis_config,
    )

    assert result.success is True
    assert result.status == "EXECUTE_APPROVED_COMPLETED"
    assert called["kwargs"]["execute"] is True
    assert called["kwargs"]["final_confirm"] == "I_UNDERSTAND_REAL_ORDER"
    assert called["kwargs"]["live_policy"].allow_live_env is True
    assert Path(result.audit_json_path).exists()
    data = json.loads(Path(result.audit_json_path).read_text(encoding="utf-8"))
    assert data["telegram_approval_linkage"]["request_id"] == request_id
    assert data["live_approval_audit_path"]
    assert "+09:00" in data["generated_at"]
    assert data["generated_date"]
    assert data["timezone"] == "Asia/Seoul"
    md = Path(result.audit_markdown_path).read_text(encoding="utf-8")
    assert "생성 시각" in md


def test_expired_rejected_halt_hash_and_missing_plan_block(tmp_path: Path) -> None:
    request_id, _, _ = _approval(tmp_path, expires_delta_minutes=-1)
    req = execute_approved_request(tmp_path, request_id=request_id)
    ok, errors, _ = validate_telegram_approval(req, tmp_path)
    assert ok is False
    assert any("만료" in e for e in errors)

    request_id, _, _ = _approval(tmp_path, status="TELEGRAM_APPROVAL_REJECTED")
    req = execute_approved_request(tmp_path, request_id=request_id)
    ok, errors, _ = validate_telegram_approval(req, tmp_path)
    assert ok is False
    assert any("실행 가능 상태" in e for e in errors)

    request_id, _, _ = _approval(tmp_path)
    (tmp_path / f"telegram_approval_halt_{datetime.now().strftime('%Y%m%d')}.json").write_text(
        json.dumps({"active": True}),
        encoding="utf-8",
    )
    req = execute_approved_request(tmp_path, request_id=request_id)
    ok, errors, _ = validate_telegram_approval(req, tmp_path)
    assert ok is False
    assert any("중단" in e for e in errors)

    (tmp_path / f"telegram_approval_halt_{datetime.now().strftime('%Y%m%d')}.json").unlink()
    plan = tmp_path / "live_order_plan_ai_20260519_010101.json"
    plan.write_text(plan.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    req = execute_approved_request(tmp_path, request_id=request_id)
    ok, errors, _ = validate_telegram_approval(req, tmp_path)
    assert ok is False
    assert any("hash" in e for e in errors)

    plan.unlink()
    req = execute_approved_request(tmp_path, request_id=request_id)
    ok, errors, _ = validate_telegram_approval(req, tmp_path)
    assert ok is False
    assert any("plan 파일" in e for e in errors)


def test_already_executed_blocks_second_run(tmp_path: Path) -> None:
    request_id, _, _ = _approval(tmp_path)
    req = execute_approved_request(tmp_path, request_id=request_id)
    result = run_approved_execution(
        req,
        output_dir=tmp_path,
        db_path=str(tmp_path / "test.db"),
        execute_func=_fake_execute(),
        kis_config_loader=_kis_config,
    )
    assert result.success is True

    req2 = execute_approved_request(tmp_path, request_id=request_id)
    ok, errors, _ = validate_telegram_approval(req2, tmp_path)

    assert ok is False
    assert any("이미 승인 실행" in e for e in errors)


def test_execute_last_approved_blocks_stale_plan(tmp_path: Path) -> None:
    request_id, _, _ = _approval(tmp_path)
    req = execute_approved_request(tmp_path, request_id=request_id)
    plan_path = Path(req.plan_path)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["generated_at"] = "2026-05-18T08:00:00+09:00"
    plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest = tmp_path / "live_order_plan_ai_latest.json"
    latest.write_text(json.dumps({"generated_at": "2026-05-18T08:00:00+09:00"}, ensure_ascii=False) + "\n", encoding="utf-8")

    ok, errors, _ = validate_telegram_approval(req, tmp_path, freshness_date="2026-05-19")

    assert ok is False
    assert any("오늘 기준" in e for e in errors)

    result = run_approved_execution(
        req,
        output_dir=tmp_path,
        db_path=str(tmp_path / "test.db"),
        execute_func=_fake_execute(),
        kis_config_loader=_kis_config,
        freshness_date="2026-05-19",
    )
    assert result.success is False
    audit = json.loads(Path(result.audit_json_path).read_text(encoding="utf-8"))
    assert audit.get("freshness_validation")
    assert audit.get("stale_reasons")


def test_execute_helpers_return_blocked_result_without_approval(tmp_path: Path) -> None:
    result = execute_last_approved(
        tmp_path,
        execute_func=_fake_execute(),
        kis_config_loader=_kis_config,
        wait_seconds=0,
        poll_interval=0.01,
    )
    assert result.success is False
    assert any("승인 요청" in e for e in result.errors)

    try:
        execute_approved_by_request_id(tmp_path, request_id="MISSING", execute_func=_fake_execute(), kis_config_loader=_kis_config)
    except ValueError as exc:
        assert "request-id" in str(exc)
    else:
        raise AssertionError("expected missing request-id error")


def _pending_request_state(tmp_path: Path) -> str:
    from deepsignal.live_trading.telegram_approval import APPROVAL_STATUS_PENDING, TelegramApprovalConfig, create_telegram_approval_request

    plan = _plan_path(tmp_path)
    create_telegram_approval_request(plan, TelegramApprovalConfig(output_dir=str(tmp_path), allowed_chat_id="1234", send=False))
    state = json.loads((tmp_path / "TELEGRAM_APPROVAL_STATE.json").read_text(encoding="utf-8"))
    assert state["status"] == APPROVAL_STATUS_PENDING
    return str(state["token"])


def test_execute_last_approved_poll_approve_then_executes(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import telegram_approval as tg

    token = _pending_request_state(tmp_path)
    calls = {"n": 0}

    def fake_updates(**kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            return {"ok": True, "result": []}
        return {
            "ok": True,
            "result": [
                {
                    "callback_query": {
                        "id": "cb-approve",
                        "data": f"tgapprove:approve:{token}",
                        "message": {"chat": {"id": 1234}},
                    }
                }
            ],
        }

    monkeypatch.setattr(tg, "telegram_get_updates", fake_updates)
    monkeypatch.setattr(tg, "telegram_answer_callback", lambda *a, **k: {"ok": True})
    executed: dict[str, object] = {}

    def fake_execute(*args, **kwargs):
        executed["called"] = True
        return {
            "success": True,
            "status": "KIS_LIVE_ORDER_COMPLETED",
            "orders": [],
            "results": [],
        }

    result = execute_last_approved(
        tmp_path,
        db_path=str(tmp_path / "test.db"),
        execute_func=fake_execute,
        kis_config_loader=_kis_config,
        wait_seconds=5,
        poll_interval=0.01,
    )

    assert result.success is True
    assert executed.get("called") is True
    assert list(tmp_path.glob("telegram_approval_audit_*.json"))


def test_execute_last_approved_poll_reject_blocks(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import telegram_approval as tg

    token = _pending_request_state(tmp_path)

    def fake_updates(**kwargs):
        return {
            "ok": True,
            "result": [
                {
                    "callback_query": {
                        "id": "cb-reject",
                        "data": f"tgapprove:reject:{token}",
                        "message": {"chat": {"id": 1234}},
                    }
                }
            ],
        }

    monkeypatch.setattr(tg, "telegram_get_updates", fake_updates)
    monkeypatch.setattr(tg, "telegram_answer_callback", lambda *a, **k: {"ok": True})
    executed = {"called": False}

    def fake_execute(*args, **kwargs):
        executed["called"] = True
        return {"success": True, "status": "KIS_LIVE_ORDER_COMPLETED"}

    result = execute_last_approved(
        tmp_path,
        execute_func=fake_execute,
        kis_config_loader=_kis_config,
        wait_seconds=0,
        poll_interval=0.01,
    )

    assert result.success is False
    assert executed["called"] is False
    assert any("중단" in e for e in result.errors)


def test_execute_last_approved_poll_timeout(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import telegram_approval as tg

    _pending_request_state(tmp_path)
    monkeypatch.setattr(tg, "telegram_get_updates", lambda **kwargs: {"ok": True, "result": []})

    result = execute_last_approved(
        tmp_path,
        execute_func=_fake_execute(),
        kis_config_loader=_kis_config,
        wait_seconds=0,
        poll_interval=0.01,
    )

    assert result.success is False
    assert any("대기" in e for e in result.errors)


def test_execute_last_approved_uses_existing_approved_audit_without_polling(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import telegram_approval as tg

    request_id, _, _ = _approval(tmp_path)
    polled = {"called": False}

    def fake_updates(**kwargs):
        polled["called"] = True
        return {"ok": True, "result": []}

    monkeypatch.setattr(tg, "telegram_get_updates", fake_updates)

    result = execute_last_approved(
        tmp_path,
        db_path=str(tmp_path / "test.db"),
        execute_func=_fake_execute(),
        kis_config_loader=_kis_config,
        wait_seconds=5,
        poll_interval=0.01,
    )

    assert result.success is True
    assert polled["called"] is False
