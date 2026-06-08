from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepsignal.crypto_trading.crypto_order_fill import (
    classify_fill_outcome,
    format_fill_message_cancel,
    format_fill_message_done,
    format_fill_message_wait,
    normalize_order_status,
    poll_order_fill,
    write_order_status_audit,
)
from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
from deepsignal.crypto_trading.crypto_telegram_flow import follow_up_order_fill, CryptoTelegramConfig
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitOrderResult
from deepsignal.crypto_trading.upbit_config import UpbitConfig


def _plan() -> CryptoOrderPlan:
    return CryptoOrderPlan(
        market="KRW-XRP",
        display_name="리플",
        krw_amount=10_000,
        limit_price=2_003,
    )


def test_get_order_mock_done() -> None:
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    raw = br.get_order("test-uuid-done")
    norm = normalize_order_status(raw)
    assert norm["state"] == "done"
    assert float(norm["executed_volume"]) > 0


def test_get_order_mock_wait() -> None:
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    raw = br.get_order("x-wait")
    assert raw["state"] == "wait"


def test_format_done_message() -> None:
    plan = _plan()
    status = normalize_order_status(
        {
            "uuid": "u1",
            "market": "KRW-XRP",
            "state": "done",
            "price": "2003",
            "executed_volume": "4.99251123",
            "paid_fee": "4.99",
        }
    )
    text = format_fill_message_done(plan, status)
    assert "체결 완료" in text
    assert "리플" in text
    assert "4.99251123" in text


def test_format_wait_message() -> None:
    plan = _plan()
    status = normalize_order_status(
        {"uuid": "u1", "state": "wait", "remaining_volume": "5", "price": "2003"}
    )
    text = format_fill_message_wait(plan, status)
    assert "주문 대기" in text
    assert "체결되지 않았습니다" in text


def test_format_cancel_message() -> None:
    plan = _plan()
    status = normalize_order_status({"uuid": "u1", "state": "cancel"})
    text = format_fill_message_cancel(plan, status)
    assert "취소" in text


def test_poll_timeout_wait(monkeypatch) -> None:
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    status, outcome = poll_order_fill(br, "uuid-wait", wait_fill_seconds=0.1, fill_poll_interval=0.05)
    assert outcome in ("wait", "partial", "timeout")
    if outcome == "wait":
        assert status.get("state") == "wait"


def test_poll_reaches_done() -> None:
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    status, outcome = poll_order_fill(br, "any-done", wait_fill_seconds=5, fill_poll_interval=0.1)
    assert outcome == "done"
    assert status["state"] == "done"


def test_write_audit_json(tmp_path: Path) -> None:
    path = write_order_status_audit(
        tmp_path,
        {
            "uuid": "u1",
            "market": "KRW-XRP",
            "state": "done",
            "executed_volume": 1.0,
            "remaining_volume": 0,
            "paid_fee": 1.0,
            "trades_count": 1,
            "raw": {},
        },
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["uuid"] == "u1"
    assert path.name.startswith("crypto_order_status_")


def test_follow_up_skipped_when_no_wait() -> None:
    cfg = CryptoTelegramConfig(output_dir="outputs", wait_fill_seconds=0)
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    plan = _plan()
    result = UpbitOrderResult(
        market="KRW-XRP",
        side="bid",
        order_type="limit",
        price=2003,
        volume=5,
        krw_amount=10_000,
        status="wait",
        uuid="u1",
        dry_run=False,
    )
    out = follow_up_order_fill(cfg, br, plan, result)
    assert out.get("fill_follow_up") == "skipped"


def test_follow_up_with_mock_telegram(tmp_path: Path, monkeypatch) -> None:
    cfg = CryptoTelegramConfig(
        output_dir=str(tmp_path),
        bot_token="tok",
        allowed_chat_id="1",
        wait_fill_seconds=2,
        fill_poll_interval=0.1,
    )
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    plan = _plan()
    result = UpbitOrderResult(
        market="KRW-XRP",
        side="bid",
        order_type="limit",
        price=2003,
        volume=5,
        krw_amount=10_000,
        status="wait",
        uuid="order-done",
        dry_run=False,
    )
    sent: list[str] = []

    def fake_plain(c, text):
        sent.append(text)
        return {"ok": True}

    monkeypatch.setattr("deepsignal.crypto_trading.crypto_telegram_flow.telegram_send_plain", fake_plain)
    out = follow_up_order_fill(cfg, br, plan, result)
    assert out.get("fill_outcome") == "done"
    assert sent and "체결 완료" in sent[0]
