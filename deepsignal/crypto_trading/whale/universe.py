"""Select top altcoin markets by 24h KRW turnover (public REST, no API key)."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from deepsignal.crypto_trading.whale.config import WhaleWatchConfig

logger = logging.getLogger(__name__)

_UPBIT = "https://api.upbit.com/v1"
_BITHUMB = "https://api.bithumb.com/v1"


def _krw_markets(base: str) -> list[str]:
    try:
        rows = requests.get(f"{base}/market/all", params={"isDetails": "false"}, timeout=15).json()
    except Exception as exc:
        logger.warning("whale universe market/all failed %s: %s", base, exc)
        return []
    if not isinstance(rows, list):
        return []
    return sorted(
        str(r.get("market") or "").strip().upper()
        for r in rows
        if isinstance(r, dict) and str(r.get("market") or "").startswith("KRW-")
    )


def _tickers(base: str, markets: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(markets), 100):
        chunk = markets[i : i + 100]
        try:
            rows = requests.get(
                f"{base}/ticker",
                params={"markets": ",".join(chunk)},
                timeout=15,
            ).json()
        except Exception as exc:
            logger.warning("whale universe ticker failed %s: %s", base, exc)
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            market = str(row.get("market") or "").strip().upper()
            acc = float(row.get("acc_trade_price_24h") or 0)
            if market and acc > 0:
                out[market] = max(out.get(market, 0.0), acc)
        time.sleep(0.05)
    return out


def select_watch_markets(cfg: WhaleWatchConfig) -> list[str]:
    """Top alt markets by 24h turnover across configured exchanges."""
    volumes: dict[str, float] = {}
    if "upbit" in cfg.exchanges:
        up = _krw_markets(_UPBIT)
        volumes.update(_tickers(_UPBIT, up))
    if "bithumb" in cfg.exchanges:
        bi = _krw_markets(_BITHUMB)
        for m, v in _tickers(_BITHUMB, bi).items():
            volumes[m] = max(volumes.get(m, 0.0), v)

    filtered = [
        (m, v)
        for m, v in volumes.items()
        if m not in cfg.exclude_markets
    ]
    filtered.sort(key=lambda x: x[1], reverse=True)
    return [m for m, _ in filtered[: cfg.max_markets]]
