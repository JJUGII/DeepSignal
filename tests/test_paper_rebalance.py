"""PaperTradingEngine.rebalance_portfolio 테스트."""

from __future__ import annotations

import sqlite3

from deepsignal.paper_trading.paper_trading_engine import (
    PaperRebalanceConfig,
    PaperTradingEngine,
)
from deepsignal.portfolio.portfolio_models import PortfolioSnapshot
from deepsignal.storage.database import get_paper_cash, get_paper_positions, init_database


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


def test_rebalance_buy_from_cash(tmp_path) -> None:
    db = tmp_path / "p1.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        _insert_bar(conn, "AAPL", 100.0, "2026-05-12")
        conn.commit()

    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=10_000.0,
        market_regime="neutral",
        allocations=[],
        raw={
            "allocations_for_paper": [
                {
                    "symbol": "AAPL",
                    "target_amount": 5000.0,
                    "target_weight": 0.5,
                    "rationale": "test",
                }
            ]
        },
    )
    eng = PaperTradingEngine()
    out = eng.rebalance_portfolio(
        str(db),
        snap,
        liquidate_missing=True,
        rebalance_config=PaperRebalanceConfig.legacy_no_costs(),
    )
    assert out is not None
    pos = get_paper_positions(str(db))
    assert len(pos) == 1
    assert pos[0]["symbol"] == "AAPL"
    assert int(pos[0]["quantity"]) == 50


def test_liquidate_missing_sells_extra(tmp_path) -> None:
    db = tmp_path / "p2.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        _insert_bar(conn, "AAPL", 50.0, "2026-05-12")
        _insert_bar(conn, "MSFT", 200.0, "2026-05-12")
        conn.execute(
            "INSERT INTO paper_positions (symbol, quantity, avg_price) VALUES ('MSFT', 2, 200)"
        )
        conn.commit()

    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=10_000.0,
        market_regime="neutral",
        allocations=[],
        raw={
            "allocations_for_paper": [
                {
                    "symbol": "AAPL",
                    "target_amount": 2500.0,
                    "target_weight": 0.25,
                    "rationale": "test",
                }
            ]
        },
    )
    eng = PaperTradingEngine()
    eng.rebalance_portfolio(
        str(db),
        snap,
        liquidate_missing=True,
        rebalance_config=PaperRebalanceConfig.legacy_no_costs(),
    )
    syms = {p["symbol"] for p in get_paper_positions(str(db))}
    assert "MSFT" not in syms
    assert "AAPL" in syms


def test_buy_capped_by_cash(tmp_path) -> None:
    db = tmp_path / "p3.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        _insert_bar(conn, "AAPL", 100.0, "2026-05-12")
        conn.execute(
            "INSERT INTO paper_account_snapshots (snapshot_date, cash, equity, "
            "positions_value, last_action, reason, raw_json) "
            "VALUES ('2026-05-11', 150.0, 150.0, 0.0, 'HOLD', '', '{}')"
        )
        conn.commit()

    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=150.0,
        market_regime="neutral",
        allocations=[],
        raw={
            "allocations_for_paper": [
                {
                    "symbol": "AAPL",
                    "target_amount": 10_000.0,
                    "target_weight": 0.9,
                    "rationale": "test",
                }
            ]
        },
    )
    eng = PaperTradingEngine()
    eng.rebalance_portfolio(
        str(db),
        snap,
        liquidate_missing=True,
        rebalance_config=PaperRebalanceConfig.legacy_no_costs(),
    )
    pos = get_paper_positions(str(db))
    assert int(pos[0]["quantity"]) == 1
    cash = get_paper_cash(str(db), 10000.0)
    assert cash < 150.0


def test_skip_symbol_without_price(tmp_path) -> None:
    db = tmp_path / "p4.db"
    init_database(str(db))
    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=1000.0,
        market_regime="neutral",
        allocations=[],
        raw={
            "allocations_for_paper": [
                {
                    "symbol": "NOPE",
                    "target_amount": 500.0,
                    "target_weight": 0.5,
                    "rationale": "test",
                }
            ]
        },
    )
    eng = PaperTradingEngine()
    eng.rebalance_portfolio(
        str(db),
        snap,
        liquidate_missing=True,
        rebalance_config=PaperRebalanceConfig.legacy_no_costs(),
    )
    assert get_paper_positions(str(db)) == []


def test_equity_matches_positions_and_cash(tmp_path) -> None:
    db = tmp_path / "p5.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        _insert_bar(conn, "AAPL", 10.0, "2026-05-12")
        conn.commit()

    snap = PortfolioSnapshot(
        analyzed_at="2026-05-12T12:00:00+00:00",
        total_cash=1000.0,
        market_regime="neutral",
        allocations=[],
        raw={
            "allocations_for_paper": [
                {
                    "symbol": "AAPL",
                    "target_amount": 300.0,
                    "target_weight": 0.3,
                    "rationale": "test",
                }
            ]
        },
    )
    eng = PaperTradingEngine()
    out = eng.rebalance_portfolio(
        str(db),
        snap,
        liquidate_missing=True,
        rebalance_config=PaperRebalanceConfig.legacy_no_costs(),
    )
    assert out is not None
    qty = int(get_paper_positions(str(db))[0]["quantity"])
    assert abs(out.equity - (out.cash + qty * 10.0)) < 1e-6
