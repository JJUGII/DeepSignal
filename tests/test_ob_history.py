"""Order book jsonl recorder."""

from __future__ import annotations

from deepsignal.market_data.binance_stream.models import OrderBookSnapshot
from deepsignal.market_data.binance_stream.ob_history import OrderBookHistoryRecorder
from deepsignal.market_data.binance_stream.ob_resample import resample_ob_to_1m_buckets


def test_ob_recorder_interval(tmp_path) -> None:
    rec = OrderBookHistoryRecorder(tmp_path, interval_seconds=10.0)
    book = OrderBookSnapshot(
        symbol="BTCUSDT",
        bids=[(100.0, 1.0)],
        asks=[(101.0, 1.0)],
        ts_ms=1_000_000,
    )
    assert rec.maybe_record(book) is True
    assert rec.maybe_record(book, now_ms=1_000_500) is False
    assert rec.maybe_record(book, now_ms=1_011_000) is True
    assert rec.ob_path("BTCUSDT").is_file()


def test_ob_resample_1m(tmp_path) -> None:
    rows = []
    base = 1_700_000_000
    for i in range(6):
        rows.append(
            {
                "ts": base + i * 10,
                "ts_ms": (base + i * 10) * 1000,
                "bids": [[100.0, 2.0]],
                "asks": [[101.0, 1.0]],
            }
        )
    buckets = resample_ob_to_1m_buckets(rows)
    minute = (base // 60) * 60
    assert minute in buckets
    assert "ob_imbalance_1m_mean" in buckets[minute]
