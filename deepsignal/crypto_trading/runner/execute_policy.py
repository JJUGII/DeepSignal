"""Crypto auto-execute without Telegram approval (24h Upbit)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from deepsignal.live_trading.operator_inactive_window import is_inactive_auto_execute_active

_ENV_KEYS = (
    "CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL",
    "DEEPSIGNAL_CRYPTO_AUTO_EXECUTE",
)


@dataclass
class CryptoAutoExecuteConfig:
    enabled: bool = False
    source_key: str | None = None

    def describe(self) -> str:
        if not self.enabled:
            return "disabled (Telegram 승인 필요)"
        return "enabled (24h 승인 없이 매수·매도)"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def load_crypto_auto_execute_config_from_env() -> CryptoAutoExecuteConfig:
    for key in _ENV_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return CryptoAutoExecuteConfig(enabled=_truthy(raw), source_key=key)
    return CryptoAutoExecuteConfig(enabled=False)


def is_crypto_auto_execute_without_approval() -> bool:
    return load_crypto_auto_execute_config_from_env().enabled


def should_auto_execute_crypto_on_runner_tick() -> bool:
    """Auto-runner tick / pending plan — no Telegram 승인 버튼."""
    return is_crypto_auto_execute_without_approval() or is_inactive_auto_execute_active()


def should_skip_crypto_telegram_approval(*, from_telegram_menu: bool = False) -> bool:
    """True when crypto orders run without inline approve/reject buttons.

    Telegram menu 「현재 추천 보기 — 코인」은 항상 False (승인/거부 버튼).
    """
    if from_telegram_menu:
        return False
    return should_auto_execute_crypto_on_runner_tick()
