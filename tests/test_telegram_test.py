"""telegram_test.py — Telegram MVP connection test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepsignal.live_trading.telegram_test import format_telegram_test_console, load_telegram_notify_env, run_telegram_test


def test_telegram_test_dry_run_writes_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("deepsignal.live_trading.telegram_test._ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", raising=False)

    body, path = run_telegram_test(message="DeepSignal dry-run", send=False, output_dir=tmp_path)

    assert path.is_file()
    assert body["status"] == "dry_run"
    assert body["network_called"] is False
    assert body["kis_post_called"] is False
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["message"] == "DeepSignal dry-run"
    assert "telegram_test_" in path.name
    console = format_telegram_test_console(body, path)
    assert "dry-run" in console


def test_telegram_test_send_mock(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "12345")
    calls: list[dict[str, object]] = []

    def fake_post(method: str, payload: dict[str, object], **kwargs):
        calls.append({"method": method, "payload": payload, **kwargs})
        return {"ok": True, "network_called": True, "status": "mocked"}

    monkeypatch.setattr("deepsignal.live_trading.telegram_test.telegram_api_post", fake_post)

    body, path = run_telegram_test(message="hello", send=True, output_dir=tmp_path)

    assert body["status"] == "success"
    assert body["network_called"] is True
    assert calls and calls[0]["method"] == "sendMessage"
    assert calls[0]["payload"]["text"] == "hello"
    assert path.is_file()


def test_telegram_test_send_missing_env_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("deepsignal.live_trading.telegram_test._ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", raising=False)

    body, _path = run_telegram_test(message="x", send=True, output_dir=tmp_path)

    assert body["status"] == "failed"
    assert body["env_errors"]
    assert body["network_called"] is False


def test_load_telegram_notify_env_reports_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("deepsignal.live_trading.telegram_test._ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", raising=False)

    bot, chat, errors = load_telegram_notify_env()

    assert bot is None
    assert chat is None
    assert len(errors) == 2
