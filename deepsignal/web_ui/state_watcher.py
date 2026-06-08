"""Watch outputs/ state files and publish change events to the EventBus."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from deepsignal.web_ui.event_bus import EventBus

log = logging.getLogger(__name__)

# Files to watch and the event type each one emits
_WATCH_MAP: dict[str, str] = {
    "CRYPTO_AUTO_RUNNER_STATE.json":            "runner_state",
    "CRYPTO_ORDER_PLAN.json":                   "order_plan",
    "crypto_telegram_approval_request.json":    "crypto_approval_request",
    "TELEGRAM_APPROVAL_STATE.json":             "stock_approval_update",
    "WEBUI_RUNNER_PID.json":                    "runner_pid",
}

_POLL_INTERVAL = 1.0  # seconds


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def watch_loop(output_dir: Path, event_bus: EventBus) -> None:
    """Asyncio task: poll state files every second, publish on change."""
    mtimes: dict[str, float] = {}

    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        for filename, event_type in _WATCH_MAP.items():
            path = output_dir / filename
            try:
                mtime = path.stat().st_mtime if path.exists() else -1.0
            except OSError:
                mtime = -1.0

            prev = mtimes.get(filename, -2.0)
            if mtime != prev:
                mtimes[filename] = mtime
                if mtime > 0:
                    data = _read_json(path)
                    data["_file"] = filename
                else:
                    data = {"_file": filename, "_deleted": True}
                try:
                    await event_bus.publish(event_type, data)
                except Exception as exc:
                    log.debug("event_bus publish error: %s", exc)
