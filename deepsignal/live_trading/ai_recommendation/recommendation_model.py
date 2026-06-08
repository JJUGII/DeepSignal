"""Models for AI live trade recommendations.

These models are report/order-plan inputs only. They do not execute orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_AC = DEFAULT_ANALYSIS_CONDITIONS


@dataclass
class AccountContext:
    broker: str = "kis"
    snapshot_time: str | None = None
    cash: float | None = None
    withdrawable_cash: float | None = None
    total_market_value: float | None = None
    total_equity: float | None = None
    positions: list[dict[str, Any]] = field(default_factory=list)
    stale_snapshot: bool = False
    snapshot_age_minutes: float | None = None
    source: str = "local_db"


@dataclass
class OperationalRiskContext:
    safety_audit_status: str = "NOT_AVAILABLE"
    reconcile_status: str = "NOT_AVAILABLE"
    risk_status: str = "NOT_AVAILABLE"
    partial_fill_open: bool = False
    duplicate_order_symbols: list[str] = field(default_factory=list)
    archive_repeated_problem_types: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RecommendationConfig:
    broker: str = "kis"
    symbols: list[str] | None = None
    signal_scan_limit: int = 500
    include_market_price_universe: bool = False
    market_price_universe_limit: int = 2000
    market_price_lookback_days: int = 30
    extra_symbols: list[str] | None = None
    max_recommendations: int = 10
    capital_limit: float | None = None
    allow_sell_candidates: bool = False
    output_dir: str = "outputs"
    min_confidence: float = _AC.score.min_confidence_default
    max_position_weight: float = 0.25
    stale_snapshot_minutes: int = 30
    recent_order_minutes: int = 60
    currency: str = "KRW"
    dry_run: bool = True
    allow_test_plan_order: bool = False
    ignore_safety_block_for_test: bool = False
    debug_plan: bool = False
    enable_quality_gates: bool = True
    min_final_score: float = _AC.score.min_final_score_default
    use_validation_tuned_min_score: bool = True

    def __post_init__(self) -> None:
        # 투자공격성 다이얼: DEEPSIGNAL_STOCK_MIN_SCORE 가 있으면 매수 점수 기준을 덮어씀
        import os as _o
        _ov = _o.environ.get("DEEPSIGNAL_STOCK_MIN_SCORE", "").strip()
        if _ov:
            try:
                self.min_final_score = float(_ov)
                self.use_validation_tuned_min_score = False  # 다이얼 값 직접 사용
            except ValueError:
                pass
    validation_threshold_summary_path: str | None = None
    validation_tune_fallback_score: float | None = None
    min_final_score_by_symbol: dict[str, float] | None = None
    liquidity_limit_pct: float = _AC.portfolio_risk.liquidity_limit_pct
    min_daily_volume: float | None = None
    min_daily_value: float | None = None
    liquidity_lookback_days: int = _AC.portfolio_risk.liquidity_lookback_days
    portfolio_risk_enabled: bool = True
    max_sector_weight: float = _AC.portfolio_risk.max_sector_weight
    high_correlation_threshold: float = _AC.portfolio_risk.high_correlation_threshold
    portfolio_risk_lookback_days: int = _AC.portfolio_risk.correlation_lookback_days
    sector_map_path: str | None = None
    max_same_sector_buys: int = 2
    cost_gates_enabled: bool = True
    cost_commission_rate: float = _AC.cost.commission_rate
    cost_slippage_bps: float = _AC.cost.slippage_bps
    cost_min_order_value: float = _AC.cost.min_order_value_krw
    cost_block_below_min: bool = False


@dataclass
class RecommendationResult:
    symbol: str
    action: str
    action_label: str
    confidence: float
    priority: int
    reason: str
    risk_notes: list[str]
    current_quantity: int
    current_value: float
    current_weight: float
    target_weight: float
    suggested_quantity: int
    suggested_limit_price: float | None
    estimated_order_value: float
    source_signal_score: float | None
    macro_context: dict[str, Any]
    account_context: dict[str, Any]
    blocked_reasons: list[str]
    allowed_for_plan: bool
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    quality_gates: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecommendationRunResult:
    generated_at: str
    status: str
    config: RecommendationConfig
    account_context: AccountContext
    macro_context: dict[str, Any]
    operational_risk_context: OperationalRiskContext
    recommendations: list[RecommendationResult]
    order_plan: dict[str, Any]
    output_files: dict[str, str]
    plan_diagnostics: dict[str, Any] | None = None
    safety_note: str = (
        "Recommendation only. No live-approve invocation, no --execute, "
        "no KIS order-cash POST, and no market orders."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "config": asdict(self.config),
            "account_context": asdict(self.account_context),
            "macro_context": dict(self.macro_context),
            "operational_risk_context": asdict(self.operational_risk_context),
            "recommendations": [r.to_dict() for r in self.recommendations],
            "order_plan": self.order_plan,
            "output_files": dict(self.output_files),
            "plan_diagnostics": self.plan_diagnostics,
            "safety_note": self.safety_note,
        }
