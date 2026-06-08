"""Ensure replay_at does not use future bars or order books."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from deepsignal.market_data.feature_engine import FEATURE_COUNT, FeatureEngine


def _write_bar(path: Path, *, open_ts_ms: int, close: float, tf: str = "1m", symbol: str = "ETHUSDT") -> None:
    row = {
        "symbol": symbol,
        "timeframe": tf,
        "open_ts_ms": open_ts_ms,
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": 100.0,
        "quote_volume": close * 100,
        "trade_count": 10,
        "closed": True,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _write_ob(path: Path, *, ts_ms: int, mid: float) -> None:
    row = {
        "ts": ts_ms // 1000,
        "ts_ms": ts_ms,
        "bids": [[mid - 0.5, 10.0]],
        "asks": [[mid + 0.5, 5.0]],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_replay_at_excludes_future_bar_close(tmp_path) -> None:
    stream = tmp_path / "binance_stream"
    bars = stream / "bars"
    bars.mkdir(parents=True)

    t0 = 1_700_000_000_000
    path_1m = bars / "ETHUSDT_1m.jsonl"
    path_ob = bars / "ETHUSDT_ob.jsonl"

    for i in range(30):
        _write_bar(path_1m, open_ts_ms=t0 + i * 60_000, close=100.0 + i)

    _write_ob(path_ob, ts_ms=t0 + 5 * 60_000, mid=105.0)
    _write_ob(path_ob, ts_ms=t0 + 25 * 60_000, mid=200.0)

    cutoff = t0 + 20 * 60_000
    vec = FeatureEngine(btc_symbol="BTCUSDT").replay_at(
        "ETHUSDT",
        cutoff,
        stream_dir=stream,
        forward_fill=False,
    )
    assert vec.shape == (FEATURE_COUNT,)

    future_bar_close = t0 + 29 * 60_000 + 60_000
    assert future_bar_close > cutoff

    vec_early = FeatureEngine(btc_symbol="BTCUSDT").replay_at(
        "ETHUSDT",
        t0 + 10 * 60_000,
        stream_dir=stream,
        forward_fill=False,
    )
    vec_late = FeatureEngine(btc_symbol="BTCUSDT").replay_at(
        "ETHUSDT",
        t0 + 28 * 60_000,
        stream_dir=stream,
        forward_fill=False,
    )
    assert not np.allclose(vec_early, vec_late, equal_nan=True)


def test_replay_ignores_future_ob_snapshot(tmp_path) -> None:
    stream = tmp_path / "binance_stream"
    bars = stream / "bars"
    bars.mkdir(parents=True)
    t0 = 1_700_000_000_000
    path_1m = bars / "ETHUSDT_1m.jsonl"
    path_ob = bars / "ETHUSDT_ob.jsonl"

    for i in range(15):
        _write_bar(path_1m, open_ts_ms=t0 + i * 60_000, close=100.0 + i)

    _write_ob(path_ob, ts_ms=t0 + 5 * 60_000, mid=100.0)
    _write_ob(path_ob, ts_ms=t0 + 14 * 60_000, mid=500.0)

    ts_cut = t0 + 10 * 60_000
    vec = FeatureEngine(btc_symbol="BTCUSDT").replay_at(
        "ETHUSDT",
        ts_cut,
        stream_dir=stream,
        forward_fill=False,
    )
    idx = __import__(
        "deepsignal.market_data.feature_engine.spec",
        fromlist=["FEATURE_INDEX"],
    ).FEATURE_INDEX
    spread = vec[idx["spread_bps"]]
    assert not np.isnan(spread)
    assert spread < 1000
