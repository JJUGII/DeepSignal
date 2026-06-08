"""Resolve top Binance USDT symbols by 24h quote volume."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from deepsignal.market_data.binance_stream.config import BinanceStreamConfig


def fetch_top_usdt_symbols(
    *,
    top_n: int = 30,
    quote: str = "USDT",
    rest_base: str = "https://api.binance.com",
    stablecoin_bases: tuple[str, ...] = ("USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI"),
    timeout: float = 15.0,
) -> list[str]:
    url = f"{rest_base.rstrip('/')}/api/v3/ticker/24hr"
    req = Request(url, headers={"User-Agent": "DeepSignal/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Binance 24hr ticker fetch failed: {exc}") from exc

    if not isinstance(payload, list):
        raise RuntimeError("unexpected Binance ticker response")

    quote_u = quote.upper()
    rows: list[tuple[float, str]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper()
        if not sym.endswith(quote_u):
            continue
        base = sym[: -len(quote_u)]
        if base in stablecoin_bases:
            continue
        try:
            qv = float(row.get("quoteVolume") or 0.0)
        except (TypeError, ValueError):
            qv = 0.0
        rows.append((qv, sym))

    rows.sort(key=lambda x: x[0], reverse=True)
    return [sym for _, sym in rows[: max(1, int(top_n))]]


def resolve_stream_symbols(cfg: BinanceStreamConfig) -> list[str]:
    if cfg.symbols:
        return cfg.resolved_symbols([])
    fetched = fetch_top_usdt_symbols(
        top_n=cfg.top_n,
        quote=cfg.quote_asset,
        rest_base=cfg.rest_base,
        stablecoin_bases=cfg.stablecoin_bases,
    )
    return cfg.resolved_symbols(fetched)
