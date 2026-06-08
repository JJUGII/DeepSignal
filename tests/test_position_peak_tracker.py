"""position_price_peaks 자동 추적."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepsignal.analysis.position_peak_tracker import (
    load_peak_price,
    update_position_peaks,
)
from deepsignal.storage.database import init_database


def test_peak_price_tracks_high_water_mark(tmp_path: Path) -> None:
    db = str(init_database(tmp_path / "t.db"))
    positions = [
        {
            "symbol": "005930",
            "quantity": 1,
            "avg_price": 280_000.0,
            "current_price": 290_000.0,
            "market_value": 290_000.0,
            "raw": {},
        }
    ]
    peaks = update_position_peaks(db, "kis", positions)
    assert peaks["005930"] == 290_000.0
    assert positions[0]["raw"]["peak_price"] == 290_000.0

    positions[0]["current_price"] = 270_000.0
    peaks2 = update_position_peaks(db, "kis", positions)
    assert peaks2["005930"] == 290_000.0
    assert load_peak_price(db, broker="kis", symbol="005930") == 290_000.0
