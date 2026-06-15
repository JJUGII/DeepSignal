"""Listing watch configuration from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ListingWatchConfig:
    enabled: bool = True
    interval_minutes: int = 10
    min_acc_trade_24h: float = 100_000_000.0
    vol_ratio_alert: float = 2.5
    anomaly_score_alert: float = 65.0
    telegram_alert: bool = False
    max_deep_scan: int = 60
    telegram_cooldown_sec: float = 14_400.0  # 4h per market

    @classmethod
    def from_env(cls) -> ListingWatchConfig:
        return cls(
            enabled=_truthy("LISTING_SCAN_ENABLED", "true"),
            interval_minutes=max(5, _int_env("LISTING_SCAN_INTERVAL_MIN", 10)),
            min_acc_trade_24h=max(0.0, _float_env("LISTING_MIN_ACC_TRADE_24H", 100_000_000)),
            vol_ratio_alert=max(1.0, _float_env("LISTING_VOL_RATIO_ALERT", 2.5)),
            anomaly_score_alert=max(0.0, _float_env("LISTING_ANOMALY_SCORE_ALERT", 65.0)),
            telegram_alert=_truthy("LISTING_TELEGRAM_ALERT", "false"),
            max_deep_scan=max(10, _int_env("LISTING_MAX_DEEP_SCAN", 60)),
            telegram_cooldown_sec=max(300.0, _float_env("LISTING_TELEGRAM_COOLDOWN_SEC", 14_400)),
        )


LISTING_SNAPSHOT_JSON = "LISTING_MARKET_SNAPSHOT.json"
LISTING_WATCH_JSON = "LISTING_WATCH_LATEST.json"
LISTING_ALERT_COOLDOWN_JSON = "LISTING_ALERT_COOLDOWN.json"
