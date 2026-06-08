"""콘솔 포맷터 단위 테스트."""

from __future__ import annotations

from deepsignal.reporting.console_formatter import (
    format_backtests_table,
    format_paper_report,
    format_signals_table,
)


def test_format_signals_empty() -> None:
    assert "없습니다" in format_signals_table([])


def test_format_signals_table_row() -> None:
    s = format_signals_table(
        [
            {
                "symbol": "AAA",
                "signal_date": "2024-06-01",
                "action": "HOLD",
                "technical_score": 10.0,
                "news_score": 25.5,
                "macro_score": None,
                "final_score": 14.65,
                "confidence": 0.25,
                "reason": "테스트 사유",
            }
        ]
    )
    assert "AAA" in s
    assert "technical_score" in s
    assert "news_score" in s
    assert "25.50" in s
    assert "|" in s


def test_format_backtests_empty() -> None:
    assert "없습니다" in format_backtests_table([])


def test_format_paper_no_snapshot() -> None:
    out = format_paper_report(None, [], [])
    assert "스냅샷" in out
    assert "포지션이 없습니다" in out
