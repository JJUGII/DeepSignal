"""Background listing watch scan loop for Web UI."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from deepsignal.crypto_trading.listing.config import ListingWatchConfig
from deepsignal.crypto_trading.listing.scanner import load_listing_watch_latest, run_listing_scan
from deepsignal.web_ui.event_bus import EventBus

log = logging.getLogger(__name__)


async def listing_watch_loop(output_dir: Path, event_bus: EventBus) -> None:
    """Periodic cross-exchange listing scan (read-only, no auto-buy)."""
    while True:
        cfg = ListingWatchConfig.from_env()
        interval_sec = max(300, cfg.interval_minutes * 60)
        if cfg.enabled:
            try:
                result = await asyncio.to_thread(
                    run_listing_scan,
                    output_dir,
                    cfg=cfg,
                    network=True,
                    send_alerts=True,
                )
                await event_bus.publish("listing_watch", result.to_dict())
            except Exception as exc:
                log.warning("listing watch scan failed: %s", exc)
        await asyncio.sleep(interval_sec)
