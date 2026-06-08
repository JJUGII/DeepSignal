from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import main as main_mod
from deepsignal.live_trading.live_order_plan import LiveOrderItem, LiveOrderPlan, plan_to_json_dict


def _plan_path(tmp_path: Path) -> Path:
    plan = LiveOrderPlan(
        date="2026-05-19",
        capital=300_000.0,
        investable_cash=270_000.0,
        cash_buffer=30_000.0,
        currency="USD",
        orders=[
            LiveOrderItem(
                symbol="005930",
                side="BUY",
                target_weight=0.1,
                target_value=50_000.0,
                estimated_price=50_000.0,
                estimated_qty=1,
                estimated_order_value=50_000.0,
                reason="test",
            )
        ],
        warnings=[],
        status="PENDING_APPROVAL",
        approval_required=True,
        dry_run=True,
    )
    path = tmp_path / "live_order_plan_ai_20260519_010101.json"
    path.write_text(json.dumps(plan_to_json_dict(plan), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_main_telegram_approval_request_smoke(tmp_path: Path, capsys) -> None:
    plan = _plan_path(tmp_path)

    rc = main_mod.main(
        [
            "telegram-approval-request",
            "--plan",
            str(plan),
            "--output-dir",
            str(tmp_path),
            "--allowed-chat-id",
            "1234",
        ]
    )

    assert rc == 0
    assert (tmp_path / "TELEGRAM_APPROVAL_REQUEST.md").exists()
    assert (tmp_path / "TELEGRAM_APPROVAL_STATE.json").exists()
    requests = list(tmp_path.glob("telegram_approval_request_*.json"))
    assert len(requests) == 1
    data = json.loads(requests[0].read_text(encoding="utf-8"))
    assert data["telegram_result"]["network_called"] is False
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_main_telegram_approval_status(tmp_path: Path) -> None:
    plan = _plan_path(tmp_path)
    main_mod.main(["telegram-approval-request", "--plan", str(plan), "--output-dir", str(tmp_path), "--allowed-chat-id", "1234"])

    rc = main_mod.main(["telegram-approval-status", "--output-dir", str(tmp_path)])

    assert rc == 0


def test_main_telegram_approval_listen_uses_callback_and_does_not_execute(tmp_path: Path, monkeypatch, capsys) -> None:
    from deepsignal.live_trading import telegram_approval as tg

    plan = _plan_path(tmp_path)
    main_mod.main(["telegram-approval-request", "--plan", str(plan), "--output-dir", str(tmp_path), "--allowed-chat-id", "1234"])
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

    monkeypatch.setattr(tg, "telegram_get_updates", fake_updates)
    runner = MagicMock()
    monkeypatch.setattr(
        "deepsignal.live_trading.telegram_auto_execute.run_approved_execution",
        runner,
    )

    rc = main_mod.main(["telegram-approval-listen", "--output-dir", str(tmp_path), "--no-auto-execute"])

    assert rc == 0
    runner.assert_not_called()
    audits = list(tmp_path.glob("telegram_approval_audit_*.json"))
    assert len(audits) == 1
    audit = json.loads(audits[0].read_text(encoding="utf-8"))
    assert audit["status"] == "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED"
    assert audit["live_execution_result"] is None
    assert audit["kis_post_called"] is False
    out = capsys.readouterr().out
    assert "수동 실행 필요" in out


def test_main_telegram_approval_listen_reject(tmp_path: Path, monkeypatch, capsys) -> None:
    from deepsignal.live_trading import telegram_approval as tg

    plan = _plan_path(tmp_path)
    main_mod.main(["telegram-approval-request", "--plan", str(plan), "--output-dir", str(tmp_path), "--allowed-chat-id", "1234"])
    state = json.loads((tmp_path / "TELEGRAM_APPROVAL_STATE.json").read_text(encoding="utf-8"))

    def fake_updates(**kwargs):
        return {
            "ok": True,
            "result": [
                {
                    "callback_query": {
                        "id": "cb2",
                        "data": f"tgapprove:reject:{state['token']}",
                        "message": {"chat": {"id": 1234}},
                    }
                }
            ],
        }

    monkeypatch.setattr(tg, "telegram_get_updates", fake_updates)
    monkeypatch.setattr(tg, "telegram_answer_callback", lambda *a, **k: {"ok": True})

    rc = main_mod.main(["telegram-approval-listen", "--output-dir", str(tmp_path), "--no-auto-execute"])

    assert rc == 0
    assert "거부 처리 완료" in capsys.readouterr().out
