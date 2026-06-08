"""Binance stream OHLCV aggregation and message parsing (offline)."""

from __future__ import annotations

import json

from deepsignal.market_data.binance_stream.config import BinanceStreamConfig
from deepsignal.market_data.binance_stream.ohlcv import OhlcvAggregator
from deepsignal.market_data.binance_stream.models import TradeTick
from deepsignal.market_data.binance_stream.parser import (
    parse_depth_snapshot,
    parse_mark_price,
    parse_trade,
)
from deepsignal.market_data.binance_stream.pipeline import (
    BinanceRealtimePipeline,
    build_spot_stream_names,
    combined_ws_url,
)
from deepsignal.market_data.binance_stream.symbols import resolve_stream_symbols


def test_ohlcv_1m_bar_closes_on_next_minute() -> None:
    agg = OhlcvAggregator("BTCUSDT", timeframes_minutes=(1,))
    t0 = 1_700_000_000_000
    assert not agg.on_trade(TradeTick("BTCUSDT", 100.0, 1.0, t0, False))
    closed = agg.on_trade(TradeTick("BTCUSDT", 101.0, 2.0, t0 + 60_000, False))
    assert len(closed) == 1
    bar = closed[0]
    assert bar.timeframe == "1m"
    assert bar.open == 100.0
    assert bar.close == 100.0
    assert bar.high == 100.0
    assert bar.volume == 1.0


def test_ohlcv_multi_timeframe() -> None:
    agg = OhlcvAggregator("ETHUSDT", timeframes_minutes=(1, 3, 15))
    base = 1_700_000_000_000
    agg.on_trade(TradeTick("ETHUSDT", 10.0, 1.0, base, True))
    closed = agg.on_trade(TradeTick("ETHUSDT", 11.0, 1.0, base + 180_000, True))
    tfs = {b.timeframe for b in closed}
    assert "1m" in tfs
    assert "3m" in tfs


def test_parse_trade_and_depth() -> None:
    trade = parse_trade(
        {
            "e": "trade",
            "s": "BTCUSDT",
            "p": "65000.10",
            "q": "0.01",
            "T": 1700000000123,
            "m": False,
        }
    )
    assert trade is not None
    assert trade.symbol == "BTCUSDT"
    assert trade.price == 65000.10

    book = parse_depth_snapshot(
        "BTCUSDT",
        {
            "lastUpdateId": 99,
            "bids": [["64999", "1.2"]],
            "asks": [["65001", "0.8"]],
        },
        ts_ms=1700000000999,
    )
    assert book.best_bid == 64999.0
    assert book.spread_bps is not None
    assert book.spread_bps > 0


def test_parse_mark_price() -> None:
    snap = parse_mark_price(
        {
            "e": "markPriceUpdate",
            "s": "BTCUSDT",
            "p": "65000",
            "r": "0.0001",
            "T": 1700000100000,
            "E": 1700000099999,
        }
    )
    assert snap is not None
    assert snap.funding_rate == 0.0001


def test_pipeline_handle_combined_message() -> None:
    cfg = BinanceStreamConfig(symbols=("BTCUSDT",), output_dir="outputs/test_binance_stream")
    pipe = BinanceRealtimePipeline(cfg)
    pipe.prepare()
    raw = {
        "stream": "btcusdt@trade",
        "data": {
            "e": "trade",
            "s": "BTCUSDT",
            "p": "100",
            "q": "1",
            "T": 1_700_000_000_000,
            "m": False,
        },
    }
    pipe.handle_payload(raw)
    assert pipe.stats["trades"] == 1
    assert pipe.btc_tick is not None


def test_build_ws_url() -> None:
    streams = build_spot_stream_names(["BTCUSDT", "ETHUSDT"], depth_levels=20)
    assert "btcusdt@trade" in streams
    assert "btcusdt@depth20@100ms" in streams
    url = combined_ws_url("wss://stream.binance.com:9443", streams)
    assert url.startswith("wss://stream.binance.com:9443/stream?")


def test_resolve_symbols_explicit() -> None:
    cfg = BinanceStreamConfig(symbols=("ETHUSDT", "BTCUSDT"))
    syms = resolve_stream_symbols(cfg)
    assert syms[0] == "BTCUSDT"
    assert "ETHUSDT" in syms
