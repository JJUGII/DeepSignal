from __future__ import annotations

import json
from pathlib import Path

import main as main_mod
from deepsignal.collector.market.market_data import MarketData
from deepsignal.scoring.signal_scorer import SignalResult
from deepsignal.storage.database import init_database, insert_market_prices, insert_signal_result


def _seed(path: Path) -> None:
    db = str(init_database(str(path)))
    for day, price in [("2026-01-01", 100.0), ("2026-01-02", 110.0), ("2026-01-03", 120.0)]:
        insert_market_prices(
            db,
            [
                MarketData(
                    symbol="AAPL",
                    trade_date=day,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    adjusted_close=price,
                    volume=1000,
                    source="yfinance",
                    raw={},
                )
            ],
        )
    insert_signal_result(
        db,
        SignalResult(
            symbol="AAPL",
            signal_date="2026-01-01",
            technical_score=80.0,
            news_score=None,
            macro_score=None,
            final_score=80.0,
            action="BUY_CANDIDATE",
            confidence=0.8,
            reason="buy",
            raw={},
        ),
    )


def test_validate_ai_recommendation_cli_writes_outputs(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "deep.db"
    out = tmp_path / "outputs"
    _seed(db)
    monkeypatch.setenv("DB_PATH", str(db))

    rc = main_mod.main(["validate-ai-recommendation", "--symbols", "AAPL", "--initial-cash", "1000", "--no-costs", "--output-dir", str(out)])

    assert rc == 0
    assert (out / "AI_RECOMMENDATION_VALIDATION.md").exists()
    assert (out / "AI_RECOMMENDATION_VALIDATION_TRADES.csv").exists()
    assert (out / "AI_RECOMMENDATION_PORTFOLIO_RISK.csv").exists()
    json_files = sorted(out.glob("ai_recommendation_validation_*.json"))
    assert json_files
    data = json.loads(json_files[-1].read_text(encoding="utf-8"))
    assert data["summary"]["symbols"] == ["AAPL"]
    assert data["metrics"]["trade_count"] >= 1
    assert "advanced_metrics" in data
    assert "benchmark" in data
    assert "fx_model" in data
    assert "liquidity_model" in data
    assert "portfolio_risk" in data
    assert data["cost_model"]["enabled"] is False


def test_validate_ai_recommendation_cli_include_sell_reduce(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "deep.db"
    out = tmp_path / "outputs"
    _seed(db)
    monkeypatch.setenv("DB_PATH", str(db))

    rc = main_mod.main(
        [
            "validate-ai-recommendation",
            "--symbols",
            "AAPL",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-01-03",
            "--initial-cash",
            "1000",
            "--include-sell-reduce",
            "--no-costs",
            "--benchmark",
            "--risk-free-rate",
            "0.0",
            "--output-dir",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(sorted(out.glob("ai_recommendation_validation_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["summary"]["include_sell_reduce"] is True
    assert data["summary"]["start_date"] == "2026-01-01"
    assert data["benchmark"]["available"] is True


def test_validate_ai_recommendation_cli_cost_options(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "deep.db"
    out = tmp_path / "outputs"
    _seed(db)
    monkeypatch.setenv("DB_PATH", str(db))

    rc = main_mod.main(
        [
            "validate-ai-recommendation",
            "--symbols",
            "AAPL",
            "--initial-cash",
            "1000",
            "--commission-rate",
            "0.01",
            "--tax-rate",
            "0.0",
            "--slippage-bps",
            "10",
            "--min-order-value",
            "1",
            "--max-order-value",
            "1000",
            "--currency",
            "KRW",
            "--output-dir",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(sorted(out.glob("ai_recommendation_validation_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["cost_model"]["enabled"] is True
    assert data["cost_summary"]["total_commission"] >= 0
    assert "cost_adjusted_metrics" in data


def test_validate_ai_recommendation_cli_portfolio_risk_options(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "deep.db"
    out = tmp_path / "outputs"
    sector_map = tmp_path / "sector_map.json"
    sector_map.write_text(json.dumps({"AAPL": "Technology"}), encoding="utf-8")
    _seed(db)
    monkeypatch.setenv("DB_PATH", str(db))

    rc = main_mod.main(
        [
            "validate-ai-recommendation",
            "--symbols",
            "AAPL",
            "--initial-cash",
            "1000",
            "--no-costs",
            "--sector-map",
            str(sector_map),
            "--max-symbol-weight",
            "0.35",
            "--max-sector-weight",
            "0.50",
            "--correlation-threshold",
            "0.80",
            "--correlation-lookback-days",
            "60",
            "--output-dir",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(sorted(out.glob("ai_recommendation_validation_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["portfolio_risk"]["config"]["sector_map_path"] == str(sector_map)
    assert data["portfolio_risk"]["config"]["max_symbol_weight"] == 0.35
    assert data["portfolio_risk"]["config"]["high_correlation_threshold"] == 0.80


def test_validate_ai_recommendation_cli_liquidity_options(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "deep.db"
    out = tmp_path / "outputs"
    _seed(db)
    monkeypatch.setenv("DB_PATH", str(db))

    rc = main_mod.main(
        [
            "validate-ai-recommendation",
            "--symbols",
            "AAPL",
            "--initial-cash",
            "1000",
            "--no-costs",
            "--liquidity-limit-pct",
            "0.01",
            "--min-daily-volume",
            "100",
            "--min-daily-value",
            "1000",
            "--volume-lookback-days",
            "20",
            "--output-dir",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(sorted(out.glob("ai_recommendation_validation_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["liquidity_model"]["config"]["liquidity_limit_pct"] == 0.01
    assert data["liquidity_model"]["config"]["volume_lookback_days"] == 20


def test_validate_ai_recommendation_cli_fx_options(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "deep.db"
    out = tmp_path / "outputs"
    _seed(db)
    monkeypatch.setenv("DB_PATH", str(db))

    rc = main_mod.main(
        [
            "validate-ai-recommendation",
            "--symbols",
            "AAPL",
            "--initial-cash",
            "1350000",
            "--no-costs",
            "--base-currency",
            "KRW",
            "--default-symbol-currency",
            "USD",
            "--fallback-fx",
            "USD=1350,KRW=1",
            "--output-dir",
            str(out),
        ]
    )

    assert rc == 0
    data = json.loads(sorted(out.glob("ai_recommendation_validation_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["fx_model"]["config"]["base_currency"] == "KRW"
    assert data["fx_model"]["config"]["default_symbol_currency"] == "USD"
    assert data["trades"][0]["symbol_currency"] == "USD"
    assert data["trades"][0]["fx_rate"] == 1350.0
