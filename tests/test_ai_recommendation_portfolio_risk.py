from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.ai_recommendation.portfolio_risk_model import (
    PortfolioRiskConfig,
    build_portfolio_risk_result,
    load_sector_map,
    portfolio_risk_csv_rows,
)


def test_sector_map_loads_local_json(tmp_path: Path) -> None:
    path = tmp_path / "sector_map.json"
    path.write_text(json.dumps({"aapl": "Technology", "005930": "Semiconductor"}), encoding="utf-8")

    assert load_sector_map(str(path)) == {"AAPL": "Technology", "005930": "Semiconductor"}


def test_missing_sector_map_returns_unknown_sector() -> None:
    result = build_portfolio_risk_result(
        positions={"AAPL": 1},
        latest_prices={"AAPL": 100.0},
        prices_by_day={"2026-01-01": {"AAPL": 100.0}},
        config=PortfolioRiskConfig(sector_map_path=None),
    )

    assert result.sector_weights == {"UNKNOWN": 1.0}


def test_symbol_and_sector_overweight_detected(tmp_path: Path) -> None:
    path = tmp_path / "sector_map.json"
    path.write_text(json.dumps({"AAPL": "Technology", "MSFT": "Technology"}), encoding="utf-8")

    result = build_portfolio_risk_result(
        positions={"AAPL": 8, "MSFT": 2},
        latest_prices={"AAPL": 100.0, "MSFT": 100.0},
        prices_by_day={"2026-01-01": {"AAPL": 100.0, "MSFT": 100.0}},
        config=PortfolioRiskConfig(max_symbol_weight=0.35, max_sector_weight=0.50, sector_map_path=str(path), min_correlation_points=20),
    )

    assert result.symbol_weights["AAPL"] == 0.8
    assert result.sector_weights["Technology"] == 1.0
    assert result.overweight_symbols[0]["symbol"] == "AAPL"
    assert result.overweight_sectors[0]["sector"] == "Technology"
    assert result.concentration_score > 0


def test_high_correlation_pair_detected() -> None:
    prices_by_day = {}
    for i in range(25):
        day = f"2026-01-{i + 1:02d}"
        prices_by_day[day] = {"AAPL": 100 + i, "MSFT": 200 + i * 2, "NVDA": 300 - i}

    result = build_portfolio_risk_result(
        positions={"AAPL": 1, "MSFT": 1, "NVDA": 1},
        latest_prices={"AAPL": 124.0, "MSFT": 248.0, "NVDA": 276.0},
        prices_by_day=prices_by_day,
        config=PortfolioRiskConfig(high_correlation_threshold=0.80, min_correlation_points=20),
    )

    assert any(pair["symbol_a"] == "AAPL" and pair["symbol_b"] == "MSFT" for pair in result.high_correlation_pairs)


def test_correlation_data_shortage_warning() -> None:
    result = build_portfolio_risk_result(
        positions={"AAPL": 1, "MSFT": 1},
        latest_prices={"AAPL": 100.0, "MSFT": 100.0},
        prices_by_day={
            "2026-01-01": {"AAPL": 100.0, "MSFT": 100.0},
            "2026-01-02": {"AAPL": 101.0, "MSFT": 101.0},
        },
        config=PortfolioRiskConfig(min_correlation_points=20),
    )

    assert result.high_correlation_pairs == []
    assert any("insufficient points" in warning for warning in result.risk_warnings)


def test_portfolio_risk_csv_rows() -> None:
    result = build_portfolio_risk_result(
        positions={"AAPL": 1},
        latest_prices={"AAPL": 100.0},
        prices_by_day={"2026-01-01": {"AAPL": 100.0}},
        config=PortfolioRiskConfig(),
    )

    rows = portfolio_risk_csv_rows(result)

    assert {"category", "key", "value", "threshold", "severity", "note"}.issubset(rows[0])
    assert any(row["category"] == "score" for row in rows)
