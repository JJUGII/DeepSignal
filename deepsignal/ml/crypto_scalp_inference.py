"""Runtime sequence feature windows for LSTM/Transformer inference."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from deepsignal.market_data.feature_engine.engine import FeatureEngine
from deepsignal.ml.crypto_scalp_dataset import aggregate_higher_tf, load_bars_jsonl


def recent_sequence_matrix(
    bars_dir: str | Path,
    symbol: str,
    *,
    seq_len: int = 30,
    btc_symbol: str = "BTCUSDT",
    min_warmup: int = 61,
) -> np.ndarray | None:
    """Last seq_len feature vectors from 1m bar replay (no lookahead)."""
    sym = symbol.upper()
    path = Path(bars_dir) / f"{sym}_1m.jsonl"
    if not path.is_file():
        return None
    bars = load_bars_jsonl(path)
    if len(bars) < min_warmup + seq_len:
        return None
    btc_path = Path(bars_dir) / f"{btc_symbol.upper()}_1m.jsonl"
    btc_bars = load_bars_jsonl(btc_path) if btc_path.is_file() else []

    eng = FeatureEngine(btc_symbol=btc_symbol)
    btc_idx = 0
    last_3m = last_15m = None
    vecs: list[np.ndarray] = []

    for i, bar in enumerate(bars):
        while btc_idx < len(btc_bars) and btc_bars[btc_idx].open_ts_ms <= bar.open_ts_ms:
            eng.on_bar(btc_bars[btc_idx])
            btc_idx += 1
        eng.on_bar(bar)
        sl = bars[: i + 1]
        b3 = aggregate_higher_tf(sl, 3)
        if b3:
            k = b3[-1].open_ts_ms
            if k != last_3m:
                eng.on_bar(b3[-1])
                last_3m = k
        b15 = aggregate_higher_tf(sl, 15)
        if b15:
            k = b15[-1].open_ts_ms
            if k != last_15m:
                eng.on_bar(b15[-1])
                last_15m = k
        if i < min_warmup:
            continue
        vecs.append(eng.compute(sym, forward_fill=True))

    if len(vecs) < seq_len:
        return None
    return np.stack(vecs[-seq_len:], axis=0)
