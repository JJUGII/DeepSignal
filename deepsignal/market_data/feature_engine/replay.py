"""Historical replay for FeatureEngine (no look-ahead)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deepsignal.market_data.binance_stream.models import OhlcvBar, OrderBookSnapshot, TradeTick
from deepsignal.market_data.binance_stream.ob_resample import resample_ob_to_1m_buckets
from deepsignal.market_data.binance_stream.parser import parse_depth_snapshot
from deepsignal.market_data.feature_engine.engine import FeatureEngine


def _read_jsonl_before(path: Path, *, max_ts_ms: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_ms = int(row.get("ts_ms") or int(row.get("ts", 0)) * 1000)
            if ts_ms < max_ts_ms:
                rows.append(row)
    return rows


def _bar_close_ms(bar: dict[str, Any], timeframe_min: int) -> int:
    open_ms = int(bar.get("open_ts_ms") or 0)
    return open_ms + int(timeframe_min) * 60_000


def _load_closed_bars(path: Path, *, tf: str, max_ts_ms: int) -> list[OhlcvBar]:
    tf_min = {"1m": 1, "3m": 3, "15m": 15}.get(tf, 1)
    out: list[OhlcvBar] = []
    for row in _read_jsonl_before(path, max_ts_ms=max_ts_ms):
        if not row.get("closed", True):
            continue
        close_ms = _bar_close_ms(row, tf_min)
        if close_ms > max_ts_ms:
            continue
        try:
            out.append(
                OhlcvBar(
                    symbol=str(row["symbol"]),
                    timeframe=tf,
                    open_ts_ms=int(row["open_ts_ms"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                    quote_volume=float(row.get("quote_volume") or 0),
                    trade_count=int(row.get("trade_count") or 0),
                    closed=True,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda b: b.open_ts_ms)
    return out


def _latest_ob_before(rows: list[dict[str, Any]], symbol: str) -> OrderBookSnapshot | None:
    if not rows:
        return None
    last = rows[-1]
    return parse_depth_snapshot(symbol, last)


def build_engine_at(
    symbol: str,
    ts_ms: int,
    *,
    stream_dir: str | Path,
    btc_symbol: str = "BTCUSDT",
    fear_greed_path: str | Path | None = None,
    symbols_for_alt: list[str] | None = None,
) -> FeatureEngine:
    """
    Rebuild FeatureEngine state using only events strictly before ts_ms.
    Bar inclusion: bar_close_time < ts_ms.
  OB snapshot: ts_ms_ob < ts_ms.
    """
    root = Path(stream_dir)
    bars_dir = root / "bars"
    sym = symbol.upper()
    btc = btc_symbol.upper()

    eng = FeatureEngine(btc_symbol=btc, fear_greed_path=fear_greed_path)

    # BTC + alt 1m bars for market/regime context
    syms = {sym, btc}
    if symbols_for_alt:
        syms.update(s.upper() for s in symbols_for_alt)

    alt_quote = 0.0
    for s in syms:
        path_1m = bars_dir / f"{s}_1m.jsonl"
        for bar in _load_closed_bars(path_1m, tf="1m", max_ts_ms=ts_ms):
            eng.on_bar(bar)
            if s != btc:
                alt_quote += float(bar.quote_volume)

    eng._market.alt_quote_vol_1m = alt_quote

    for tf in ("3m", "15m"):
        path_tf = bars_dir / f"{sym}_{tf}.jsonl"
        for bar in _load_closed_bars(path_tf, tf=tf, max_ts_ms=ts_ms):
            eng.on_bar(bar)

    ob_rows = _read_jsonl_before(bars_dir / f"{sym}_ob.jsonl", max_ts_ms=ts_ms)
    book = _latest_ob_before(ob_rows, sym)
    if book is not None:
        eng.on_orderbook(book)

    buckets = resample_ob_to_1m_buckets(ob_rows)
    if buckets:
        last_minute = max(k for k in buckets if k * 1000 < ts_ms)
        eng._state(sym).ob_1m_agg = buckets[last_minute]

    btc_ob = _read_jsonl_before(bars_dir / f"{btc}_ob.jsonl", max_ts_ms=ts_ms)
    btc_book = _latest_ob_before(btc_ob, btc)
    if btc_book is not None and sym != btc:
        pass

  # Funding from live_state snapshot if present
    live_path = root / "live_state.json"
    if live_path.is_file():
        try:
            live = json.loads(live_path.read_text(encoding="utf-8"))
            funding = live.get("funding") or {}
            if isinstance(funding, dict) and sym in funding:
                fr = funding[sym]
                if isinstance(fr, dict) and fr.get("funding_rate") is not None:
                    eng._state(sym).funding_rate = float(fr["funding_rate"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    return eng


def replay_feature_vector(
    symbol: str,
    ts_ms: int,
    *,
    stream_dir: str | Path = "outputs/binance_stream",
    btc_symbol: str = "BTCUSDT",
    fear_greed_path: str | Path | None = None,
    forward_fill: bool = False,
) -> Any:
    eng = build_engine_at(
        symbol,
        int(ts_ms),
        stream_dir=stream_dir,
        btc_symbol=btc_symbol,
        fear_greed_path=fear_greed_path,
    )
    return eng.compute(symbol, forward_fill=forward_fill)
