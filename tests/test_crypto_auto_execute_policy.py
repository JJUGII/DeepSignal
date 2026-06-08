"""crypto_auto_execute_policy — 24h no-approval mode."""

from __future__ import annotations

import pytest

from deepsignal.crypto_trading.crypto_auto_execute_policy import (
    is_crypto_auto_execute_without_approval,
    load_crypto_auto_execute_config_from_env,
    should_auto_execute_crypto_on_runner_tick,
    should_skip_crypto_telegram_approval,
)


def test_crypto_auto_execute_env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL", raising=False)
    monkeypatch.delenv("DEEPSIGNAL_CRYPTO_AUTO_EXECUTE", raising=False)
    assert not is_crypto_auto_execute_without_approval()


def test_crypto_auto_execute_env_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL", "true")
    cfg = load_crypto_auto_execute_config_from_env()
    assert cfg.enabled
    assert cfg.source_key == "CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL"
    assert should_auto_execute_crypto_on_runner_tick()
    assert should_skip_crypto_telegram_approval()
    assert not should_skip_crypto_telegram_approval(from_telegram_menu=True)


def test_inactive_window_also_skips_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL", raising=False)
    monkeypatch.setenv("DEEPSIGNAL_INACTIVE_AUTO_EXECUTE", "true")
    monkeypatch.setenv("DEEPSIGNAL_INACTIVE_START", "00:00")
    monkeypatch.setenv("DEEPSIGNAL_INACTIVE_END", "23:59")
    assert should_auto_execute_crypto_on_runner_tick()
    assert should_skip_crypto_telegram_approval()
    assert not should_skip_crypto_telegram_approval(from_telegram_menu=True)
