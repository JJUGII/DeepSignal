from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deepsignal.live_trading.daily_ai_auto_runner import (
    DailyAIAutoRunnerConfig,
    DailyAIAutoRunnerState,
    _due_scheduled,
    load_runner_state,
    run_morning_plan,
    save_runner_state,
    tick_runner,
)
from deepsignal.live_trading.telegram_approval import APPROVAL_STATUS_PENDING, TelegramApprovalConfig


def _plan_result(path: Path, *, order_count: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        order_count=order_count,
        latest_order_plan_json=path.as_posix(),
    )


def test_due_scheduled_once_per_day() -> None:
    now = datetime(2026, 5, 21, 9, 6, 0)
    assert _due_scheduled(None, "09:05", now) is True
    assert _due_scheduled("2026-05-21", "09:05", now) is False


def test_runner_state_persistence(tmp_path: Path) -> None:
    state = DailyAIAutoRunnerState(last_plan_date="2026-05-21", telegram_update_offset=99)
    save_runner_state(tmp_path, state)
    loaded = load_runner_state(tmp_path)
    assert loaded.last_plan_date == "2026-05-21"
    assert loaded.telegram_update_offset == 99


def test_morning_zero_orders_sends_telegram(tmp_path: Path, monkeypatch) -> None:
    sent: list[str] = []

    def fake_send(*, text, config, reply_markup=False, token=None):
        sent.append(text)
        return {"ok": True}

    monkeypatch.setattr(
        "deepsignal.live_trading.daily_ai_auto_runner.send_runner_telegram",
        fake_send,
    )
    def _tg_cfg(**k):
        return TelegramApprovalConfig(
            output_dir=str(tmp_path),
            bot_token="t",
            allowed_chat_id="1",
            timeout_seconds=5.0,
            expires_minutes=420,
            max_total_order_value=300_000,
            max_single_order_value=300_000,
            max_orders=1,
            send=True,
        )

    monkeypatch.setattr("deepsignal.live_trading.daily_ai_auto_runner.load_telegram_config_from_env", _tg_cfg)

    cfg = DailyAIAutoRunnerConfig(output_dir=str(tmp_path))
    cfg.plan_runner = lambda *a, **k: _plan_result(tmp_path / "x.json", order_count=0)
    state = DailyAIAutoRunnerState()
    out = run_morning_plan(cfg, db_path=":memory:", state=state)
    assert out.last_plan_order_count == 0
    assert sent and "오늘은 주문하지 않습니다" in sent[0]


