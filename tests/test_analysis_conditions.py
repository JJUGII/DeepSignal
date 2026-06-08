"""analysis_conditions 단일 출처·모듈 연동 테스트."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig
from deepsignal.live_trading.risk_guard import RiskGuardPolicy, evaluate_position_risk
from deepsignal.paper_trading.paper_trading_engine import PaperRebalanceConfig
from deepsignal.portfolio.portfolio_engine import PortfolioEngine
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS
from deepsignal.scoring.macro_scorer import MacroScorer
from deepsignal.scoring.signal_scorer import SignalScorer


def test_default_conditions_export() -> None:
    data = DEFAULT_ANALYSIS_CONDITIONS.to_dict()
    assert data["risk"]["stop_loss_pct"] == -0.07
    assert data["score"]["buy_candidate_min"] == 60.0
    assert data["macro"]["vix_high"] == 30.0


def test_signal_scorer_uses_central_thresholds() -> None:
    ac = DEFAULT_ANALYSIS_CONDITIONS
    sc = SignalScorer()
    assert sc.conditions is ac
    assert sc.decide_action(ac.score.buy_candidate_min) == "BUY_CANDIDATE"
    assert sc.decide_action(ac.score.sell_candidate_max) == "SELL_CANDIDATE"


def test_macro_scorer_vix_thresholds() -> None:
    result = MacroScorer().calculate_macro_score([{"indicator_name": "VIX", "value": 31.0}])
    assert result.macro_score is not None
    assert result.macro_score <= -30.0


def test_risk_guard_peak_drawdown_warning() -> None:
    pos = {
        "symbol": "005930",
        "quantity": 10,
        "avg_price": 70_000.0,
        "current_price": 78_000.0,
        "market_value": 780_000.0,
        "raw": {"peak_price": 100_000.0},
    }
    r = evaluate_position_risk(pos)
    assert r.risk_level == "WARNING"
    assert any("peak drawdown" in a.lower() for a in r.alerts)


def test_risk_policy_matches_central_defaults() -> None:
    r = DEFAULT_ANALYSIS_CONDITIONS.risk
    pol = RiskGuardPolicy()
    assert pol.stop_loss_pct == r.stop_loss_pct
    assert pol.take_profit_pct == r.take_profit_pct


def test_paper_rebalance_defaults_match_costs() -> None:
    c = DEFAULT_ANALYSIS_CONDITIONS.cost
    cfg = PaperRebalanceConfig()
    assert cfg.commission_rate == c.commission_rate
    assert cfg.slippage_rate == c.slippage_rate


def test_crypto_quality_defaults_match() -> None:
    c = DEFAULT_ANALYSIS_CONDITIONS.crypto
    cfg = CryptoBuyQualityConfig()
    assert cfg.max_rsi == c.max_rsi
    assert cfg.min_volume_ratio == c.min_volume_ratio


def test_portfolio_engine_constants() -> None:
    import deepsignal.portfolio.portfolio_engine as pe

    p = DEFAULT_ANALYSIS_CONDITIONS.portfolio
    assert pe._MAX_NAMES == p.max_names
    assert pe._MAX_WEIGHT == p.max_symbol_weight
