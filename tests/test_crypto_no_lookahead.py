"""Label/feature leakage guards for replay_at and validation dataset."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from deepsignal.market_data.feature_engine import FEATURE_COUNT, FEATURE_NAMES, FeatureEngine
from deepsignal.market_data.feature_engine.replay import _read_jsonl_before, build_engine_at
from deepsignal.market_data.feature_engine.spec import FEATURE_INDEX
from deepsignal.ml.crypto_validate_ml import build_replay_dataset, filter_bars_last_days
from deepsignal.market_data.binance_stream.models import OhlcvBar


def _bar(
    symbol: str,
    open_ts_ms: int,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
) -> OhlcvBar:
    h = high if high is not None else close + 0.5
    l = low if low is not None else close - 0.5
    return OhlcvBar(
        symbol=symbol,
        timeframe="1m",
        open_ts_ms=open_ts_ms,
        open=close - 0.1,
        high=h,
        low=l,
        close=close,
        volume=100.0,
        quote_volume=close * 100,
        trade_count=1,
        closed=True,
    )


def _write_ob(path: Path, ts_ms: int, mid: float) -> None:
    row = {
        "ts": ts_ms // 1000,
        "ts_ms": ts_ms,
        "bids": [[mid - 0.5, 10.0]],
        "asks": [[mid + 0.5, 5.0]],
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def test_replay_at_unchanged_when_future_bars_appended(tmp_path: Path) -> None:
    """Adding bars after t must not change replay_at(t)."""
    stream = tmp_path / "binance_stream"
    bars_dir = stream / "bars"
    bars_dir.mkdir(parents=True)
    sym = "ETHUSDT"
    t0 = 1_700_000_000_000

    bars: list[OhlcvBar] = []
    for i in range(40):
        bars.append(_bar(sym, t0 + i * 60_000, 100.0 + i * 0.01))

    path_1m = bars_dir / f"{sym}_1m.jsonl"
    for b in bars[:25]:
        with path_1m.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(b.to_dict()) + "\n")

    _write_ob(bars_dir / f"{sym}_ob.jsonl", t0 + 20 * 60_000, 105.0)

    t_cut = t0 + 20 * 60_000 + 60_000
    vec_before = FeatureEngine(btc_symbol="BTCUSDT").replay_at(
        sym, t_cut, stream_dir=stream, forward_fill=False
    )

    with path_1m.open("a", encoding="utf-8") as fh:
        for b in bars[25:]:
            fh.write(json.dumps(b.to_dict()) + "\n")
    _write_ob(bars_dir / f"{sym}_ob.jsonl", t0 + 39 * 60_000, 999.0)

    vec_after = FeatureEngine(btc_symbol="BTCUSDT").replay_at(
        sym, t_cut, stream_dir=stream, forward_fill=False
    )
    assert vec_before.shape == (FEATURE_COUNT,)
    assert np.allclose(vec_before, vec_after, equal_nan=True, rtol=1e-9, atol=1e-9)


def test_ret_1m_uses_only_past_closes(tmp_path: Path) -> None:
    """ret_1m at t must not incorporate the close of the bar starting at t."""
    stream = tmp_path / "binance_stream"
    bars_dir = stream / "bars"
    bars_dir.mkdir(parents=True)
    sym = "ETHUSDT"
    t0 = 1_700_000_000_000
    path_1m = bars_dir / f"{sym}_1m.jsonl"

    for i in range(10):
        close = 100.0 if i < 9 else 200.0
        b = _bar(sym, t0 + i * 60_000, close)
        with path_1m.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(b.to_dict()) + "\n")

    t8_close = t0 + 8 * 60_000 + 60_000
    vec = FeatureEngine(btc_symbol="BTCUSDT").replay_at(
        sym, t8_close, stream_dir=stream, forward_fill=False
    )
    idx = FEATURE_INDEX["ret_1m"]
    ret = vec[idx]
    assert not np.isnan(ret)
    assert abs(ret) < 0.05


def test_jsonl_before_excludes_boundary(tmp_path: Path) -> None:
    p = tmp_path / "x.jsonl"
    rows = [{"ts_ms": 1000}, {"ts_ms": 2000}, {"ts_ms": 3000}]
    for r in rows:
        with p.open("a") as fh:
            fh.write(json.dumps(r) + "\n")
    got = _read_jsonl_before(p, max_ts_ms=2000)
    assert len(got) == 1
    assert got[0]["ts_ms"] == 1000


def test_build_engine_bar_close_not_after_ts(tmp_path: Path) -> None:
    stream = tmp_path / "binance_stream"
    bars_dir = stream / "bars"
    bars_dir.mkdir(parents=True)
    sym = "ETHUSDT"
    t0 = 1_700_000_000_000
    path_1m = bars_dir / f"{sym}_1m.jsonl"
    for i in range(15):
        b = _bar(sym, t0 + i * 60_000, 50.0 + i)
        with path_1m.open("a") as fh:
            fh.write(json.dumps(b.to_dict()) + "\n")

    ts_ms = t0 + 10 * 60_000 + 60_000
    eng = build_engine_at(sym, ts_ms, stream_dir=stream, btc_symbol="BTCUSDT")
    closes = list(eng._state(sym).closes_1m)
    assert closes
    assert max(closes) < 200.0


def test_validation_dataset_timestamps_before_labels(tmp_path: Path) -> None:
    from deepsignal.ml.crypto_validate_ml import ValidateMlConfig

    stream = tmp_path / "binance_stream"
    bars_dir = stream / "bars"
    bars_dir.mkdir(parents=True)
    sym = "ETHUSDT"
    t0 = 1_700_000_000_000
    bars = [_bar(sym, t0 + i * 60_000, 100.0 + i * 0.01) for i in range(90)]
    path_1m = bars_dir / f"{sym}_1m.jsonl"
    for b in bars:
        with path_1m.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(b.to_dict()) + "\n")
    _write_ob(bars_dir / f"{sym}_ob.jsonl", t0 + 30 * 60_000, 110.0)

    cfg = ValidateMlConfig(horizon_minutes=5, fee_rate=0.0005, min_warmup_bars=30)
    data, _ = build_replay_dataset({sym: bars}, stream_dir=stream, cfg=cfg)
    assert data.n_samples > 10
    horizon_ms = 5 * 60_000
    for ts, y in zip(data.timestamps_ms, data.y):
        assert int(ts) + horizon_ms <= bars[-1].open_ts_ms + 60_000
