"""리포트용 DB 조회 함수 테스트."""

from __future__ import annotations

from deepsignal.backtest.backtest_engine import BacktestResult
from deepsignal.paper_trading.paper_trading_engine import (
    PaperAccountSnapshot,
    PaperTrade,
)
from deepsignal.scoring.signal_scorer import SignalResult
from deepsignal.storage.database import (
    fetch_latest_paper_snapshot,
    fetch_recent_backtests,
    fetch_recent_paper_trades,
    fetch_recent_signals,
    init_database,
    insert_backtest_result,
    insert_paper_account_snapshot,
    insert_paper_trade,
    insert_signal_result,
)


def test_fetch_recent_signals_order(tmp_path) -> None:
    db = str(tmp_path / "rq.db")
    init_database(db)
    insert_signal_result(
        db,
        SignalResult(
            symbol="A",
            signal_date="2024-01-01",
            technical_score=1.0,
            news_score=None,
            macro_score=None,
            final_score=1.0,
            action="HOLD",
            confidence=0.1,
            reason="r1",
            raw={},
        ),
    )
    insert_signal_result(
        db,
        SignalResult(
            symbol="B",
            signal_date="2024-01-02",
            technical_score=2.0,
            news_score=None,
            macro_score=None,
            final_score=2.0,
            action="HOLD",
            confidence=0.2,
            reason="r2",
            raw={},
        ),
    )
    rows = fetch_recent_signals(db, 20)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "B"
    assert "technical_score" in rows[0]
    assert "news_score" in rows[0]
    assert "macro_score" in rows[0]


def test_fetch_recent_backtests(tmp_path) -> None:
    db = str(tmp_path / "rb.db")
    init_database(db)
    br = BacktestResult(
        symbol="X",
        strategy_name="technical_v1",
        start_date="2024-01-01",
        end_date="2024-01-10",
        initial_cash=10000.0,
        final_value=10100.0,
        total_return_pct=1.0,
        trade_count=1,
        win_rate=100.0,
        max_drawdown_pct=-0.5,
        raw={},
    )
    insert_backtest_result(db, br)
    rows = fetch_recent_backtests(db, 5)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "X"


def test_fetch_paper_snapshot_and_trades(tmp_path) -> None:
    db = str(tmp_path / "rp.db")
    init_database(db)
    insert_paper_account_snapshot(
        db,
        PaperAccountSnapshot(
            snapshot_date="2026-01-01",
            cash=9000.0,
            equity=9500.0,
            positions_value=500.0,
            positions=[],
            last_action="HOLD",
            reason="s",
            raw={},
        ),
    )
    assert fetch_latest_paper_snapshot(db) is not None
    insert_paper_trade(
        db,
        PaperTrade(
            symbol="Z",
            trade_date="2026-01-01",
            side="BUY",
            price=10.0,
            quantity=1,
            cash_before=100.0,
            cash_after=90.0,
            reason="t",
            raw={},
        ),
    )
    tr = fetch_recent_paper_trades(db, 5)
    assert len(tr) == 1
    assert tr[0]["side"] == "BUY"
