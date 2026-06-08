"""Build ScalpDataset from crypto_trades closed round-trips."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from deepsignal.crypto_trading.crypto_trades import load_closed_trades
from deepsignal.market_data.feature_engine.spec import FEATURE_COUNT, FEATURE_NAMES
from deepsignal.ml.crypto_scalp_dataset import ScalpDataset
from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig


def _parse_features_snapshot(raw: str | None) -> np.ndarray | None:
    if not raw:
        return None
    try:
        vec = json.loads(raw)
        if not isinstance(vec, list) or len(vec) < 1:
            return None
        arr = np.asarray([float(x) for x in vec], dtype=np.float64)
        if arr.shape[0] == FEATURE_COUNT:
            return arr
        if arr.shape[0] > FEATURE_COUNT:
            return arr[:FEATURE_COUNT]
        padded = np.zeros(FEATURE_COUNT, dtype=np.float64)
        padded[: arr.shape[0]] = arr
        return padded
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _entry_ts_ms(iso_ts: str) -> int:
    from deepsignal.live_trading.time_utils import parse_datetime_with_default_tz

    try:
        return int(parse_datetime_with_default_tz(iso_ts).timestamp() * 1000)
    except Exception:
        return 0


def load_dataset_from_crypto_trades(
    trades_db: str | Path,
    *,
    lookback_days: int = 14,
    label_cfg: ScalpLabelConfig | None = None,
) -> ScalpDataset | None:
    """Label y=1 if actual_return > 0 (fee-inclusive decimal return)."""
    cfg = label_cfg or ScalpLabelConfig()
    rows = load_closed_trades(trades_db, lookback_days=lookback_days)
    if not rows:
        return None

    xs: list[np.ndarray] = []
    ys: list[int] = []
    ts_list: list[int] = []
    sym_list: list[str] = []
    rets: list[float] = []

    for row in rows:
        feat = _parse_features_snapshot(row.features_snapshot)
        if feat is None:
            continue
        ret = float(row.actual_return or 0.0)
        xs.append(feat)
        ys.append(1 if ret > 0.0 else 0)
        rets.append(ret)
        ts_list.append(_entry_ts_ms(row.entry_time))
        sym_list.append(row.symbol)

    if not xs:
        return None

    return ScalpDataset(
        X=np.stack(xs, axis=0),
        y=np.asarray(ys, dtype=np.int8),
        timestamps_ms=np.asarray(ts_list, dtype=np.int64),
        symbols=np.asarray(sym_list, dtype=object),
        feature_names=FEATURE_NAMES,
        returns=np.asarray(rets, dtype=np.float64),
    )
