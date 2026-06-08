"""economic_indicators 저장·조회 테스트."""

from __future__ import annotations

from pathlib import Path

from deepsignal.collector.economic.economic_collector import EconomicIndicator
from deepsignal.storage.database import (
    fetch_latest_economic_indicators,
    init_database,
    insert_economic_indicators,
)


def test_insert_and_fetch_latest(tmp_path: Path) -> None:
    db = tmp_path / "econ.db"
    init_database(str(db))
    rows = [
        EconomicIndicator("VIX", "2026-01-01", 20.0, "yfinance", {"a": 1}),
        EconomicIndicator("VIX", "2026-01-05", 18.0, "yfinance", {"a": 2}),
        EconomicIndicator("DXY", "2026-01-04", 103.0, "yfinance", {}),
    ]
    st = insert_economic_indicators(str(db), rows)
    assert st["inserted"] == 3
    latest = fetch_latest_economic_indicators(str(db))
    by = {d["indicator_name"].upper(): d for d in latest}
    assert by["VIX"]["indicator_date"] == "2026-01-05"
    assert float(by["VIX"]["value"]) == 18.0
    assert by["DXY"]["indicator_date"] == "2026-01-04"


def test_unique_duplicate_skipped(tmp_path: Path) -> None:
    db = tmp_path / "econ2.db"
    init_database(str(db))
    one = EconomicIndicator("TNX", "2026-02-01", 4.0, "yfinance", {})
    st1 = insert_economic_indicators(str(db), [one])
    st2 = insert_economic_indicators(str(db), [one])
    assert st1["inserted"] == 1
    assert st2["skipped"] == 1
