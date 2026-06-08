"""Fund-manager numeric policy — shared stock/crypto thresholds from analysis_conditions."""

from __future__ import annotations

from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_RISK = DEFAULT_ANALYSIS_CONDITIONS.risk
_SCORE = DEFAULT_ANALYSIS_CONDITIONS.score
_PORT = DEFAULT_ANALYSIS_CONDITIONS.portfolio
_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto


def risk_pct_to_display(fraction: float) -> float:
    """Stock risk uses fractions (-0.07); crypto uses percent points (-7.0)."""
    return round(float(fraction) * 100.0, 3)


def fund_manager_tp_sl_percent() -> tuple[float, float, float, float]:
    """Take-profit / stop-loss in crypto percent-point form (+15 / -7)."""
    return (
        risk_pct_to_display(_RISK.take_profit_pct),
        risk_pct_to_display(_RISK.stop_loss_pct),
        float(_CRYPTO.take_profit_buffer_pct),
        float(_CRYPTO.stop_loss_buffer_pct),
    )


def fund_manager_warn_levels_percent() -> tuple[float, float, float]:
    return (
        risk_pct_to_display(_RISK.warn_loss_pct),
        risk_pct_to_display(_RISK.warn_profit_pct),
        risk_pct_to_display(_RISK.entry_drawdown_review_pct),
    )


def fund_manager_buy_min_final_score() -> float:
    return float(_SCORE.buy_candidate_min)


def fund_manager_max_single_position_pct() -> float:
    return float(_PORT.advisory_concentrated_name_cap)


def sync_crypto_defaults_from_fund_manager() -> dict[str, float]:
    """Reference map for docs/tests — frozen dataclass defaults already mirror these."""
    tp, sl, tp_buf, sl_buf = fund_manager_tp_sl_percent()
    return {
        "take_profit_pct": tp,
        "stop_loss_pct": sl,
        "take_profit_buffer_pct": tp_buf,
        "stop_loss_buffer_pct": sl_buf,
        "min_final_score": fund_manager_buy_min_final_score(),
        "max_single_position_pct": fund_manager_max_single_position_pct(),
    }