def test_morning_auto_execute_skips_approval(tmp_path: Path, monkeypatch) -> None:
    plan_file = tmp_path / "live_order_plan_ai_latest.json"
    plan_file.write_text(
        json.dumps(
            {
                "orders": [
                    {
                        "symbol": "005930",
                        "side": "BUY",
                        "estimated_qty": 1,
                        "estimated_price": 70000,
                        "estimated_order_value": 70000,
                        "limit_price": 70000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    sent: list[str] = []
    executed: list[str] = []

    monkeypatch.setenv("KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL", "true")

    def fake_send(*, text, config, reply_markup=False, token=None):
        sent.append(text)
        return {"ok": True}

    def fake_execute(plan_path, **kwargs):
        executed.append(str(plan_path))
        from deepsignal.live_trading.approved_execution import ApprovedExecutionResult

        return ApprovedExecutionResult(
            request_id="auto",
            success=True,
            status="INACTIVE_AUTO_COMPLETED",
            errors=[],
            warnings=[],
            audit_json_path="",
            audit_markdown_path="",
            live_approval_audit_path=None,
            execution_result={"success": True, "status": "KIS_LIVE_ORDER_COMPLETED"},
        )

    monkeypatch.setattr("deepsignal.live_trading.daily_ai_auto_runner.send_runner_telegram", fake_send)
    monkeypatch.setattr("deepsignal.live_trading.daily_ai_auto_runner.execute_kis_plan_inactive_auto", fake_execute)
    monkeypatch.setattr(
        "deepsignal.live_trading.daily_ai_auto_runner.notify_inactive_kis_execution",
        lambda **k: {"ok": True},
    )

    def _tg_cfg(**k):
        return TelegramApprovalConfig(
            output_dir=str(tmp_path),
            bot_token="t",
            allowed_chat_id="1",
            timeout_seconds=5.0,
            expires_minutes=420,
            max_total_order_value=300_000,
            max_single_order_value=300_000,
            max_orders=1,
            send=True,
        )

    monkeypatch.setattr("deepsignal.live_trading.daily_ai_auto_runner.load_telegram_config_from_env", _tg_cfg)

    cfg = DailyAIAutoRunnerConfig(output_dir=str(tmp_path))
    cfg.plan_runner = lambda *a, **k: _plan_result(plan_file, order_count=1)
    state = run_morning_plan(cfg, db_path=":memory:", state=DailyAIAutoRunnerState())
    assert executed
    assert state.pending_token is None
    assert not (tmp_path / "TELEGRAM_APPROVAL_STATE.json").exists()


def test_morning_with_orders_sends_approval(tmp_path: Path, monkeypatch) -> None:
    plan_file = tmp_path / "live_order_plan_ai_latest.json"
    plan_file.write_text(
        json.dumps(
            {
                "orders": [
                    {
                        "symbol": "005930",
                        "side": "BUY",
                        "estimated_qty": 1,
                        "estimated_price": 70000,
                        "estimated_order_value": 70000,
                        "limit_price": 70000,
                        "ai_confidence": 82,
                        "ai_reasons": ["momentum positive"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    sent: list[str] = []

    def fake_send(*, text, config, reply_markup=False, token=None):
        sent.append(text)
        return {"ok": True}

    monkeypatch.setattr("deepsignal.live_trading.daily_ai_auto_runner.send_runner_telegram", fake_send)
    def _tg_cfg(**k):
        return TelegramApprovalConfig(
            output_dir=str(tmp_path),
            bot_token="t",
            allowed_chat_id="1",
            timeout_seconds=5.0,
            expires_minutes=420,
            max_total_order_value=300_000,
            max_single_order_value=300_000,
            max_orders=1,
            send=True,
        )

    monkeypatch.setattr("deepsignal.live_trading.daily_ai_auto_runner.load_telegram_config_from_env", _tg_cfg)

    cfg = DailyAIAutoRunnerConfig(output_dir=str(tmp_path))
    cfg.plan_runner = lambda *a, **k: _plan_result(plan_file, order_count=1)
    state = run_morning_plan(cfg, db_path=":memory:", state=DailyAIAutoRunnerState())
    assert state.pending_token
    assert any("삼성전자" in t or "005930" in t for t in sent)
    assert (tmp_path / "TELEGRAM_APPROVAL_STATE.json").exists()


def test_tick_runner_polls_pending(tmp_path: Path, monkeypatch) -> None:
    state_payload = {
        "token": "tok123",
        "status": APPROVAL_STATUS_PENDING,
        "plan_path": (tmp_path / "live_order_plan_ai_latest.json").as_posix(),
        "plan_hash": "x",
        "expires_at": "2099-12-31T23:59:59+09:00",
        "allowed_chat_id": "1",
        "max_total_order_value": 300_000,
        "max_single_order_value": 300_000,
        "max_orders": 1,
    }
    (tmp_path / "TELEGRAM_APPROVAL_STATE.json").write_text(json.dumps(state_payload), encoding="utf-8")
    (tmp_path / "live_order_plan_ai_latest.json").write_text(
        json.dumps({"orders": [{"symbol": "005930", "estimated_qty": 1, "estimated_order_value": 1, "estimated_price": 1}]}),
        encoding="utf-8",
    )

    outcome = SimpleNamespace(outcome="executed", message="ok", audit=None, audit_path=None, execution=None)
    monkeypatch.setattr(
        "deepsignal.live_trading.daily_ai_auto_runner.try_resume_approved_execution",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.daily_ai_auto_runner.poll_telegram_approval_once",
        lambda *a, **k: (outcome, 42),
    )
    def _tg_cfg(**k):
        return TelegramApprovalConfig(
            output_dir=str(tmp_path),
            bot_token="t",
            allowed_chat_id="1",
            timeout_seconds=5.0,
            expires_minutes=420,
            max_total_order_value=300_000,
            max_single_order_value=300_000,
            max_orders=1,
            send=True,
        )

    monkeypatch.setattr("deepsignal.live_trading.daily_ai_auto_runner.load_telegram_config_from_env", _tg_cfg)

    from deepsignal.live_trading.time_utils import now_kst

    today = now_kst().date().isoformat()
    cfg = DailyAIAutoRunnerConfig(output_dir=str(tmp_path))
    state = DailyAIAutoRunnerState(last_plan_date=today, last_report_date=today)
    out = tick_runner(cfg, db_path=":memory:", state=state)
    assert out.telegram_update_offset == 42
    assert out.last_event == "executed"
