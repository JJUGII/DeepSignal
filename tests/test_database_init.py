"""SQLite 초기화 테스트."""

from __future__ import annotations

from pathlib import Path

from deepsignal.storage.database import init_database, list_user_tables

_EXPECTED = frozenset(
    {
        "news_items",
        "market_prices",
        "economic_indicators",
        "signals",
        "trades",
        "backtest_results",
        "paper_positions",
        "paper_trades",
        "paper_account_snapshots",
        "collection_runs",
    }
)


def test_init_database_creates_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "deepsignal_test.db"
    returned = init_database(str(db_file))
    assert returned == db_file.resolve()
    tables = list_user_tables(str(db_file))
    assert _EXPECTED <= tables


def test_init_database_idempotent(tmp_path: Path) -> None:
    db_file = tmp_path / "deepsignal_idem.db"
    init_database(str(db_file))
    init_database(str(db_file))
    tables = list_user_tables(str(db_file))
    assert _EXPECTED <= tables
