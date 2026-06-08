"""paper-rebalance 거래비용·최소 거래 규칙."""

from __future__ import annotations

import json
import sqlite3

import pytest

from deepsignal.paper_trading.paper_trading_engine import (
    PaperRebalanceConfig,
    PaperTradingEngine,
)
from deepsignal.portfolio.portfolio_models import PortfolioSnapshot
from deepsignal.storage.database import get_paper_positions, init_database


def _insert_bar(conn: sqlite3.Connection, sym: str, close: float, bar_time: str) -> None:
    conn.execute(
        """
        INSERT INTO market_prices (
            symbol, timeframe, bar_time, source, open, high, low, close,
            adjusted_close, volume, raw_json
        ) VALUES (?, '1d', ?, 'yfinance', ?, ?, ?, ?, ?, 100, '{}')
        """,
        (sym, bar_time, close, close, close, close, close),
    )


def _last_trade(db: str) -> tuple[float, int, float, float, dict]:
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT price, quantity, cash_before, cash_after, raw_json "
            "FROM paper_trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    raw = json.loads(row[4])
    return float(row[0]), int(row[1]), float(row[2]), float(row[3]), raw


def test_buy_slippage_and_commission(tmp_path) -> None:
    db = tmp_path / "c1.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        _insert_bar(conn, "AAPL", 100.0, "2026-05-12")
        conn.commit()

    cfg = PaperRebalanceConfig(
        commission_rate=0.01,
        slippage_rate=0.01,
        min_trade_value=0.0,
        rebalance_threshold=0.0,
    )
    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=20_000.0,
        market_regime="neutral",
        allocations=[],
        raw={
            "allocations_for_paper": [
                {
                    "symbol": "AAPL",
                    "target_amount": 5000.0,
                    "target_weight": 0.25,
                    "rationale": "test",
                }
            ]
        },
    )
    PaperTradingEngine().rebalance_portfolio(
        str(db), snap, liquidate_missing=True, rebalance_config=cfg
    )
    px, qty, cb, ca, raw = _last_trade(str(db))
    assert qty == 50
    assert raw["market_price"] == 100.0
    assert raw["executed_price"] == pytest.approx(101.0)
    assert px == pytest.approx(101.0)
    gross = 50 * 101.0
    comm = gross * 0.01
    assert cb - ca == pytest.approx(gross + comm)


def test_sell_slippage_and_commission(tmp_path) -> None:
    db = tmp_path / "c2.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        _insert_bar(conn, "AAPL", 100.0, "2026-05-12")
        conn.execute(
            "INSERT INTO paper_positions (symbol, quantity, avg_price) VALUES ('AAPL', 1, 50)"
        )
        conn.commit()

    cfg = PaperRebalanceConfig(
        commission_rate=0.01,
        slippage_rate=0.01,
        min_trade_value=0.0,
        rebalance_threshold=0.0,
    )
    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=0.0,
        market_regime="neutral",
        allocations=[],
        raw={"allocations_for_paper": []},
    )
    PaperTradingEngine().rebalance_portfolio(
        str(db), snap, liquidate_missing=True, rebalance_config=cfg
    )
    px, qty, cb, ca, raw = _last_trade(str(db))
    assert qty == 1
    assert raw["market_price"] == 100.0
    assert raw["executed_price"] == pytest.approx(99.0)
    assert px == pytest.approx(99.0)
    gross = 99.0
    comm = gross * 0.01
    assert raw["commission"] == pytest.approx(comm)
    assert ca - cb == pytest.approx(gross - comm)


def test_buy_capped_by_cash_includes_commission(tmp_path) -> None:
    db = tmp_path / "c3.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        _insert_bar(conn, "AAPL", 100.0, "2026-05-12")
        conn.execute(
            "INSERT INTO paper_account_snapshots (snapshot_date, cash, equity, "
            "positions_value, last_action, reason, raw_json) "
            "VALUES ('2026-05-11', 250.0, 250.0, 0.0, 'HOLD', '', '{}')"
        )
        conn.commit()

    cfg = PaperRebalanceConfig(
        commission_rate=0.01,
        slippage_rate=0.0,
        min_trade_value=0.0,
        rebalance_threshold=0.0,
    )
    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=250.0,
        market_regime="neutral",
        allocations=[],
        raw={
            "allocations_for_paper": [
                {
                    "symbol": "AAPL",
                    "target_amount": 10_000.0,
                    "target_weight": 1.0,
                    "rationale": "test",
                }
            ]
        },
    )
    PaperTradingEngine().rebalance_portfolio(
        str(db), snap, liquidate_missing=True, rebalance_config=cfg
    )
    pos = get_paper_positions(str(db))
    assert int(pos[0]["quantity"]) == 2
    unit_all_in = 100.0 * (1.0 + cfg.commission_rate)
    assert int(250.0 // unit_all_in) == 2


def test_min_trade_value_skips(tmp_path) -> None:
    db = tmp_path / "c4.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        _insert_bar(conn, "AAPL", 10.0, "2026-05-12")
        conn.execute(
            "INSERT INTO paper_positions (symbol, quantity, avg_price) VALUES ('AAPL', 100, 10)"
        )
        conn.commit()

    cfg = PaperRebalanceConfig(
        commission_rate=0.0,
        slippage_rate=0.0,
        min_trade_value=20.0,
        rebalance_threshold=0.0,
    )
    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=0.0,
        market_regime="neutral",
        allocations=[],
        raw={
            "allocations_for_paper": [
                {
                    "symbol": "AAPL",
                    "target_amount": 1015.0,
                    "target_weight": 0.1,
                    "rationale": "test",
                }
            ]
        },
    )
    PaperTradingEngine().rebalance_portfolio(
        str(db), snap, liquidate_missing=True, rebalance_config=cfg
    )
    with sqlite3.connect(str(db)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    assert int(n) == 0
    assert len(get_paper_positions(str(db))) == 1


def test_rebalance_threshold_skips(tmp_path) -> None:
    db = tmp_path / "c5.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        _insert_bar(conn, "AAPL", 100.0, "2026-05-12")
        conn.execute(
            "INSERT INTO paper_positions (symbol, quantity, avg_price) VALUES ('AAPL', 100, 100)"
        )
        conn.commit()

    cfg = PaperRebalanceConfig(
        commission_rate=0.0,
        slippage_rate=0.0,
        min_trade_value=0.0,
        rebalance_threshold=0.01,
    )
    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=0.0,
        market_regime="neutral",
        allocations=[],
        raw={
            "allocations_for_paper": [
                {
                    "symbol": "AAPL",
                    "target_amount": 9920.0,
                    "target_weight": 0.99,
                    "rationale": "test",
                }
            ]
        },
    )
    PaperTradingEngine().rebalance_portfolio(
        str(db), snap, liquidate_missing=True, rebalance_config=cfg
    )
    with sqlite3.connect(str(db)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    assert int(n) == 0

