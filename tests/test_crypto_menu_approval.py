"""Telegram menu — approval buttons when recommendation exists."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deepsignal.crypto_trading.crypto_telegram_flow import CryptoTelegramConfig
from deepsignal.crypto_trading.crypto_telegram_menu import (
    MENU_TEXT_RECOMMEND_CRYPTO,
    handle_menu_crypto_recommend,
    process_crypto_telegram_menu_message,
)
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig


def _cfg(tmp_path: Path) -> CryptoTelegramConfig:
    return CryptoTelegramConfig(
        output_dir=str(tmp_path),
        bot_token="test-token",
        allowed_chat_id="12345",
    )


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def test_handle_menu_crypto_sends_approval_when_rec_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rec = MagicMock()
    rec.display_name = "비트코인"
    rec.market = "KRW-BTC"
    rec.side = "buy"
    rec.reason = "test"
    rec.pnl_pct = 1.0
    rec.current_price = 100.0
    rec.sell_trigger = None
    rec.score_breakdown = {"display": {"final": 0.7, "macro_regime": "on"}}

    plan = MagicMock()
    plan.market = "KRW-BTC"

    approvals: list = []

    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.prepare_menu_scan_lock",
        lambda *a, **k: "acquired",
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.release_menu_scan_lock",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.build_daily_crypto_recommendation",
        lambda *a, **k: rec,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.build_plan_from_recommendation",
        lambda r, **k: plan,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.save_crypto_plan",
        lambda out, p: (tmp_path / "CRYPTO_ORDER_PLAN.json", tmp_path / "plan.md"),
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_recommendation_outcomes.record_crypto_recommendation",
        lambda *a, **k: 1,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_outcome_threshold_tuning.apply_active_thresholds_to_runner",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.init_database",
        lambda *a, **k: ":memory:",
    )
    monkeypatch.setattr(
        "deepsignal.config.settings.load_settings",
        lambda: type("S", (), {"db_path": ":memory:"})(),
    )

    def fake_approval(plan, *, cfg, plan_path):
        approvals.append(cfg.send)
        req = MagicMock()
        req.telegram_result = {"ok": True}
        return req

    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_telegram_flow.create_crypto_approval_request",
        fake_approval,
    )

    out = handle_menu_crypto_recommend(
        _br(),
        _cfg(tmp_path),
        take_profit_pct=2.0,
        stop_loss_pct=-1.5,
        take_profit_buffer_pct=0.05,
        stop_loss_buffer_pct=0.05,
        min_volume_ratio=0.8,
        max_order_value=10_000.0,
    )
    assert out["has_recommendation"] is True
    assert out["approval_sent"] is True
    assert approvals == [True]


def test_menu_crypto_command_uses_handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.handle_menu_crypto_recommend",
        lambda *a, **k: {"body": "[ok]", "approval_sent": True, "has_recommendation": True},
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu._send_menu_text",
        lambda cfg, text, keyboard=None: {"ok": True},
    )
    upd = {"message": {"text": MENU_TEXT_RECOMMEND_CRYPTO, "chat": {"id": "12345"}}}
    out = process_crypto_telegram_menu_message(upd, cfg=_cfg(tmp_path), broker=_br())
    assert out is not None
    assert out.get("action") == "recommend_crypto"
    assert out.get("approval_sent") is True
