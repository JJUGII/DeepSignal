"""Crypto quality gates — validation / liquidity / macro regime (stock-aligned)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_signal_scorer import CryptoMarketScore, build_crypto_score_breakdown
from deepsignal.crypto_trading.crypto_sell_triggers import SellTrigger
from deepsignal.crypto_trading.upbit_broker import MIN_ORDER_KRW, CryptoHolding, UpbitTicker
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto
_SCORE = DEFAULT_ANALYSIS_CONDITIONS.score


@dataclass
class CryptoRecommendationQualityConfig:
    min_final_score: float = _CRYPTO.min_final_score
    min_volume_ratio: float = _CRYPTO.min_volume_ratio
    min_acc_trade_price_24h: float = _CRYPTO.min_acc_trade_price_24h
    block_buy_on_risk_off: bool = _CRYPTO.block_buy_on_risk_off
    use_active_thresholds: bool = True
    output_dir: str = "outputs"
    enabled: bool = True
    concentration_warn_pct: float = _CRYPTO.concentration_warn_pct
    concentration_block_pct: float = _CRYPTO.concentration_block_pct


def resolve_crypto_min_final_score(
    *,
    config: CryptoRecommendationQualityConfig | None = None,
    output_dir: str | Path | None = None,
) -> tuple[float, str]:
    cfg = config or CryptoRecommendationQualityConfig()
    # 투자공격성 다이얼: CRYPTO_MIN_FINAL_SCORE 가 있으면 최우선 적용
    # (높은 단계일수록 코인 매수 점수 문턱을 낮춰 더 공격적으로 진입)
    import os as _o_mfs
    _ov_mfs = _o_mfs.environ.get("CRYPTO_MIN_FINAL_SCORE", "").strip()
    if _ov_mfs:
        try:
            return float(_ov_mfs), "aggression_dial"
        except ValueError:
            pass
    if cfg.use_active_thresholds and output_dir:
        try:
            from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import (
                load_active_crypto_thresholds,
            )

            tuned = load_active_crypto_thresholds(output_dir)
            if tuned and tuned.buy_win_rate is not None:
                src = "outcome_scalping" if bool(getattr(_CRYPTO, "scalping_mode", True)) else "outcome_default"
                return float(_SCORE.buy_candidate_min), src
        except Exception:
            pass
    return float(cfg.min_final_score), "scalping_default" if bool(getattr(_CRYPTO, "scalping_mode", True)) else "crypto_default"


def check_crypto_validation_gate(
    final_score: float | None,
    *,
    macro_regime: str,
    min_final_score: float,
    block_buy_on_risk_off: bool = True,
    enforce_min_score: bool = True,
) -> tuple[str, list[str]]:
    """Return gate status ok|blocked|warning and reasons."""
    blocked: list[str] = []
    regime = str(macro_regime or "").lower()
    if block_buy_on_risk_off and ("risk_off" in regime or "risk-off" in regime):
        blocked.append(f"macro_regime:{regime}")
        return "blocked", blocked

    if final_score is None:
        if enforce_min_score:
            blocked.append("final_score_missing")
            return "blocked", blocked
        return "warning", ["final_score_missing"]

    if not enforce_min_score:
        return "ok", []

    fs = float(final_score)
    if fs < float(min_final_score):
        blocked.append(f"below_min_final_score:{fs:.1f}<{min_final_score:.1f}")
        return "blocked", blocked

    if fs < float(min_final_score) + 5.0:
        return "warning", [f"final_score_marginal:{fs:.1f}"]

    return "ok", []


def check_crypto_liquidity_gate(
    ticker: UpbitTicker,
    *,
    quality_ok: bool,
    quality_reason: str,
    quality_diag: dict[str, Any],
    min_volume_ratio: float,
    min_acc_trade_price_24h: float,
    order_krw: float,
) -> tuple[str, list[str]]:
    blocked: list[str] = []
    if not quality_ok:
        blocked.append(quality_reason or "quality_filter_fail")
        return "blocked", blocked

    acc = float(ticker.acc_trade_price_24h or 0)
    if acc < float(min_acc_trade_price_24h):
        blocked.append(f"acc_trade_24h:{acc:,.0f}<{min_acc_trade_price_24h:,.0f}")
        return "blocked", blocked

    vol_ratio = quality_diag.get("volume_ratio")
    if isinstance(vol_ratio, (int, float)):
        if float(vol_ratio) < float(min_volume_ratio):
            blocked.append(f"volume_ratio:{float(vol_ratio):.2f}<{min_volume_ratio}")
            return "blocked", blocked
    else:
        return "warning", ["volume_ratio_unavailable"]

    if order_krw < MIN_ORDER_KRW:
        blocked.append(f"order_krw:{order_krw}<{MIN_ORDER_KRW}")
        return "blocked", blocked

    return "ok", []


def check_crypto_concentration_gate(
    *,
    current_position_krw: float,
    total_portfolio_krw: float,
    order_krw: float,
    warn_pct: float,
    block_pct: float,
) -> tuple[str, list[str]]:
    if total_portfolio_krw <= 0:
        return "warning", ["portfolio_total_unavailable"]
    cur = max(0.0, float(current_position_krw))
    total = float(total_portfolio_krw)
    nxt = max(0.0, cur + float(order_krw))
    cur_pct = cur / total
    nxt_pct = nxt / total
    if nxt_pct > float(block_pct):
        return "blocked", [f"concentration:{nxt_pct:.3f}>{float(block_pct):.3f}"]
    if nxt_pct > float(warn_pct):
        return "warning", [f"concentration_warn:{nxt_pct:.3f}>{float(warn_pct):.3f}"]
    return "ok", []


def apply_crypto_buy_quality_gates(
    market_score: CryptoMarketScore,
    *,
    ticker: UpbitTicker,
    macro_context: dict[str, Any],
    order_krw: float,
    current_position_krw: float = 0.0,
    total_portfolio_krw: float = 0.0,
    config: CryptoRecommendationQualityConfig | None = None,
    output_dir: str | Path | None = None,
) -> tuple[bool, dict[str, str], dict[str, Any], list[str]]:
    cfg = config or CryptoRecommendationQualityConfig()
    out_dir = output_dir or cfg.output_dir
    if not cfg.enabled:
        bd = build_crypto_score_breakdown(market_score, macro_context)
        return True, {}, bd, []

    from deepsignal.crypto_trading.crypto_gate_config import (
        crypto_gate_mode,
        skip_min_final_score_block,
    )

    min_fs, fs_src = resolve_crypto_min_final_score(config=cfg, output_dir=out_dir)
    enforce_min_score = not skip_min_final_score_block()
    gates: dict[str, str] = {
        "min_final_score": f"{min_fs:.1f}({fs_src})",
        "gate_mode": crypto_gate_mode(),
    }
    blocked: list[str] = []

    val_status, val_reasons = check_crypto_validation_gate(
        market_score.final_score,
        macro_regime=market_score.macro_regime,
        min_final_score=min_fs,
        block_buy_on_risk_off=cfg.block_buy_on_risk_off,
        enforce_min_score=enforce_min_score,
    )
    gates["validation"] = val_status
    blocked.extend(val_reasons)

    liq_status, liq_reasons = check_crypto_liquidity_gate(
        ticker,
        quality_ok=market_score.quality_ok,
        quality_reason=market_score.quality_reason,
        quality_diag=market_score.quality_diag,
        min_volume_ratio=cfg.min_volume_ratio,
        min_acc_trade_price_24h=cfg.min_acc_trade_price_24h,
        order_krw=order_krw,
    )
    gates["liquidity"] = liq_status
    blocked.extend(liq_reasons)

    conc_status, conc_reasons = check_crypto_concentration_gate(
        current_position_krw=current_position_krw,
        total_portfolio_krw=total_portfolio_krw,
        order_krw=order_krw,
        warn_pct=cfg.concentration_warn_pct,
        block_pct=cfg.concentration_block_pct,
    )
    gates["concentration"] = conc_status
    blocked.extend(conc_reasons)

    breakdown = build_crypto_score_breakdown(market_score, macro_context)
    breakdown["quality_gates"] = dict(gates)
    allowed = (
        val_status != "blocked"
        and liq_status != "blocked"
        and conc_status != "blocked"
    )
    return allowed, gates, breakdown, blocked


def build_sell_quality_gates(
    holding: CryptoHolding,
    *,
    trigger: SellTrigger,
    macro_context: dict[str, Any],
) -> tuple[dict[str, str], dict[str, Any], list[str]]:
    gates: dict[str, str] = {
        "validation": "ok",
        "liquidity": "ok" if holding.valuation_krw >= MIN_ORDER_KRW else "blocked",
        "sell_trigger": str(trigger),
    }
    blocked: list[str] = []
    if holding.valuation_krw < MIN_ORDER_KRW:
        blocked.append(f"valuation<{MIN_ORDER_KRW}")

    breakdown = {
        "technical_score": None,
        "news_score": None,
        "macro_score": macro_context.get("macro_score"),
        "final_score": None,
        "macro_regime": macro_context.get("market_regime"),
        "pnl_pct": holding.pnl_pct,
        "sell_trigger": trigger,
        "display": {
            "technical": "n/a",
            "news": "n/a",
            "macro": (
                f"{float(macro_context['macro_score']):+.1f}"
                if macro_context.get("macro_score") is not None
                and math.isfinite(float(macro_context["macro_score"]))
                else "n/a"
            ),
            "final": "n/a",
            "macro_regime": str(macro_context.get("market_regime") or "n/a"),
            "pnl": f"{holding.pnl_pct:+.2f}%",
        },
    }
    return gates, breakdown, blocked
