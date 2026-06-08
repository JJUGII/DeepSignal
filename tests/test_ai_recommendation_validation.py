from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from deepsignal.collector.market.market_data import MarketData
from deepsignal.live_trading.ai_recommendation.cost_model import CostModel
from deepsignal.live_trading.ai_recommendation.fx_model import FXConfig
from deepsignal.live_trading.ai_recommendation.liquidity_model import LiquidityConfig
from deepsignal.live_trading.ai_recommendation.portfolio_risk_model import PortfolioRiskConfig
from deepsignal.live_trading.ai_recommendation.validation_engine import (
    run_ai_recommendation_validation,
    run_validation,
)
from deepsignal.live_trading.ai_recommendation.validation_model import ValidationConfig
from deepsignal.scoring.signal_scorer import SignalResult
from deepsignal.storage.database import init_database, insert_market_prices, insert_signal_result, list_user_tables


def _db(tmp_path: Path) -> str:
    path = tmp_path / "validation.db"
    init_database(str(path))
    return str(path)


def _price(db: str, symbol: str, day: str, close: float) -> None:
    insert_market_prices(
        db,
        [
            MarketData(
                symbol=symbol,
                trade_date=day,
                open=close,
                high=close,
                low=close,
                close=close,
                adjusted_close=close,
                volume=1000,
                source="yfinance",
                raw={},
            )
        ],
    )


def _signal(db: str, symbol: str, day: str, score: float, action: str) -> None:
    insert_signal_result(
        db,
        SignalResult(
            symbol=symbol,
            signal_date=day,
            technical_score=score,
            news_score=None,
            macro_score=None,
            final_score=score,
            action=action,
            confidence=0.8,
            reason=f"{action} {score}",
            raw={},
        ),
    )


def _seed(db: str) -> None:
    for day, price in [("2026-01-01", 100.0), ("2026-01-02", 110.0), ("2026-01-03", 120.0)]:
        _price(db, "AAPL", day, price)
    _signal(db, "AAPL", "2026-01-01", 80.0, "BUY_CANDIDATE")
    _signal(db, "AAPL", "2026-01-02", 70.0, "BUY_CANDIDATE")
    _signal(db, "AAPL", "2026-01-03", -80.0, "SELL_CANDIDATE")


def _cfg(**kwargs) -> ValidationConfig:
    base = {"costs_enabled": False, "cost_model": CostModel.no_costs(), "initial_cash": 1000.0}
    base.update(kwargs)
    return ValidationConfig(**base)


