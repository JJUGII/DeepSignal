"""기관형·문헌 기반 숫자 분석 조건 (단일 출처).

점수·거시·리스크·포트·비용·코인 임계값을 한곳에서 정의한다.
각 모듈은 여기의 DEFAULT_ANALYSIS_CONDITIONS를 참조한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScoreWeightThresholds:
    technical_weight: float = 0.6
    news_weight: float = 0.2
    macro_weight: float = 0.2
    score_min: float = -100.0
    score_max: float = 100.0
    buy_candidate_min: float = 60.0
    sell_candidate_max: float = -60.0
    min_final_score_default: float = 60.0
    min_confidence_default: float = 0.25
    valuation_weight: float = 0.0
    validation_candidate_scores: tuple[float, ...] = (50.0, 55.0, 60.0, 65.0, 70.0, 75.0)
    score_bucket_edges: tuple[float, ...] = (55.0, 65.0, 75.0)
    # valuation_weight 비활성(0.0): valuation_analyzer는 fair_pe=현재PER×0.9 순환정의라
    # mispricing이 구조적으로 상수에 수렴 → 랭킹에 0 기여하며 노이즈만 추가했음.
    # 절대기준(섹터 PER 등) 기반으로 재구축 후 다시 양수 가중 부여할 것.


@dataclass(frozen=True)
class TechnicalScoreThresholds:
    trend_points: tuple[tuple[float, int], ...] = (
        (1.0, 60),
        (0.5, 30),
        (0.0, 0),
        (-0.5, -30),
        (-1.0, -60),
    )
    rsi_period: int = 14
    ema_fast: int = 12
    ema_slow: int = 26
    rsi_overbought_severe: float = 75.0
    rsi_overbought_mild: float = 70.0
    rsi_oversold_severe: float = 25.0
    rsi_oversold_mild: float = 30.0
    rsi_overbought_severe_penalty: int = -20
    rsi_overbought_mild_penalty: int = -10
    rsi_oversold_severe_bonus: int = 20
    rsi_oversold_mild_bonus: int = 10
    close_above_ema_fast_bonus: int = 10
    close_below_ema_fast_penalty: int = -10

    def trend_delta(self, trend_score: float) -> int:
        mapping = dict(self.trend_points)
        return int(mapping.get(float(trend_score), 0))


@dataclass(frozen=True)
class MacroIndicatorThresholds:
    vix_elevated: float = 20.0
    vix_high: float = 30.0
    vix_low: float = 15.0
    vix_elevated_penalty: float = -20.0
    vix_high_penalty: float = -40.0
    vix_low_bonus: float = 10.0
    dxy_strong: float = 105.0
    dxy_weak: float = 100.0
    dxy_strong_penalty: float = -20.0
    dxy_weak_bonus: float = 5.0
    tnx_high: float = 4.5
    tnx_low: float = 3.0
    tnx_high_penalty: float = -20.0
    tnx_low_bonus: float = 10.0
    regime_risk_on_min: float = 20.0
    regime_risk_off_max: float = -20.0
    narrative_positive_min: float = 10.0
    narrative_negative_max: float = -10.0
    score_min: float = -100.0
    score_max: float = 100.0


@dataclass(frozen=True)
class PositionRiskThresholds:
    stop_loss_pct: float = -0.07
    take_profit_pct: float = 0.15
    warn_loss_pct: float = -0.03
    warn_profit_pct: float = 0.10
    drawdown_from_peak_review_pct: float = -0.20
    entry_drawdown_review_pct: float = -0.10
    take_profit_reduce_ratio: float = 0.5
    portfolio_loss_per_position_cap_pct: float = 0.02
    liberal_stop_loss_pct: float = -0.20


@dataclass(frozen=True)
class PortfolioAllocationThresholds:
    max_names: int = 5
    max_symbol_weight: float = 0.40
    min_symbol_weight: float = 0.05
    min_confidence: float = 0.20
    invest_cap_risk_off: float = 0.40
    invest_cap_neutral: float = 0.70
    invest_cap_risk_on: float = 0.95
    rebalance_drift_pct: float = 0.05
    advisory_concentrated_name_cap: float = 0.05


@dataclass(frozen=True)
class PortfolioRiskThresholds:
    max_symbol_weight: float = 0.35
    max_sector_weight: float = 0.50
    high_correlation_threshold: float = 0.80
    correlation_lookback_days: int = 60
    min_correlation_points: int = 20
    liquidity_limit_pct: float = 0.05
    liquidity_lookback_days: int = 20
    max_same_sector_buys: int = 2


@dataclass(frozen=True)
class TradingCostThresholds:
    commission_rate: float = 0.001
    slippage_rate: float = 0.0005
    slippage_bps: float = 5.0
    tax_rate: float = 0.0
    min_order_value_krw: float = 10_000.0
    min_trade_value_usd: float = 10.0
    rebalance_threshold_fraction: float = 0.01


@dataclass(frozen=True)
class CryptoTradingThresholds:
    """Percent points for TP/SL tuned for short-term crypto trading."""
    take_profit_pct: float = 2.0
    take_profit_buffer_pct: float = 0.05
    stop_loss_pct: float = -1.5
    stop_loss_buffer_pct: float = 0.05
    warn_loss_pct: float = -1.2
    warn_profit_pct: float = 1.5
    entry_drawdown_review_pct: float = -3.0
    order_pct_of_available: float = 0.35
    max_order_pct_of_available: float = 1.0
    max_single_position_pct: float = 0.15
    max_single_order_pct_of_total: float = 0.08
    max_order_cap_krw: float = 0.0
    dynamic_cap_floor_krw: float = 10_000.0
    dynamic_cap_target_pct_total: float = 0.60
    dynamic_cap_ceiling_pct_total: float = 1.00
    score_reference: float = 55.0
    score_factor_floor: float = 0.5
    max_orders_per_day_max: int = 200
    max_orders_per_day_min: int = 1
    min_krw_per_order_slot: float = 10_000.0
    atr_tp_multiplier: float = 1.1
    atr_sl_multiplier: float = 0.9
    tp_pct_min: float = 1.0
    tp_pct_max: float = 4.0
    sl_pct_min: float = -3.0
    sl_pct_max: float = -0.8
    prefer_fund_manager_tp_sl: bool = False
    scalping_mode: bool = True
    outcome_tune_enabled: bool = True
    outcome_tune_apply_tp_sl: bool = False
    outcome_tune_max_volume_ratio: float = 0.45
    block_buy_on_risk_off: bool = False
    max_rsi: float = 90.0
    min_volume_ratio: float = 0.3
    max_atr_pct: float = 12.0
    high_volatility_size_multiplier: float = 0.5
    rsi_period: int = 14
    atr_period: int = 14
    technical_weight: float = 0.8
    macro_weight: float = 0.2
    min_final_score: float = 45.0
    min_acc_trade_price_24h: float = 300_000_000.0
    market_universe: str = "all_krw"
    max_buy_scan_markets: int = 100
    ticker_batch_size: int = 100
    exclude_market_warning: bool = True
    prefer_non_holding_buy: bool = False
    rebuy_cooldown_minutes: int = 20
    max_buy_per_market_per_hour: int = 2
    post_sell_reentry_cooldown_minutes: int = 15
    max_buy_krw_per_market_per_day_pct: float = 0.12
    max_add_on_buys_per_market_per_day: int = 3
    min_hold_minutes_before_sell: int = 5
    near_take_profit_min_pnl_pct: float = 1.2
    # 일일 매수 가드레일(0=무제한). 무제한이면 잘못된 신호가 반복돼도 멈추지 않으므로
    # 보수 기본값을 둔다. CLI(--max-distinct-buy-markets-per-day / --max-buy-krw-per-day)
    # 또는 env로 상향 가능.
    max_distinct_buy_markets_per_day: int = 5
    max_buy_krw_per_day: float = 300000.0
    min_expected_rr_after_fees: float = 0.75
    max_spread_bps_for_entry: float = 55.0
    limit_buy_requote_max_attempts: int = 2
    limit_buy_requote_wait_sec: float = 8.0
    limit_buy_requote_tick_pct: float = 0.05
    concentration_warn_pct: float = 0.12
    concentration_block_pct: float = 0.18
    overweight_reduce_trigger_pct: float = 0.20
    session_max_signed_change_rate: float = 0.08
    session_min_acc_trade_price_24h: float = 500_000_000.0
    # 스테이블코인 / 거래 불가 종목 영구 제외 (KRW-USDT, KRW-USD1 등)
    static_excluded_markets: tuple[str, ...] = ("KRW-USDT", "KRW-USD1", "KRW-BUSD", "KRW-USDC", "KRW-DAI")


@dataclass(frozen=True)
class AnalysisConditions:
    score: ScoreWeightThresholds = field(default_factory=ScoreWeightThresholds)
    technical: TechnicalScoreThresholds = field(default_factory=TechnicalScoreThresholds)
    macro: MacroIndicatorThresholds = field(default_factory=MacroIndicatorThresholds)
    risk: PositionRiskThresholds = field(default_factory=PositionRiskThresholds)
    portfolio: PortfolioAllocationThresholds = field(default_factory=PortfolioAllocationThresholds)
    portfolio_risk: PortfolioRiskThresholds = field(default_factory=PortfolioRiskThresholds)
    cost: TradingCostThresholds = field(default_factory=TradingCostThresholds)
    crypto: CryptoTradingThresholds = field(default_factory=CryptoTradingThresholds)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_ANALYSIS_CONDITIONS = AnalysisConditions()


def risk_guard_policy_defaults() -> dict[str, float]:
    r = DEFAULT_ANALYSIS_CONDITIONS.risk
    # 투자공격성 다이얼 → 주식 익절/손절 연동 (env 오버라이드)
    import os as _o
    _tp, _sl = r.take_profit_pct, r.stop_loss_pct
    try:
        if _o.environ.get("DEEPSIGNAL_STOCK_TP_PCT"):
            _tp = float(_o.environ["DEEPSIGNAL_STOCK_TP_PCT"])
        if _o.environ.get("DEEPSIGNAL_STOCK_SL_PCT"):
            _sl = float(_o.environ["DEEPSIGNAL_STOCK_SL_PCT"])
    except ValueError:
        pass
    return {
        "stop_loss_pct": _sl,
        "take_profit_pct": _tp,
        "warn_loss_pct": r.warn_loss_pct,
        "warn_profit_pct": r.warn_profit_pct,
        "drawdown_from_peak_review_pct": r.drawdown_from_peak_review_pct,
        "entry_drawdown_review_pct": r.entry_drawdown_review_pct,
        "take_profit_reduce_ratio": r.take_profit_reduce_ratio,
    }
