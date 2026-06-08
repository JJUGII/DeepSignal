"""IPS 5% 집중도 검사."""

from __future__ import annotations

from deepsignal.analysis.portfolio_concentration import check_position_concentration


def test_concentration_warning_over_5pct() -> None:
    positions = [
        {"symbol": "005930", "quantity": 1, "market_value": 60_000.0},
        {"symbol": "000660", "quantity": 1, "market_value": 40_000.0},
    ]
    r = check_position_concentration(positions, total_equity=1_000_000.0, cap_fraction=0.05)
    assert r.status == "WARNING"
    assert any("005930" in w for w in r.warnings)


def test_concentration_ok_under_cap() -> None:
    positions = [{"symbol": "005930", "quantity": 1, "market_value": 30_000.0}]
    r = check_position_concentration(positions, total_equity=1_000_000.0, cap_fraction=0.05)
    assert r.status == "OK"
