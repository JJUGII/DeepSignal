"""TechnicalAnalyzer 단위 테스트."""

from __future__ import annotations

from datetime import date, timedelta

from deepsignal.analyzer.technical.technical_analyzer import TechnicalAnalyzer


def test_ema_length_matches_input() -> None:
    ta = TechnicalAnalyzer()
    vals = [float(i) for i in range(30)]
    ema = ta.calculate_ema(vals, 12)
    assert len(ema) == len(vals)


def test_rsi_length_matches_input() -> None:
    ta = TechnicalAnalyzer()
    vals = [float(i % 5 + 1) for i in range(40)]
    rsi = ta.calculate_rsi(vals, 14)
    assert len(rsi) == len(vals)


def test_rsi_none_until_enough_periods() -> None:
    ta = TechnicalAnalyzer()
    vals = [100.0 + i * 0.1 for i in range(10)]
    rsi = ta.calculate_rsi(vals, period=14)
    assert all(x is None for x in rsi)


def test_uptrend_positive_trend_score() -> None:
    ta = TechnicalAnalyzer()
    closes = [100.0 + i * 2.0 for i in range(60)]
    base = date(2024, 1, 1)
    rows = [
        {"bar_time": (base + timedelta(days=i)).isoformat(), "close": c}
        for i, c in enumerate(closes)
    ]
    out = ta.analyze_prices("TEST", rows)
    last = out[-1]
    assert last.trend_score is not None
    assert last.trend_score > 0


def test_none_close_does_not_raise() -> None:
    ta = TechnicalAnalyzer()
    rows = [
        {"bar_time": "2024-01-01", "close": 10.0},
        {"bar_time": "2024-01-02", "close": None},
        {"bar_time": "2024-01-03", "close": 11.0},
    ]
    out = ta.analyze_prices("NULLC", rows)
    assert len(out) == 3
    assert out[1].close is None
