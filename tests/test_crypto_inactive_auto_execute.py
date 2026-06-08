"""Crypto inactive-window auto execution."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deepsignal.crypto_trading.upbit_broker import UpbitOrderResult
from deepsignal.live_trading.inactive_auto_execute import execute_crypto_plan_inactive_auto
from deepsignal.live_trading.operator_inactive_window import OperatorInactiveConfig


def test_execute_crypto_plan_inactive_auto_executes_when_not_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = MagicMock()
    plan.to_dict.return_value = {"market": "KRW-BTC", "side": "buy"}
    plan.display_name = "비트코인"
    plan.market = "KRW-BTC"
    plan.limit_price = 100_000_000.0
    plan.krw_amount = 10_000.0
    plan.volume = 0.0001
    plan.side = "buy"

    broker = MagicMock()
    broker.config.dry_run = False
    order = UpbitOrderResult(
        market="KRW-BTC",
        side="bid",
        order_type="limit",
        price=100_000_000.0,
        volume=0.0001,
        krw_amount=10_000.0,
        status="wait",
        uuid="uuid-1",
        dry_run=False,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_telegram_flow.execute_approved_crypto_order",
        lambda _b, _p, *, execute: order if execute else order,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_telegram_flow._write_audit",
        lambda _o, payload: tmp_path / "audit.json",
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_telegram_flow.telegram_send_plain",
        lambda *_a, **_k: {"ok": True},
    )

    tg = MagicMock()
    tg.bot_token = "tok"
    tg.allowed_chat_id = "123"
    audit = execute_crypto_plan_inactive_auto(
        broker,
        plan,
        tg_cfg=tg,
        output_dir=tmp_path,
        inactive_cfg=OperatorInactiveConfig(enabled=True),
    )
    assert audit.get("telegram_approval_skipped") is True
    assert audit.get("result", {}).get("uuid") == "uuid-1"
