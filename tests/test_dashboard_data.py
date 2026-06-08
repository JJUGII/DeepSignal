"""대시보드 데이터 로더 테스트."""

from __future__ import annotations

from deepsignal.backtest.backtest_engine import BacktestResult
from deepsignal.dashboard.dashboard_data import load_dashboard_data
from deepsignal.paper_trading.paper_trading_engine import (
    PaperAccountSnapshot,
    PaperTrade,
)
from deepsignal.scoring.signal_scorer import SignalResult
from deepsignal.storage.database import (
    init_database,
    insert_backtest_result,
    insert_paper_account_snapshot,
    insert_paper_trade,
    insert_signal_result,
    upsert_paper_position,
)


def test_load_dashboard_data_populated(tmp_path) -> None:
    db = str(tmp_path / "dash.db")
    init_database(db)
    insert_signal_result(
        db,
        SignalResult(
            symbol="D1",
            signal_date="2024-03-01",
            technical_score=10.0,
            news_score=50.0,
            macro_score=None,
            final_score=22.0,
            action="HOLD",
            confidence=0.1,
            reason="r",
            raw={},
        ),
    )
    insert_backtest_result(
        db,
        BacktestResult(
            symbol="D1",
            strategy_name="technical_v1",
            start_date="2024-01-01",
            end_date="2024-01-05",
            initial_cash=10000.0,
            final_value=10050.0,
            total_return_pct=0.5,
            trade_count=0,
            win_rate=None,
            max_drawdown_pct=None,
            raw={},
        ),
    )
    insert_paper_account_snapshot(
        db,
        PaperAccountSnapshot(
            snapshot_date="2024-03-02",
            cash=9000.0,
            equity=9500.0,
            positions_value=500.0,
            positions=[],
            last_action="HOLD",
            reason="s",
            raw={},
        ),
    )
    upsert_paper_position(db, {"symbol": "D1", "quantity": 1, "avg_price": 100.0})
    insert_paper_trade(
        db,
        PaperTrade(
            symbol="D1",
            trade_date="2024-03-02",
            side="BUY",
            price=100.0,
            quantity=1,
            cash_before=10000.0,
            cash_after=9900.0,
            reason="t",
            raw={},
        ),
    )

    d = load_dashboard_data(db)
    assert len(d.signals) == 1
    assert d.signals[0]["symbol"] == "D1"
    assert float(d.signals[0]["technical_score"]) == 10.0
    assert float(d.signals[0]["news_score"]) == 50.0
    assert float(d.signals[0]["final_score"]) == 22.0
    assert len(d.backtests) == 1
    assert d.paper_snapshot is not None
    assert float(d.paper_snapshot["cash"]) == 9000.0
    assert len(d.paper_positions) == 1
    assert len(d.paper_trades) == 1


def test_load_dashboard_data_empty(tmp_path) -> None:
    db = str(tmp_path / "dash2.db")
    init_database(db)
    d = load_dashboard_data(db)
    assert d.signals == []
    assert d.backtests == []
    assert d.paper_snapshot is None
    assert d.paper_positions == []
    assert d.paper_trades == []
