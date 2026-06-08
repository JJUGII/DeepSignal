"""main.py telegram-test CLI tests."""

from __future__ import annotations

from pathlib import Path

import main as main_mod


def test_main_telegram_test_dry_run(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", raising=False)

    rc = main_mod.main(["telegram-test", "--message", "DeepSignal dry-run", "--output-dir", str(tmp_path)])

    assert rc == 0
    assert list(tmp_path.glob("telegram_test_*.json"))
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_main_telegram_test_send_mock(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "99")

    def fake_post(method: str, payload: dict[str, object], **kwargs):
        return {"ok": True, "network_called": True, "method": method}

    monkeypatch.setattr("deepsignal.live_trading.telegram_test.telegram_api_post", fake_post)

    rc = main_mod.main(["telegram-test", "--send", "--message", "hi", "--output-dir", str(tmp_path)])

    assert rc == 0
    assert "Telegram 연결 성공" in capsys.readouterr().out
