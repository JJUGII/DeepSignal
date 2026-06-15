"""Market set snapshots for cross-exchange diff."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.listing.config import LISTING_SNAPSHOT_JSON


def load_market_snapshot(output_dir: str | Path) -> dict[str, Any] | None:
    path = Path(output_dir) / LISTING_SNAPSHOT_JSON
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def save_market_snapshot(
    output_dir: str | Path,
    *,
    upbit: set[str],
    bithumb: set[str],
) -> Path:
    path = Path(output_dir) / LISTING_SNAPSHOT_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cached_at_ts": time.time(),
        "upbit_markets": sorted(upbit),
        "bithumb_markets": sorted(bithumb),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def diff_new_markets(
    current: set[str],
    previous: set[str] | None,
) -> list[str]:
    if not previous:
        return []
    return sorted(m for m in current if m not in previous)
