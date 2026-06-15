"""Telegram menu poll — offset, mixed updates, text matching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepsignal.crypto_trading.crypto_telegram_flow import CryptoTelegramConfig
from deepsignal.crypto_trading.crypto_telegram_menu import (
    MENU_TEXT_HOLDINGS,
    MENU_TEXT_RECOMMEND_CRYPTO,
    MENU_TEXT_RECOMMEND_KIS,
    normalize_menu_text,
    poll_telegram_updates_once,
    process_crypto_telegram_menu_message,
)
from deepsignal.crypto_trading.crypto_telegram_offset import (
    load_telegram_offset,
    offset_path,
    save_telegram_offset,
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


def test_normalize_menu_text_trims_whitespace() -> None:
    assert normalize_menu_text("  현재 내 자산 보기 \n") == MENU_TEXT_HOLDINGS


def test_offset_migration_legacy_offset_key(tmp_path: Path) -> None:
    offset_path(tmp_path).write_text(json.dumps({"offset": 42}), encoding="utf-8")
    assert load_telegram_offset(tmp_path) == 42
    save_telegram_offset(tmp_path, 99)
    data = json.loads(offset_path(tmp_path).read_text(encoding="utf-8"))
    assert data["last_update_id"] == 99
    assert data["message_offset"] == 99
    assert data["callback_offset"] == 99


def test_holdings_text_message(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def fake_send(cfg, text, keyboard=None):
        sent.append(text or "")
        return {"ok": True}

    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu._send_menu_text",
        fake_send,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.format_kis_holdings_telegram",
        lambda db: ["KIS line"],
    )
    upd = {"update_id": 1, "message": {"text": f"  {MENU_TEXT_HOLDINGS}  ", "chat": {"id": "12345"}}}
    out = process_crypto_telegram_menu_message(
        upd, cfg=_cfg(Path("outputs")), broker=_br(), db_path=":memory:"
    )
    assert out is not None
    assert out.get("action") == "holdings"
    assert sent
    assert "국내주식" in sent[0] or "DeepSignal" in sent[0]


def test_recommend_crypto_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu._send_menu_text",
        lambda cfg, text, keyboard=None: {"ok": True},
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_telegram_menu.handle_menu_crypto_recommend",
        lambda *a, **k: {"body": "[crypto rec]", "approval_sent": False, "has_recommendation": False},
    )
    upd = {"update_id": 2, "message": {"text": MENU_TEXT_RECOMMEND_CRYPTO, "chat": {"id": "12345"}}}
    out = process_crypto_telegram_menu_message(upd, cfg=_cfg(Path("outputs")), broker=_br())
    assert out is not None
    assert out.get("action") == "recommend_crypto"


def test_mixed_callback_and_text_single_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    updates = [
        {
            "update_id": 10,
            "message": {"text": MENU_TEXT_HOLDINGS, "chat": {"id": "12345"}},
        },
        {
            "update_id": 11,
            "callback_query": {
                "id": "cb1",
                "data": "crypto_reject:tok",
                "message": {"chat": {"id": "12345"}},
            },
        },
    ]
    def fake_get_updates(cfg, offset=None):
        if offset is not None and int(offset) >= 12:
            return []
        return updates

    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.telegram_get_updates",
        fake_get_updates,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu.process_crypto_telegram_menu_message",
        lambda upd, **kw: {"action": "holdings"},
    )
    import deepsignal.crypto_trading.crypto_telegram_flow as tg_flow

    monkeypatch.setattr(
        tg_flow,
        "process_crypto_telegram_update",
        lambda upd, **kw: {"status": "REJECTED"},
    )
    cfg = _cfg(tmp_path)
    summary = poll_telegram_updates_once(cfg, _br(), process_approvals=True)
    assert summary["updates"] == 2
    assert len(summary["menu"]) == 1
    assert len(summary["callbacks"]) == 1
    assert load_telegram_offset(tmp_path) == 12

    summary2 = poll_telegram_updates_once(cfg, _br(), process_approvals=True)
    assert summary2["updates"] == 0


def test_recommend_kis_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "deepsignal.crypto_trading.telegram.menu._send_menu_text",
        lambda cfg, text, keyboard=None: {"ok": True},
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_telegram_menu.handle_menu_kis_recommend",
        lambda *a, **kw: {"body": "[kis rec]", "approval_sent": False, "has_orders": False},
    )
    upd = {"update_id": 3, "message": {"text": MENU_TEXT_RECOMMEND_KIS, "chat": {"id": "12345"}}}
    out = process_crypto_telegram_menu_message(upd, cfg=_cfg(Path("outputs")), broker=_br())
    assert out is not None
    assert out.get("action") == "recommend_kis"
