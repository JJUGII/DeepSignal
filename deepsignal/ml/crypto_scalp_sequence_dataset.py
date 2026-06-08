"""Sequence dataset (seq_len, n_features) for LSTM/Transformer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from deepsignal.market_data.binance_stream.models import OhlcvBar
from deepsignal.market_data.feature_engine.spec import FEATURE_COUNT, FEATURE_NAMES
from deepsignal.ml.crypto_scalp_dataset import build_dataset_from_bars, load_bars_jsonl
from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig


@dataclass
class SequenceDataset:
    X: np.ndarray  # (n, seq_len, n_features)
    y: np.ndarray
    timestamps_ms: np.ndarray
    symbols: np.ndarray
    seq_len: int
    feature_names: tuple[str, ...] = FEATURE_NAMES

    @property
    def n_samples(self) -> int:
        return int(self.y.shape[0])

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_samples": self.n_samples,
            "seq_len": self.seq_len,
            "positive_rate": float(np.mean(self.y)) if self.n_samples else 0.0,
            "feature_names": list(self.feature_names),
        }


def build_sequence_dataset_from_bars(
    bars_by_symbol: dict[str, list[OhlcvBar]],
    *,
    label_cfg: ScalpLabelConfig | None = None,
    seq_len: int = 30,
    btc_symbol: str = "BTCUSDT",
    min_warmup_bars: int = 61,
) -> SequenceDataset:
    """Rolling feature windows; label at last bar in window (no future in features)."""
    from deepsignal.market_data.feature_engine.engine import FeatureEngine

    cfg = label_cfg or ScalpLabelConfig()
    horizon = int(cfg.horizon_minutes)
    slen = max(2, int(seq_len))
    btc_bars = bars_by_symbol.get(btc_symbol.upper(), [])

    xs: list[np.ndarray] = []
    ys: list[int] = []
    ts_list: list[int] = []
    sym_list: list[str] = []

    for symbol, bars_1m in bars_by_symbol.items():
        if len(bars_1m) < min_warmup_bars + horizon + slen:
            continue
        eng = FeatureEngine(btc_symbol=btc_symbol)
        btc_idx = 0
        last_3m_key = last_15m_key = None
        window: list[np.ndarray] = []

        for i, bar in enumerate(bars_1m):
            while btc_idx < len(btc_bars) and btc_bars[btc_idx].open_ts_ms <= bar.open_ts_ms:
                eng.on_bar(btc_bars[btc_idx])
                btc_idx += 1
            eng.on_bar(bar)
            slice_1m = bars_1m[: i + 1]
            from deepsignal.ml.crypto_scalp_dataset import aggregate_higher_tf

            b3 = aggregate_higher_tf(slice_1m, 3)
            if b3:
                k3 = b3[-1].open_ts_ms
                if k3 != last_3m_key:
                    eng.on_bar(b3[-1])
                    last_3m_key = k3
            b15 = aggregate_higher_tf(slice_1m, 15)
            if b15:
                k15 = b15[-1].open_ts_ms
                if k15 != last_15m_key:
                    eng.on_bar(b15[-1])
                    last_15m_key = k15

            if i < min_warmup_bars:
                continue
            vec = eng.compute(symbol, forward_fill=True)
            window.append(vec.copy())
            if len(window) > slen:
                window.pop(0)
            if len(window) < slen:
                continue
            j = i + horizon
            if j >= len(bars_1m):
                break
            y = cfg.label_from_prices(bars_1m[i].close, bars_1m[j].close)
            if y is None:
                continue
            xs.append(np.stack(window, axis=0))
            ys.append(int(y))
            ts_list.append(int(bar.open_ts_ms))
            sym_list.append(symbol.upper())

    if not xs:
        return SequenceDataset(
            X=np.zeros((0, slen, FEATURE_COUNT)),
            y=np.zeros(0, dtype=np.int8),
            timestamps_ms=np.zeros(0, dtype=np.int64),
            symbols=np.array([], dtype=object),
            seq_len=slen,
        )
    return SequenceDataset(
        X=np.stack(xs),
        y=np.asarray(ys, dtype=np.int8),
        timestamps_ms=np.asarray(ts_list, dtype=np.int64),
        symbols=np.asarray(sym_list, dtype=object),
        seq_len=slen,
    )


def load_sequence_dataset_from_bars_dir(
    bars_dir: str | Path,
    *,
    seq_len: int = 30,
    label_cfg: ScalpLabelConfig | None = None,
    btc_symbol: str = "BTCUSDT",
) -> SequenceDataset:
    root = Path(bars_dir)
    bars_by_symbol: dict[str, list[OhlcvBar]] = {}
    for path in sorted(root.glob("*_1m.jsonl")):
        sym = path.name.replace("_1m.jsonl", "").upper()
        bars = load_bars_jsonl(path)
        if bars:
            bars_by_symbol[sym] = bars
    if btc_symbol.upper() not in bars_by_symbol:
        p = root / f"{btc_symbol.upper()}_1m.jsonl"
        if p.is_file():
            bars_by_symbol[btc_symbol.upper()] = load_bars_jsonl(p)
    return build_sequence_dataset_from_bars(
        bars_by_symbol,
        label_cfg=label_cfg,
        seq_len=seq_len,
        btc_symbol=btc_symbol,
    )
