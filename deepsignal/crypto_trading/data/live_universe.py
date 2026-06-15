"""Resolve Upbit scan universe from Binance live_state when fresh."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.data.live_data import live_state_fresh
from deepsignal.crypto_trading.data.market_data import DEFAULT_CRYPTO_MARKETS
from deepsignal.crypto_trading.data.stream_stale_alert import live_state_path
from deepsignal.crypto_trading.signal.universe import (
    CryptoUniverseConfig,
    CryptoUniverseResult,
    market_display_name,
    select_markets_for_buy_scan,
)
from deepsignal.crypto_trading.broker.interface import CryptoBroker, CryptoTicker

_SYNTHETIC_ACC_TRADE_24H = 1.0


def live_state_scan_enabled() -> bool:
    raw = os.getenv("CRYPTO_USE_LIVE_STATE_SCAN", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def binance_symbol_to_upbit_market(symbol: str) -> str:
    sym = str(symbol or "").strip().upper()
    if sym.endswith("USDT"):
        return f"KRW-{sym[:-4]}"
    if sym.startswith("KRW-"):
        return sym
    return f"KRW-{sym}"


def _mid_price_from_orderbook(book: dict[str, Any]) -> float:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bid = float(bids[0][0]) if bids and isinstance(bids[0], (list, tuple)) else 0.0
    ask = float(asks[0][0]) if asks and isinstance(asks[0], (list, tuple)) else 0.0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    if bid > 0:
        return bid
    if ask > 0:
        return ask
    return 0.0


def _load_live_payload(output_dir: str | Path) -> dict[str, Any] | None:
    path = live_state_path(output_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def tickers_from_live_state(
    output_dir: str | Path,
    *,
    max_markets: int | None = None,
    valid_upbit_markets: frozenset[str] | None = None,
) -> dict[str, CryptoTicker]:
    payload = _load_live_payload(output_dir)
    if payload is None:
        return {}

    orderbooks = payload.get("orderbooks") or {}
    out: dict[str, CryptoTicker] = {}
    if isinstance(orderbooks, dict):
        for binance_sym, book in orderbooks.items():
            if not isinstance(book, dict):
                continue
            market = binance_symbol_to_upbit_market(str(binance_sym))
            if valid_upbit_markets is not None and market not in valid_upbit_markets:
                continue
            price = _mid_price_from_orderbook(book)
            if price <= 0:
                continue
            out[market] = CryptoTicker(
                market=market,
                trade_price=price,
                signed_change_rate=0.0,
                acc_trade_price_24h=_SYNTHETIC_ACC_TRADE_24H,
            )
            if max_markets is not None and len(out) >= max(1, int(max_markets)):
                break

    if out:
        return out

    symbols = payload.get("symbols") or []
    if not isinstance(symbols, list):
        return {}
    for sym in symbols:
        market = binance_symbol_to_upbit_market(str(sym))
        if valid_upbit_markets is not None and market not in valid_upbit_markets:
            continue
        btc = payload.get("btc") or {}
        price = 0.0
        if isinstance(btc, dict) and str(btc.get("symbol") or "").upper() == str(sym).upper():
            price = float(btc.get("price", 0) or 0)
        if price <= 0:
            continue
        out[market] = CryptoTicker(
            market=market,
            trade_price=price,
            signed_change_rate=0.0,
            acc_trade_price_24h=_SYNTHETIC_ACC_TRADE_24H,
        )
        if max_markets is not None and len(out) >= max(1, int(max_markets)):
            break
    return out


def resolve_crypto_markets_live_first(
    broker: CryptoBroker,
    *,
    config: CryptoUniverseConfig | None = None,
    holdings_markets: tuple[str, ...] | None = None,
    output_dir: str | Path | None = None,
) -> tuple[CryptoUniverseResult | None, str]:
    if output_dir is None or not live_state_scan_enabled() or not live_state_fresh(output_dir):
        return None, "rest"

    cfg = config or CryptoUniverseConfig()
    from deepsignal.crypto_trading.signal.universe import get_upbit_krw_market_set

    valid_upbit = get_upbit_krw_market_set(broker, output_dir=output_dir)
    ticker_map = tickers_from_live_state(
        output_dir,
        max_markets=max(int(cfg.max_buy_scan_markets) * 3, 50),
        valid_upbit_markets=valid_upbit,
    )
    if not ticker_map:
        return None, "rest"

    hold = tuple(h.strip().upper() for h in (holdings_markets or ()) if h and h.strip())
    always = tuple(dict.fromkeys((*DEFAULT_CRYPTO_MARKETS, *cfg.extra_markets, *hold)))
    selected = select_markets_for_buy_scan(
        ticker_map,
        min_acc_trade_price_24h=float(cfg.min_acc_trade_price_24h),
        max_markets=int(cfg.max_buy_scan_markets),
        always_include=always,
    )
    display = {m: market_display_name(m) for m in selected}
    return (
        CryptoUniverseResult(
            markets=tuple(selected),
            universe=cfg.universe,
            total_krw_markets=len(valid_upbit),
            scanned_for_buy=len(selected),
            display_names=display,
            min_acc_trade_price_24h=float(cfg.min_acc_trade_price_24h),
        ),
        "live_state",
    )
