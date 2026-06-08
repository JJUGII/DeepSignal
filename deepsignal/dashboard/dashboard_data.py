"""대시보드용 DB 읽기 전용 데이터 로더 (GUI와 분리)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deepsignal.storage.database import (
    fetch_latest_paper_snapshot,
    fetch_recent_backtests,
    fetch_recent_paper_trades,
    fetch_recent_signals,
    get_paper_positions,
)


@dataclass(frozen=True)
class DashboardData:
    """대시보드에 표시할 스냅샷."""

    signals: list[dict[str, Any]]
    backtests: list[dict[str, Any]]
    paper_snapshot: dict[str, Any] | None
    paper_positions: list[dict[str, Any]]
    paper_trades: list[dict[str, Any]]


def load_dashboard_data(db_path: str) -> DashboardData:
    """
    DB에서 signals / backtests / paper 관련 행을 읽어온다.

    쓰기·수집·점수화·체결 트리거는 수행하지 않는다.
    """
    return DashboardData(
        signals=fetch_recent_signals(db_path, 20),
        backtests=fetch_recent_backtests(db_path, 20),
        paper_snapshot=fetch_latest_paper_snapshot(db_path),
        paper_positions=get_paper_positions(db_path),
        paper_trades=fetch_recent_paper_trades(db_path, 20),
    )
