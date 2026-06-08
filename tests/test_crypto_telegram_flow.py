from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan, save_crypto_plan
from deepsignal.crypto_trading.crypto_telegram_flow import (
    ACTION_APPROVE,
    STATUS_APPROVED,
    CryptoTelegramConfig,
    create_crypto_approval_request,
    execute_approved_crypto_order,
    format_approval_message,
    process_crypto_telegram_update,
)
from deepsignal.crypto_trading.upbit_broker import UpbitBroker
from deepsignal.crypto_trading.upbit_config import UpbitConfig


def test_approval_message_korean() -> None:
    plan = CryptoOrderPlan(
        market="KRW-BTC",
        display_name="비트코인",
        krw_amount=10_000,
        limit_price=95_000_000,
        reason="단기 상승",
    )
    text = format_approval_message(plan)
    assert "코인 매매 승인" in text
    assert "비트코인" in text


def test_execute_approved_dry_run(tmp_path: Path) -> None:
    plan = CryptoOrderPlan(market="KRW-BTC", display_name="BTC", krw_amount=10_000, limit_price=95_000_000)
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    result = execute_approved_crypto_order(br, plan, execute=True)
    assert result.status == "UPBIT_DRY_RUN_BLOCKED"


def test_process_approve_callback(tmp_path: Path) -> None:
    plan = CryptoOrderPlan(
        broker="upbit",
        market="KRW-ETH",
        display_name="이더리움",
        krw_amount=10_000,
        limit_price=3_500_000,
        reason="test",
    )
    jpath, _ = save_crypto_plan(tmp_path, plan)
    cfg = CryptoTelegramConfig(output_dir=str(tmp_path), allowed_chat_id="123", bot_token="tok")
    req = create_crypto_approval_request(plan, cfg=cfg, plan_path=jpath)
    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))

    update = {
        "callback_query": {
            "data": f"{ACTION_APPROVE}:{req.token}",
            "message": {"chat": {"id": "123"}},
        }
    }
    with patch("deepsignal.crypto_trading.crypto_telegram_flow.telegram_send_message", return_value={"ok": True}):
        out = process_crypto_telegram_update(update, cfg=cfg, broker=br)
    assert out is not None
    assert out.get("status") == STATUS_APPROVED
