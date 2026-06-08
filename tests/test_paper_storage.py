"""paper_* 테이블 저장 테스트."""

from __future__ import annotations

import sqlite3

from deepsignal.paper_trading.paper_trading_engine import (
    PaperAccountSnapshot,
    PaperPosition,
    PaperTrade,
)
from deepsignal.storage.database import (
    clear_paper_position,
    get_paper_cash,
    get_paper_positions,
    init_database,
    insert_paper_account_snapshot,
    insert_paper_trade,
    upsert_paper_position,
)


def test_upsert_and_clear_position(tmp_path) -> None:
    db = str(tmp_path / "ps.db")
    init_database(db)
    upsert_paper_position(db, {"symbol": "Z", "quantity": 5, "avg_price": 10.0})
    rows = get_paper_positions(db)
    assert len(rows) == 1
    assert int(rows[0]["quantity"]) == 5
    upsert_paper_position(db, {"symbol": "Z", "quantity": 8, "avg_price": 11.0})
    rows = get_paper_positions(db)
    assert int(rows[0]["quantity"]) == 8
    clear_paper_position(db, "Z")
    assert get_paper_positions(db) == []


def test_cash_from_latest_snapshot(tmp_path) -> None:
    db = str(tmp_path / "pc.db")
    init_database(db)
    assert get_paper_cash(db, 10000.0) == 10000.0
    snap = PaperAccountSnapshot(
        snapshot_date="2026-01-10",
        cash=7500.0,
        equity=9000.0,
        positions_value=1500.0,
        positions=[
            PaperPosition(
                symbol="X",
                quantity=1,
                avg_price=100.0,
                last_price=150.0,
                market_value=150.0,
                unrealized_pnl=50.0,
                unrealized_pnl_pct=50.0,
            )
        ],
        last_action="HOLD",
        reason="t",
        raw={},
    )
    insert_paper_account_snapshot(db, snap)
    assert get_paper_cash(db, 10000.0) == 7500.0


def test_insert_paper_trade(tmp_path) -> None:
    db = str(tmp_path / "pt.db")
    init_database(db)
    insert_paper_trade(
        db,
        PaperTrade(
            symbol="T",
            trade_date="2026-01-01",
            side="BUY",
            price=10.0,
            quantity=2,
            cash_before=100.0,
            cash_after=80.0,
            reason="r",
            raw={"k": 1},
        ),
    )
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    assert int(n) == 1
