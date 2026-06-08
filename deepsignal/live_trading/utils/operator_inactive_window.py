"""Operator inactive hours — auto-execute without Telegram approval ([학습루프-UX])."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time

from deepsignal.live_trading.utils.time_utils import DEFAULT_TZ, ensure_timezone_aware, now_kst
from zoneinfo import ZoneInfo


@dataclass
class OperatorInactiveConfig:
    enabled: bool = False
    start_hhmm: str = "20:00"
    end_hhmm: str = "09:00"
    timezone: str = DEFAULT_TZ

    def describe_window(self) -> str:
        return f"{self.start_hhmm}~{self.end_hhmm} ({self.timezone})"


def load_operator_inactive_config_from_env() -> OperatorInactiveConfig:
    flag = (os.environ.get("DEEPSIGNAL_INACTIVE_AUTO_EXECUTE") or "").strip().lower()
    enabled = flag in ("1", "true", "yes", "on")
    return OperatorInactiveConfig(
        enabled=enabled,
        start_hhmm=(os.environ.get("DEEPSIGNAL_INACTIVE_START") or "20:00").strip() or "20:00",
        end_hhmm=(os.environ.get("DEEPSIGNAL_INACTIVE_END") or "09:00").strip() or "09:00",
        timezone=(os.environ.get("DEEPSIGNAL_INACTIVE_TIMEZONE") or DEFAULT_TZ).strip() or DEFAULT_TZ,
    )


def _parse_hhmm(value: str) -> time:
    parts = str(value).strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"invalid time {value!r}, expected HH:MM")
    return time(int(parts[0]), int(parts[1]))


def is_operator_inactive_window(
    now: datetime | None = None,
    *,
    start_hhmm: str = "20:00",
    end_hhmm: str = "09:00",
    timezone: str = DEFAULT_TZ,
) -> bool:
    """True when local time is in the inactive window (default 20:00~09:00, crosses midnight)."""
    current = ensure_timezone_aware(now or now_kst(), default_tz=timezone).astimezone(ZoneInfo(timezone))
    t = current.time()
    start_t = _parse_hhmm(start_hhmm)
    end_t = _parse_hhmm(end_hhmm)
    if start_t < end_t:
        return start_t <= t < end_t
    return t >= start_t or t < end_t


def is_inactive_auto_execute_active(
    now: datetime | None = None,
    *,
    config: OperatorInactiveConfig | None = None,
) -> bool:
    cfg = config or load_operator_inactive_config_from_env()
    if not cfg.enabled:
        return False
    return is_operator_inactive_window(
        now,
        start_hhmm=cfg.start_hhmm,
        end_hhmm=cfg.end_hhmm,
        timezone=cfg.timezone,
    )