def test_validation_summary_and_equity_curve(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    result = run_validation(db, config=_cfg(symbols=["AAPL"]))

    assert result.summary["start_date"] == "2026-01-01"
    assert result.summary["end_date"] == "2026-01-03"
    assert result.metrics["trade_count"] >= 1
    assert len(result.equity_curve) == 3
    assert result.equity_curve[-1].equity > 0


def test_buy_increase_reflected(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    result = run_validation(db, config=_cfg(symbols=["AAPL"]))
    actions = [trade.action for trade in result.trades]

    assert "BUY" in actions
    assert "INCREASE" in actions


def test_include_sell_reduce_option(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    no_sell = run_validation(db, config=_cfg(symbols=["AAPL"], include_sell_reduce=False))
    with_sell = run_validation(db, config=_cfg(symbols=["AAPL"], include_sell_reduce=True))

    assert not any(trade.action == "SELL" for trade in no_sell.trades)
    assert any(trade.action == "SELL" for trade in with_sell.trades)


def test_period_filter(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    result = run_validation(
        db,
        config=_cfg(symbols=["AAPL"], start_date="2026-01-02", end_date="2026-01-03"),
    )

    assert [point.date for point in result.equity_curve] == ["2026-01-02", "2026-01-03"]


def test_empty_data_graceful(tmp_path: Path) -> None:
    db = _db(tmp_path)

    result = run_validation(db, config=_cfg(symbols=["NONE"]))

    assert result.metrics["trade_count"] == 0
    assert result.equity_curve == []
    assert result.warnings


def test_outputs_json_markdown_csv(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)
    out = tmp_path / "outputs"

    result, json_path, md_path, csv_path, risk_csv_path = run_ai_recommendation_validation(
        db,
        config=_cfg(symbols=["AAPL"], include_sell_reduce=True, output_dir=str(out)),
    )

    assert json_path.exists()
    assert md_path.exists()
    assert csv_path.exists()
    assert risk_csv_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert "summary" in data
    assert "metrics" in data
    assert "advanced_metrics" in data
    assert "benchmark" in data
    assert "cost_model" in data
    assert "cost_summary" in data
    assert "skipped_orders" in data
    assert "cost_adjusted_metrics" in data
    assert "fx_model" in data
    assert "liquidity_model" in data
    assert "portfolio_risk" in data
    assert "trades" in data
    assert "equity_curve" in data
    assert "action_breakdown" in data
    assert "symbol_breakdown" in data
    assert "validation_warnings" in data
    assert "daily_return_pct" in data["equity_curve"][0]
    assert "drawdown_pct" in data["equity_curve"][0]
    assert "exposure_pct" in data["equity_curve"][0]
    assert data["benchmark"]["available"] is True
    md = md_path.read_text(encoding="utf-8")
    assert "AI Recommendation Validation" in md
    assert "고급 성과 지표" in md
    assert "벤치마크 비교" in md
    assert "최대 낙폭 구간" in md
    assert "연속 손실 위험" in md
    assert "거래 비용 가정" in md
    assert "비용 반영 성과" in md
    assert "통화 / 환율 검증" in md
    assert "유동성 제한 검증" in md
    assert "포트폴리오 리스크 검증" in md
    assert "실계좌 주문과 무관" in md
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    assert rows
    assert {"date", "symbol", "action", "quantity", "price", "value", "cash_after", "equity_after", "reason"}.issubset(rows[0])
    assert {"realized_pnl", "holding_days", "action_group"}.issubset(rows[0])
    assert {"raw_price", "adjusted_price", "commission", "tax", "slippage_cost", "total_cost", "skip_reason"}.issubset(rows[0])
    assert {"liquidity_requested_quantity", "liquidity_allowed_quantity", "liquidity_adjusted_quantity", "liquidity_skip_reason", "liquidity_warning"}.issubset(rows[0])
    assert {"symbol_currency", "base_currency", "fx_rate", "value_base_currency", "cost_base_currency"}.issubset(rows[0])
    risk_rows = list(csv.DictReader(risk_csv_path.open(encoding="utf-8-sig")))
    assert risk_rows
    assert {"category", "key", "value", "threshold", "severity", "note"}.issubset(risk_rows[0])
    assert result.output_files["trades_csv"] == "AI_RECOMMENDATION_VALIDATION_TRADES.csv"
    assert result.output_files["portfolio_risk_csv"] == "AI_RECOMMENDATION_PORTFOLIO_RISK.csv"
    assert "equity_base_currency" in data["equity_curve"][0]
    assert "cash_by_currency" in data["equity_curve"][0]
    assert "position_value_by_currency" in data["equity_curve"][0]
    assert "fx_rates_used" in data["equity_curve"][0]


def test_advanced_metrics_values_and_benchmark(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    result = run_validation(
        db,
        config=_cfg(symbols=["AAPL"], include_sell_reduce=True, risk_free_rate=0.0),
    )

    adv = result.advanced_metrics
    assert "total_return_pct" in adv
    assert "annualized_return_pct" in adv
    assert "volatility_pct" in adv
    assert "sharpe_ratio" in adv
    assert adv["sharpe_ratio"] is None or isinstance(adv["sharpe_ratio"], float)
    assert adv["max_drawdown_pct"] <= 0
    assert "max_drawdown_start" in adv
    assert "max_drawdown_end" in adv
    assert "profit_factor" in adv
    assert "expectancy" in adv
    assert "consecutive_losses_max" in adv
    assert "trade_count_by_action" in adv
    assert "pnl_by_action" in adv
    assert "pnl_by_symbol" in adv
    assert result.benchmark["available"] is True
    assert "benchmark_final_equity" in result.benchmark
    assert "benchmark_return_pct" in result.benchmark
    assert "excess_return_pct" in result.benchmark
    assert "strategy_vs_benchmark" in result.benchmark


def test_portfolio_risk_json_with_sector_map(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)
    sector_map = tmp_path / "sector_map.json"
    sector_map.write_text(json.dumps({"AAPL": "Technology"}), encoding="utf-8")

    result = run_validation(
        db,
        config=_cfg(
            symbols=["AAPL"],
            include_sell_reduce=False,
            portfolio_risk_config=PortfolioRiskConfig(
                max_symbol_weight=0.35,
                max_sector_weight=0.50,
                sector_map_path=str(sector_map),
                min_correlation_points=20,
            ),
        ),
    )

    assert result.portfolio_risk["symbol_weights"]["AAPL"] > 0
    assert result.portfolio_risk["sector_weights"]["Technology"] > 0
    assert result.portfolio_risk["overweight_symbols"]
    assert result.portfolio_risk["overweight_sectors"]


def test_benchmark_unavailable_when_no_prices(tmp_path: Path) -> None:
    db = _db(tmp_path)

    result = run_validation(db, config=_cfg(symbols=["NONE"], benchmark=True))

    assert result.benchmark["available"] is False


def test_validation_does_not_create_or_modify_live_or_paper_tables(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)
    before_tables = list_user_tables(db)
    with sqlite3.connect(db) as conn:
        before_paper = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        before_real = conn.execute("SELECT COUNT(*) FROM real_order_history").fetchone()[0]

    run_validation(db, config=_cfg(symbols=["AAPL"], include_sell_reduce=True))

    assert list_user_tables(db) == before_tables
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == before_paper
        assert conn.execute("SELECT COUNT(*) FROM real_order_history").fetchone()[0] == before_real


def test_costs_recorded_and_net_return_below_gross(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)
    cost_model = CostModel(commission_rate=0.01, tax_rate=0.0, slippage_bps=100, min_order_value=1.0)

    result = run_validation(
        db,
        config=ValidationConfig(symbols=["AAPL"], initial_cash=1000.0, include_sell_reduce=True, cost_model=cost_model),
    )

    assert result.trades
    assert result.trades[0].commission > 0
    assert result.trades[0].slippage_cost > 0
    assert result.cost_summary["total_commission"] > 0
    assert result.cost_summary["total_slippage_cost"] > 0
    assert result.advanced_metrics["net_return_pct"] < result.advanced_metrics["gross_return_pct"]


def test_min_and_max_order_skips_recorded(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)
    min_model = CostModel(min_order_value=10_000.0)
    min_result = run_validation(db, config=ValidationConfig(symbols=["AAPL"], initial_cash=1000.0, cost_model=min_model))
    assert min_result.cost_summary["skipped_by_min_order_count"] > 0
    assert any(row["skip_reason"] == "SKIP_COST_MIN_ORDER" for row in min_result.skipped_orders)

    max_model = CostModel(min_order_value=1.0, max_order_value=10.0)
    max_result = run_validation(db, config=ValidationConfig(symbols=["AAPL"], initial_cash=1000.0, cost_model=max_model))
    assert max_result.cost_summary["skipped_by_max_order_count"] > 0
    assert any(row["skip_reason"] == "SKIP_COST_MAX_ORDER" for row in max_result.skipped_orders)


def test_liquidity_adjusts_trade_quantity(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    result = run_validation(
        db,
        config=_cfg(
            symbols=["AAPL"],
            initial_cash=100_000.0,
            liquidity_config=LiquidityConfig(liquidity_limit_pct=0.01, volume_lookback_days=3),
        ),
    )

    assert result.trades
    assert result.trades[0].liquidity_requested_quantity > result.trades[0].liquidity_adjusted_quantity
    assert result.liquidity_model["summary"]["adjusted_by_liquidity_count"] > 0
    assert result.advanced_metrics["adjusted_by_liquidity_count"] > 0


def test_liquidity_min_volume_skip(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    result = run_validation(
        db,
        config=_cfg(
            symbols=["AAPL"],
            initial_cash=100_000.0,
            liquidity_config=LiquidityConfig(min_daily_volume=100_000.0, volume_lookback_days=3),
        ),
    )

    assert result.metrics["trade_count"] == 0
    assert result.liquidity_model["summary"]["skipped_by_liquidity_count"] > 0
    assert any(row["skip_reason"] == "SKIP_LOW_VOLUME" for row in result.liquidity_model["skipped_orders"])


def test_liquidity_default_keeps_existing_behavior(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    no_liquidity = run_validation(db, config=_cfg(symbols=["AAPL"], initial_cash=100_000.0))
    disabled = run_validation(db, config=_cfg(symbols=["AAPL"], initial_cash=100_000.0, liquidity_config=LiquidityConfig()))

    assert [t.quantity for t in disabled.trades] == [t.quantity for t in no_liquidity.trades]
    assert disabled.liquidity_model["summary"]["enabled"] is False


def test_fx_multi_currency_trade_values(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    result = run_validation(
        db,
        config=_cfg(
            symbols=["AAPL"],
            initial_cash=1_350_000.0,
            fx_config=FXConfig(base_currency="KRW", default_symbol_currency="USD", fallback_rates={"USD": 1350.0, "KRW": 1.0}),
        ),
    )

    assert result.trades
    trade = result.trades[0]
    assert trade.symbol_currency == "USD"
    assert trade.base_currency == "KRW"
    assert trade.fx_rate == 1350.0
    assert trade.value_base_currency == trade.value * 1350.0
    assert result.fx_model["summary"]["base_currency"] == "KRW"
    assert result.equity_curve[0].equity_base_currency == result.equity_curve[0].equity


def test_fx_file_missing_keeps_default_currency_behavior(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db)

    result = run_validation(db, config=_cfg(symbols=["AAPL"], initial_cash=1000.0, fx_config=FXConfig()))

    assert result.trades[0].symbol_currency == "KRW"
    assert result.trades[0].fx_rate == 1.0
    assert result.fx_model["warnings"] == []
