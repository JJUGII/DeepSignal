"""Alternative.me Fear & Greed index — daily cache."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from deepsignal.live_trading.time_utils import now_kst, now_kst_iso

logger = logging.getLogger(__name__)

FNG_API_URL = "https://api.alternative.me/fng/?limit=1&format=json"
DEFAULT_CACHE_NAME = "fear_greed_cache.json"


def default_cache_path(output_dir: str | Path = "outputs") -> Path:
    return Path(output_dir).expanduser().resolve() / DEFAULT_CACHE_NAME


def load_fear_greed_cache(path: str | Path | None = None) -> dict[str, Any] | None:
    p = Path(path) if path else default_cache_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def fear_greed_for_date(cache: dict[str, Any] | None, day: str) -> float | None:
    if not cache:
        return None
    if str(cache.get("date")) == day and cache.get("value") is not None:
        try:
            return float(cache["value"])
        except (TypeError, ValueError):
            return None
    history = cache.get("history")
    if isinstance(history, dict) and day in history:
        try:
            return float(history[day])
        except (TypeError, ValueError):
            return None
    return None


def fetch_fear_greed_index(*, timeout: float = 15.0) -> dict[str, Any]:
    req = urllib.request.Request(
        FNG_API_URL,
        headers={"User-Agent": "DeepSignal/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not entries or not isinstance(entries, list):
        raise ValueError("unexpected fear & greed API response")
    row = entries[0]
    value = float(row["value"])
    ts = int(row.get("timestamp") or 0)
    day = now_kst().date().isoformat()
    if ts > 0:
        from datetime import datetime, timezone

        day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    return {
        "value": value,
        "value_classification": str(row.get("value_classification") or ""),
        "timestamp": ts,
        "date": day,
        "fetched_at": now_kst_iso(),
        "source": FNG_API_URL,
    }


def update_fear_greed_cache(
    path: str | Path | None = None,
    *,
    force: bool = False,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Fetch at most once per calendar day unless force=True."""
    p = Path(path) if path else default_cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    today = now_kst().date().isoformat()
    existing = load_fear_greed_cache(p)
    if not force and existing and str(existing.get("date")) == today:
        return existing

    history: dict[str, float] = {}
    if existing and isinstance(existing.get("history"), dict):
        history = {str(k): float(v) for k, v in existing["history"].items()}

    try:
        fresh = fetch_fear_greed_index(timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        logger.warning("fear & greed fetch failed: %s", exc)
        if existing:
            return existing
        raise

    history[str(fresh["date"])] = float(fresh["value"])
    out = {
        **fresh,
        "history": history,
    }
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
