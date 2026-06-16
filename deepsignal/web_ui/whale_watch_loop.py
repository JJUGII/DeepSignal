"""Background whale trade watch for Web UI."""

from __future__ import annotations

import logging
from pathlib import Path

from deepsignal.crypto_trading.whale.watch import get_whale_service
from deepsignal.web_ui.event_bus import EventBus

log = logging.getLogger(__name__)


async def whale_watch_loop(output_dir: Path, event_bus: EventBus) -> None:
    """Stream large KRW trades and push to event bus."""
    from dotenv import load_dotenv

    load_dotenv(override=False)

    async def _on_whale(data: dict) -> None:
        await event_bus.publish("whale_trade", data)

    service = get_whale_service()
    try:
        await service.run(on_whale=_on_whale)
    except Exception as exc:
        log.warning("whale watch loop ended: %s", exc)
