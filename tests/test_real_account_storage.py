"""[실전-6] real_* DB 저장·조회."""

from __future__ import annotations

import json

from deepsignal.storage.database import (
    init_database,
    load_latest_real_account_snapshot,
    load_latest_real_positions,
    save_real_account_snapshot,
    save_real_positions,
)


def test_save_and_load_real_positions(tmp_path) -> None:
    db = str(tmp_path / "t.db")
    init_database(db)
    ts = "2026-05-15T12:00:00"
    rows = [
        {
            "symbol": "005930",
            "quantity": 10,
            "avg_price": 70000.0,
            "current_price": 71000.0,
            "market_value": 710000.0,
            "raw": {"pdno": "005930"},
        }
    ]
    n = save_real_positions(db, ts, "kis", rows)
    assert n == 1
    loaded = load_latest_real_positions(db, broker="kis")
    assert len(loaded) == 1
    assert loaded[0]["symbol"] == "005930"
    assert loaded[0]["quantity"] == 10
    assert loaded[0]["raw"]["pdno"] == "005930"


def test_save_and_load_account_snapshot(tmp_path) -> None:
    db = str(tmp_path / "t2.db")
    init_database(db)
    ts = "2026-05-15T12:01:00"
    payload = {"timestamp": ts, "kis_env": "paper", "cash": {}, "positions": []}
    save_real_account_snapshot(
        db,
        ts,
        "kis",
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
        total_market_value=100.0,
        total_equity=1_000_100.0,
        raw_payload=payload,
    )
    snap = load_latest_real_account_snapshot(db, broker="kis")
    assert snap is not None
    assert snap["cash"] == 1_000_000.0
    assert snap["raw"]["kis_env"] == "paper"


def test_latest_empty_account_snapshot_returns_no_positions(tmp_path) -> None:
    db = str(tmp_path / "t3.db")
    init_database(db)
    old_ts = "2026-05-15T12:00:00"
    new_ts = "2026-05-15T12:01:00"
    save_real_positions(
        db,
        old_ts,
        "kis",
        [{"symbol": "005930", "quantity": 1, "raw": {"pdno": "005930"}}],
    )
    save_real_account_snapshot(
        db,
        new_ts,
        "kis",
        cash=500_000.0,
        withdrawable_cash=500_000.0,
        total_market_value=None,
        total_equity=500_000.0,
        raw_payload={"timestamp": new_ts, "positions": []},
    )

    assert load_latest_real_positions(db, broker="kis") == []
