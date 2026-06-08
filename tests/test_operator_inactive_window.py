"""Tests for operator inactive window (20:00~09:00 KST)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from deepsignal.live_trading.operator_inactive_window import (
    OperatorInactiveConfig,
    is_inactive_auto_execute_active,
    is_operator_inactive_window,
    load_operator_inactive_config_from_env,
)


def _kst(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 5, 20, hour, minute, tzinfo=ZoneInfo("Asia/Seoul"))


def test_inactive_window_crosses_midnight() -> None:
    assert is_operator_inactive_window(_kst(21, 0), start_hhmm="20:00", end_hhmm="09:00")
    assert is_operator_inactive_window(_kst(8, 30), start_hhmm="20:00", end_hhmm="09:00")
    assert not is_operator_inactive_window(_kst(10, 0), start_hhmm="20:00", end_hhmm="09:00")
    assert not is_operator_inactive_window(_kst(15, 0), start_hhmm="20:00", end_hhmm="09:00")


def test_inactive_auto_requires_env_flag(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSIGNAL_INACTIVE_AUTO_EXECUTE", raising=False)
    cfg = load_operator_inactive_config_from_env()
    assert cfg.enabled is False
    assert is_inactive_auto_execute_active(_kst(22, 0), config=cfg) is False

    monkeypatch.setenv("DEEPSIGNAL_INACTIVE_AUTO_EXECUTE", "true")
    cfg_on = load_operator_inactive_config_from_env()
    assert cfg_on.enabled is True
    assert is_inactive_auto_execute_active(_kst(22, 0), config=cfg_on) is True
    assert is_inactive_auto_execute_active(_kst(12, 0), config=cfg_on) is False
