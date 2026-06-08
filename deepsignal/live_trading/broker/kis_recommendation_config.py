"""Env-driven KIS stock recommendation scan breadth."""

from __future__ import annotations

import os
from typing import Any

from deepsignal.live_trading.ai_recommendation.recommendation_model import RecommendationConfig
from deepsignal.live_trading.kis_stock_auto_execute_policy import is_kis_stock_auto_execute_without_approval


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _int_env(key: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        out = default
    else:
        try:
            out = int(float(raw))
        except ValueError:
            out = default
    out = max(minimum, out)
    if maximum is not None:
        out = min(out, maximum)
    return out


def _symbols_from_env(key: str) -> list[str] | None:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return None
    parts = [p.strip().upper() for p in raw.replace(";", ",").split(",") if p.strip()]
    return parts or None


def load_stock_recommendation_config_from_env(
    base: RecommendationConfig | None = None,
) -> RecommendationConfig:
    """Merge KIS stock scan settings from env into RecommendationConfig."""
    cfg = base or RecommendationConfig()
    auto = is_kis_stock_auto_execute_without_approval()
    scan_default = 2000 if auto else 500
    scan_limit = _int_env("KIS_STOCK_SIGNAL_SCAN_LIMIT", scan_default, minimum=100, maximum=5000)
    max_rec = _int_env("KIS_STOCK_MAX_RECOMMENDATIONS", 50 if auto else cfg.max_recommendations, minimum=1, maximum=200)
    universe_limit = _int_env("KIS_STOCK_MARKET_UNIVERSE_LIMIT", 3000, minimum=50, maximum=10000)
    include_universe_raw = (os.environ.get("KIS_STOCK_INCLUDE_MARKET_UNIVERSE") or "").strip()
    if include_universe_raw:
        include_universe = _truthy(include_universe_raw)
    else:
        include_universe = auto

    overrides: dict[str, Any] = {
        "signal_scan_limit": scan_limit,
        "max_recommendations": max_rec,
        "include_market_price_universe": include_universe,
        "market_price_universe_limit": universe_limit,
        "market_price_lookback_days": _int_env("KIS_STOCK_MARKET_LOOKBACK_DAYS", 30, minimum=1, maximum=365),
    }
    extra = _symbols_from_env("KIS_STOCK_EXTRA_SYMBOLS")
    if extra:
        overrides["extra_symbols"] = extra
    if cfg.symbols:
        merged = list(dict.fromkeys([*(cfg.symbols or []), *(extra or [])]))
        overrides["symbols"] = merged or None

    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg


def load_daily_ai_runner_limits_from_env() -> dict[str, float | int]:
    """Optional overrides for daily-ai-auto-runner order caps."""
    out: dict[str, float | int] = {}
    for env_key, attr, default, as_float in (
        ("KIS_STOCK_MAX_ORDER_VALUE", "max_order_value", 300_000.0, True),
        ("KIS_STOCK_MAX_SINGLE_ORDER_VALUE", "max_single_order_value", 300_000.0, True),
        ("KIS_STOCK_MAX_TOTAL_ORDER_VALUE", "max_total_order_value", 300_000.0, True),
        ("KIS_STOCK_MAX_ORDERS_PER_DAY", "max_orders", 3, False),
    ):
        raw = (os.environ.get(env_key) or "").strip()
        if not raw:
            continue
        try:
            val: float | int = float(raw) if as_float else int(float(raw))
        except ValueError:
            val = default
        out[attr] = val
    return out
