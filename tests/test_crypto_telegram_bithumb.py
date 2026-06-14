from __future__ import annotations

import pytest

from deepsignal.crypto_trading.broker.bithumb.broker import BithumbBroker
from deepsignal.crypto_trading.broker.bithumb.config import BithumbConfig
from deepsignal.crypto_trading.crypto_order_fill import normalize_order_status, poll_order_fill
from deepsignal.crypto_trading.telegram.flow import (
    _active_exchange_label,
    format_approval_message,
    format_execution_report,
)
from deepsignal.crypto_trading.broker.interface import CryptoOrderResult
from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan


def test_active_exchange_label_bithumb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_BROKER", "bithumb")
    assert _active_exchange_label() == "Bithumb"


def test_format_approval_message_uses_bithumb_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_BROKER", "bithumb")
    plan = CryptoOrderPlan(
        market="KRW-BTC",
        display_name="비트코인",
        side="buy",
        krw_amount=10_000,
        limit_price=90_000_000,
        reason="test",
    )
    msg = format_approval_message(plan)
    assert "Bithumb" in msg
    assert "업비트" not in msg


def test_format_execution_report_bithumb_broker() -> None:
    br = BithumbBroker(BithumbConfig(api_key="demo-key", secret_key="demo-secret", dry_run=True))
    plan = CryptoOrderPlan(
        market="KRW-BTC",
        display_name="비트코인",
        side="buy",
        krw_amount=10_000,
        limit_price=90_000_000,
        reason="test",
    )
    result = CryptoOrderResult(
        market="KRW-BTC",
        side="bid",
        order_type="limit",
        price=90_000_000,
        volume=0.0001,
        krw_amount=10_000,
        status="wait",
        uuid="order-123",
        dry_run=False,
    )
    report = format_execution_report(plan, result, exchange_label="Bithumb")
    assert "Bithumb" in report
    assert "Upbit" not in report


def test_normalize_order_status_bithumb_order_id() -> None:
    raw = {
        "order_id": "C12345",
        "market": "KRW-BTC",
        "state": "done",
        "side": "bid",
        "price": "90000000",
        "volume": "0.001",
        "executed_volume": "0.001",
        "remaining_volume": "0",
        "paid_fee": "45",
    }
    norm = normalize_order_status(raw)
    assert norm["uuid"] == "C12345"
    assert norm["state"] == "done"
    assert norm["executed_volume"] == 0.001


def test_poll_order_fill_bithumb_demo() -> None:
    br = BithumbBroker(BithumbConfig(api_key="demo-key", secret_key="demo-secret", dry_run=True))
    status, outcome = poll_order_fill(
        br,
        "demo-order-id",
        wait_fill_seconds=0.1,
        fill_poll_interval=0.05,
    )
    assert outcome in ("done", "skipped", "wait", "partial")
    if status:
        assert status.get("uuid") or status.get("state")
