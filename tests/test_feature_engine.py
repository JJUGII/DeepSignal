"""FeatureEngine offline tests."""

from __future__ import annotations

import numpy as np

from deepsignal.market_data.binance_stream.models import OhlcvBar, OrderBookSnapshot, TradeTick
from deepsignal.market_data.feature_engine import FEATURE_COUNT, FEATURE_NAMES, FeatureEngine
from deepsignal.market_data.feature_engine.math_utils import forward_fill_vector
from deepsignal.market_data.feature_engine.orderbook_features import orderbook_features


def test_forward_fill() -> None:
    cur = np.array([1.0, np.nan, 3.0])
    prev = np.array([0.5, 2.0, np.nan])
    out = forward_fill_vector(cur, prev)
    assert out[0] == 1.0
    assert out[1] == 2.0
    assert out[2] == 3.0


def test_orderbook_imbalance() -> None:
    book = OrderBookSnapshot(
        symbol="BTCUSDT",
        bids=[(100.0, 3.0), (99.0, 1.0)],
        asks=[(101.0, 1.0), (102.0, 1.0)],
        ts_ms=1,
    )
    feats = orderbook_features(book)
    assert feats["ob_imbalance"] > 0
    assert feats["ob_spread_frac"] > 0


def test_feature_vector_shape_and_ffill() -> None:
    eng = FeatureEngine(btc_symbol="BTCUSDT")
    base = 1_700_000_000_000
    for i in range(25):
        ts = base + i * 60_000
        px = 100.0 + i * 0.1
        eng.on_trade(
            TradeTick("ETHUSDT", px, 1.0, ts, is_buyer_maker=False)
        )
        eng.on_bar(
            OhlcvBar(
                "ETHUSDT",
                "1m",
                ts,
                px - 0.05,
                px + 0.05,
                px - 0.1,
                px,
                10.0,
                px * 10,
                5,
                closed=True,
            )
        )
    eng.on_orderbook(
        OrderBookSnapshot(
            "ETHUSDT",
            bids=[(px, 5.0)],
            asks=[(px + 0.1, 2.0)],
            ts_ms=base,
        )
    )
    v1 = eng.compute("ETHUSDT")
    assert v1.shape == (FEATURE_COUNT,)
    assert len(FEATURE_NAMES) == FEATURE_COUNT
    assert not np.all(np.isnan(v1))
    v2 = eng.compute("ETHUSDT")
    assert v2.shape == v1.shape


def test_alpha_vs_btc() -> None:
    eng = FeatureEngine(btc_symbol="BTCUSDT")
    t0 = 1_700_000_000_000
    for i in range(20):
        eng.on_bar(
            OhlcvBar(
                "BTCUSDT",
                "1m",
                t0 + i * 60_000,
                100,
                101,
                99,
                100 + i * 0.01,
                1,
                100,
                1,
                closed=True,
            )
        )
        eng.on_bar(
            OhlcvBar(
                "ETHUSDT",
                "1m",
                t0 + i * 60_000,
                50,
                51,
                49,
                50 + i * 0.05,
                1,
                50,
                1,
                closed=True,
            )
        )
    d = eng.feature_dict("ETHUSDT")
    assert "alpha_vs_btc_1m" in d
