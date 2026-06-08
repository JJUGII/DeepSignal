"""Smoke tests for crypto-validate-ml pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from deepsignal.market_data.binance_stream.models import OhlcvBar
from deepsignal.ml.crypto_validate_ml import (
    ValidateMlConfig,
    build_replay_dataset,
    normalize_symbols,
    run_threshold_sweep,
    write_threshold_report_md,
    write_validation_report_md,
    FoldValidationRow,
)


def test_normalize_symbols() -> None:
    assert normalize_symbols("BTC,ETH") == ["BTCUSDT", "ETHUSDT"]
    assert normalize_symbols("BTCUSDT") == ["BTCUSDT"]


def test_validate_ml_smoke(tmp_path: Path) -> None:
    stream = tmp_path / "binance_stream"
    bars_dir = stream / "bars"
    bars_dir.mkdir(parents=True)
    sym = "ETHUSDT"
    t0 = 1_700_000_000_000
    bars: list[OhlcvBar] = []
    for i in range(120):
        bars.append(
            OhlcvBar(
                symbol=sym,
                timeframe="1m",
                open_ts_ms=t0 + i * 60_000,
                open=100 + i * 0.01,
                high=100.5 + i * 0.01,
                low=99.5 + i * 0.01,
                close=100 + i * 0.01,
                volume=10.0,
                quote_volume=1000.0,
                trade_count=1,
                closed=True,
            )
        )
    path = bars_dir / f"{sym}_1m.jsonl"
    for b in bars:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(b.to_dict()) + "\n")

    cfg = ValidateMlConfig(horizon_minutes=5, fee_rate=0.0005, min_warmup_bars=30, n_splits=3, gap=5)
    data, net = build_replay_dataset({sym: bars}, stream_dir=stream, cfg=cfg)
    assert data.n_samples >= 20
    assert len(net) == data.n_samples

    folds: list[FoldValidationRow] = []
    try:
        from deepsignal.ml.crypto_validate_ml import run_timeseries_cv

        folds, oof = run_timeseries_cv(data, net, cfg=cfg)
        assert len(folds) >= 2
        assert np.sum(~np.isnan(oof)) > 0
    except (RuntimeError, OSError) as exc:
        if "lightgbm" in str(exc).lower() or "libomp" in str(exc).lower():
            pytest.skip("lightgbm/libomp not available")
        raise

    out = tmp_path / "outputs"
    write_validation_report_md(
        out / "CRYPTO_ML_VALIDATION_REPORT.md",
        folds=folds,
        cfg=cfg,
        dataset=data,
        symbols=[sym],
        days=60,
    )
