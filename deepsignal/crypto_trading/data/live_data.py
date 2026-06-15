"""Binance stream live_state freshness checks."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.data.stream_stale_alert import (
    live_state_path,
    parse_live_state_age_seconds,
    stream_stale_threshold_seconds,
)


@dataclass
class BinanceLiveStateStatus:
    exists: bool
    stale: bool
    age_seconds: float | None
    threshold_seconds: float
    path: str
    generated_at: str | None = None
    symbol_count: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "stale": self.stale,
            "age_seconds": self.age_seconds,
            "threshold_seconds": self.threshold_seconds,
            "path": self.path,
            "generated_at": self.generated_at,
            "symbol_count": self.symbol_count,
            "reason": self.reason,
        }


def check_binance_live_state(output_dir: str | Path) -> BinanceLiveStateStatus:
    path = live_state_path(output_dir)
    threshold = stream_stale_threshold_seconds()
    if not path.is_file():
        return BinanceLiveStateStatus(
            exists=False,
            stale=True,
            age_seconds=None,
            threshold_seconds=threshold,
            path=str(path),
            reason="live_state.json not found",
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return BinanceLiveStateStatus(
            exists=True,
            stale=True,
            age_seconds=None,
            threshold_seconds=threshold,
            path=str(path),
            reason=f"failed to read live_state: {exc}",
        )

    if not isinstance(payload, dict):
        payload = {}

    generated_at = str(payload.get("generated_at") or "").strip() or None
    age = parse_live_state_age_seconds(payload, path=path)
    stale = age is None or age > threshold
    symbols = payload.get("symbols") or list((payload.get("orderbooks") or {}).keys())
    reason = "ok"
    if stale:
        if age is None:
            reason = "missing or invalid generated_at"
        else:
            reason = f"age {age:.1f}s exceeds threshold {threshold:.1f}s"

    return BinanceLiveStateStatus(
        exists=True,
        stale=stale,
        age_seconds=age,
        threshold_seconds=threshold,
        path=str(path),
        generated_at=generated_at,
        symbol_count=len(symbols) if isinstance(symbols, list) else 0,
        reason=reason,
    )


def live_state_fresh(output_dir: str | Path) -> bool:
    return not check_binance_live_state(output_dir).stale


def live_state_max_age_seconds() -> float:
    override = os.getenv("CRYPTO_LIVE_STATE_MAX_AGE_SECONDS", "").strip()
    if override:
        try:
            return max(1.0, float(override))
        except ValueError:
            pass
    return stream_stale_threshold_seconds()
