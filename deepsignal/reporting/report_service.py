"""DB 기반 리포트 문자열 생성."""

from __future__ import annotations

from deepsignal.reporting.console_formatter import (
    format_backtests_table,
    format_paper_report,
    format_signals_table,
)
from deepsignal.storage.database import (
    fetch_latest_paper_snapshot,
    fetch_recent_backtests,
    fetch_recent_paper_trades,
    fetch_recent_signals,
    get_paper_positions,
)


def render_signals_report(db_path: str) -> str:
    rows = fetch_recent_signals(db_path, 20)
    return "=== signals (최신 20) ===\n" + format_signals_table(rows)


def render_backtests_report(db_path: str) -> str:
    rows = fetch_recent_backtests(db_path, 20)
    return "=== backtest_results (최신 20) ===\n" + format_backtests_table(rows)


def render_paper_report(db_path: str) -> str:
    snap = fetch_latest_paper_snapshot(db_path)
    positions = get_paper_positions(db_path)
    trades = fetch_recent_paper_trades(db_path, 20)
    return format_paper_report(snap, positions, trades)
