"""Models for AI recommendation validation.

Validation is read-only against the project DB and uses an in-memory portfolio.
It never writes paper_* or live account tables.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from deepsignal.live_trading.ai_recommendation.cost_model import CostModel
from deepsignal.live_trading.ai_recommendation.fx_model import FXConfig
from deepsignal.live_trading.ai_recommendation.liquidity_model import LiquidityConfig
from deepsignal.live_trading.ai_recommendation.portfolio_risk_model import PortfolioRiskConfig


@dataclass
class ValidationConfig:
    symbols: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    initial_cash: float = 1_000_000.0
    include_sell_reduce: bool = False
    benchmark: bool = True
    risk_free_rate: float = 0.0
    costs_enabled: bool = True
    cost_model: CostModel = field(default_factory=CostModel)
    fx_config: FXConfig = field(default_factory=FXConfig)
    liquidity_config: LiquidityConfig = field(default_factory=LiquidityConfig)
    portfolio_risk_config: PortfolioRiskConfig = field(default_factory=PortfolioRiskConfig)
    output_dir: str = "outputs"
    max_position_weight: float = 0.25
    min_trade_value: float = 1.0


@dataclass
class ValidationTrade:
    date: str
    symbol: str
    action: str
    quantity: int
    price: float
    value: float
    cash_after: float
    equity_after: float
    reason: str
    realized_pnl: float = 0.0
    holding_days: int | None = None
    action_group: str = "entry"
    raw_price: float | None = None
    adjusted_price: float | None = None
    commission: float = 0.0
    tax: float = 0.0
    slippage_cost: float = 0.0
    total_cost: float = 0.0
    skip_reason: str = ""
    liquidity_requested_quantity: int | None = None
    liquidity_allowed_quantity: int | None = None
    liquidity_adjusted_quantity: int | None = None
    liquidity_skip_reason: str = ""
    liquidity_warning: str = ""
    symbol_currency: str = "KRW"
    base_currency: str = "KRW"
    fx_rate: float = 1.0
    value_base_currency: float = 0.0
    cost_base_currency: float = 0.0


@dataclass
class EquityPoint:
    date: str
    cash: float
    positions_value: float
    equity: float
    drawdown: float
    daily_return_pct: float = 0.0
    drawdown_pct: float = 0.0
    exposure_pct: float = 0.0
    equity_base_currency: float | None = None
    cash_by_currency: dict[str, float] = field(default_factory=dict)
    position_value_by_currency: dict[str, float] = field(default_factory=dict)
    fx_rates_used: dict[str, float] = field(default_factory=dict)


@dataclass
class ValidationResult:
    generated_at: str
    summary: dict[str, Any]
    metrics: dict[str, Any]
    advanced_metrics: dict[str, Any]
    benchmark: dict[str, Any]
    cost_model: dict[str, Any]
    cost_summary: dict[str, Any]
    skipped_orders: list[dict[str, Any]]
    cost_adjusted_metrics: dict[str, Any]
    fx_model: dict[str, Any]
    liquidity_model: dict[str, Any]
    portfolio_risk: dict[str, Any]
    trades: list[ValidationTrade]
    equity_curve: list[EquityPoint]
    action_breakdown: dict[str, dict[str, Any]]
    symbol_breakdown: dict[str, dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    threshold_tuning: dict[str, Any] | None = None
    output_files: dict[str, str] = field(default_factory=dict)
    safety_note: str = (
        "Validation only. No KIS calls, no live-approve, no --execute, "
        "no live orders, and no paper_* table writes."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "summary": dict(self.summary),
            "metrics": dict(self.metrics),
            "advanced_metrics": dict(self.advanced_metrics),
            "benchmark": dict(self.benchmark),
            "cost_model": dict(self.cost_model),
            "cost_summary": dict(self.cost_summary),
            "skipped_orders": list(self.skipped_orders),
            "cost_adjusted_metrics": dict(self.cost_adjusted_metrics),
            "fx_model": dict(self.fx_model),
            "liquidity_model": dict(self.liquidity_model),
            "portfolio_risk": dict(self.portfolio_risk),
            "trades": [asdict(t) for t in self.trades],
            "equity_curve": [asdict(p) for p in self.equity_curve],
            "action_breakdown": dict(self.action_breakdown),
            "symbol_breakdown": dict(self.symbol_breakdown),
            "warnings": list(self.warnings),
            "threshold_tuning": dict(self.threshold_tuning) if self.threshold_tuning else None,
            "validation_warnings": list(self.warnings),
            "output_files": dict(self.output_files),
            "safety_note": self.safety_note,
        }
