"""Crypto scalp label + LightGBM training (synthetic bars)."""

from __future__ import annotations

import pytest

from deepsignal.market_data.binance_stream.models import OhlcvBar
from deepsignal.ml.crypto_scalp_dataset import build_dataset_from_bars
from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig
from deepsignal.ml.crypto_scalp_lgbm import LgbmTrainConfig, train_lgbm_classifier


def _synthetic_bars(symbol: str, n: int, *, drift: float = 0.001) -> list[OhlcvBar]:
    bars: list[OhlcvBar] = []
    px = 100.0
    t0 = 1_700_000_000_000
    for i in range(n):
        px *= 1.0 + drift
        bars.append(
            OhlcvBar(
                symbol=symbol,
                timeframe="1m",
                open_ts_ms=t0 + i * 60_000,
                open=px * 0.999,
                high=px * 1.001,
                low=px * 0.998,
                close=px,
                volume=10.0,
                quote_volume=px * 10,
                trade_count=5,
                closed=True,
            )
        )
    return bars


def test_label_hurdle() -> None:
    cfg = ScalpLabelConfig(horizon_minutes=5, cost_pct=0.2)
    assert cfg.label_from_prices(100.0, 100.25) == 1
    assert cfg.label_from_prices(100.0, 100.1) == 0


def test_build_dataset() -> None:
    btc = _synthetic_bars("BTCUSDT", 150, drift=0.0005)
    eth = _synthetic_bars("ETHUSDT", 150, drift=0.001)
    ds = build_dataset_from_bars(
        {"BTCUSDT": btc, "ETHUSDT": eth},
        label_cfg=ScalpLabelConfig(horizon_minutes=5, cost_pct=0.2),
    )
    assert ds.n_samples > 50
    assert ds.X.shape[1] == len(ds.feature_names)


def _lightgbm_available() -> bool:
    if __import__("importlib").util.find_spec("lightgbm") is None:
        return False
    try:
        import lightgbm  # noqa: F401
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _lightgbm_available(), reason="lightgbm not installed or libomp missing")
def test_train_lgbm_smoke(tmp_path) -> None:
    btc = _synthetic_bars("BTCUSDT", 200, drift=0.0008)
    eth = _synthetic_bars("ETHUSDT", 200, drift=0.0012)
    ds = build_dataset_from_bars(
        {"BTCUSDT": btc, "ETHUSDT": eth},
        label_cfg=ScalpLabelConfig(horizon_minutes=5, cost_pct=0.15),
    )
    cfg = LgbmTrainConfig(horizon_minutes=5, cost_pct=0.15, n_splits=3, min_train_samples=80)
    model, report = train_lgbm_classifier(ds, train_cfg=cfg, model_dir=tmp_path)
    assert model is not None
    assert report.feature_importance
    assert (tmp_path / "crypto_scalp_lgbm_5m.txt").is_file()
