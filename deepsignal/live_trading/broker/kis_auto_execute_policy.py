"""KIS domestic stock auto-execute without Telegram approval (regular session)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from deepsignal.live_trading.operator_inactive_window import is_inactive_auto_execute_active

_ENV_KEYS = (
    "KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL",
    "DEEPSIGNAL_KIS_STOCK_AUTO_EXECUTE",
)


@dataclass
class KisStockAutoExecuteConfig:
    enabled: bool = False
    source_key: str | None = None

    def describe(self) -> str:
        if not self.enabled:
            return "disabled (Telegram 승인 필요)"
        return "enabled (장중 승인 없이 매수·매도, 체결 시 Telegram만)"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def load_kis_stock_auto_execute_config_from_env() -> KisStockAutoExecuteConfig:
    for key in _ENV_KEYS:
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return KisStockAutoExecuteConfig(enabled=_truthy(raw), source_key=key)
    return KisStockAutoExecuteConfig(enabled=False)


def is_kis_stock_auto_execute_without_approval() -> bool:
    return load_kis_stock_auto_execute_config_from_env().enabled


def should_skip_kis_telegram_approval() -> bool:
    """True when KIS orders run without inline approve/reject buttons."""
    return is_kis_stock_auto_execute_without_approval() or is_inactive_auto_execute_active()


def should_notify_kis_plan_with_no_orders() -> bool:
    """Operator Telegram for empty daily plans (approval mode only)."""
    return not should_skip_kis_telegram_approval()
