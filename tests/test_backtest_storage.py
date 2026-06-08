"""backtest_results 저장 테스트."""

from __future__ import annotations

from deepsignal.backtest.backtest_engine import BacktestResult
from deepsignal.storage.database import init_database, insert_backtest_result


def _sample() -> BacktestResult:
    return BacktestResult(
        symbol="BT",
        strategy_name="technical_v1",
        start_date="2024-01-01",
        end_date="2024-01-31",
        initial_cash=10000.0,
        final_value=10100.0,
        total_return_pct=1.0,
        trade_count=0,
        win_rate=None,
        max_drawdown_pct=None,
        raw={"trades": [], "equity_curve": [], "parameters": {}},
    )


def test_duplicate_backtest_skipped(tmp_path) -> None:
    db = tmp_path / "bt.db"
    init_database(str(db))
    r = _sample()
    s1 = insert_backtest_result(str(db), r)
    assert s1["inserted"] == 1
    s2 = insert_backtest_result(str(db), r)
    assert s2["inserted"] == 0
    assert s2["skipped"] == 1
