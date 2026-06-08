"""Quality gates for live AI recommendations (liquidity, portfolio risk, breakdown)."""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from deepsignal.live_trading.ai_recommendation.cost_model import CostModel
from deepsignal.live_trading.ai_recommendation.liquidity_model import LiquidityConfig, check_liquidity
from deepsignal.live_trading.ai_recommendation.portfolio_risk_model import (
    PortfolioRiskConfig,
    build_portfolio_risk_result,
    load_sector_map,
)
from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    RecommendationConfig,
    RecommendationResult,
)
from deepsignal.live_trading.ai_recommendation.validation_threshold_tuning import (
    ValidationThresholdSummary,
    load_threshold_summary,
    resolve_min_final_score,
)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def build_score_breakdown(
    signal: dict[str, Any],
    macro_context: dict[str, Any],
) -> dict[str, Any]:
    tech = signal.get("technical_score")
    news = signal.get("news_score")
    macro = signal.get("macro_score") or macro_context.get("macro_score")
    final = signal.get("final_score")

    def _fmt(v: Any) -> str:
        if v is None:
            return "n/a"
        try:
            return f"{float(v):+.1f}"
        except (TypeError, ValueError):
            return "n/a"

    return {
        "technical_score": tech,
        "news_score": news,
        "macro_score": macro,
        "final_score": final,
        "macro_regime": macro_context.get("market_regime"),
        "display": {
            "technical": _fmt(tech),
            "news": _fmt(news),
            "macro": _fmt(macro),
            "final": _fmt(final),
            "macro_regime": str(macro_context.get("market_regime") or "n/a"),
        },
    }


def load_recent_market_history(
    db_path: str,
    symbols: set[str],
    *,
    lookback_days: int = 30,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float | None]], str | None]:
    """Return prices_by_day, volumes_by_day, latest_day (YYYY-MM-DD)."""
    if not symbols:
        return {}, {}, None
    start = (date.today() - timedelta(days=max(lookback_days, 1))).isoformat()
    sym_list = sorted(symbols)
    placeholders = ",".join("?" for _ in sym_list)
    sql = (
        "SELECT symbol, substr(bar_time, 1, 10) AS d, close, volume FROM market_prices "
        f"WHERE timeframe = '1d' AND symbol IN ({placeholders}) AND bar_time >= ? "
        "ORDER BY d ASC"
    )
    prices_by_day: dict[str, dict[str, float]] = defaultdict(dict)
    volumes_by_day: dict[str, dict[str, float | None]] = defaultdict(dict)
    try:
        with sqlite3.connect(str(Path(db_path).expanduser().resolve())) as conn:
            rows = conn.execute(sql, [*sym_list, start]).fetchall()
    except sqlite3.Error:
        return {}, {}, None
    latest: str | None = None
    for sym, d, close, vol in rows:
        if not d or not sym:
            continue
        try:
            px = float(close)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(px) or px <= 0:
            continue
        sym_u = str(sym).upper()
        prices_by_day[str(d)][sym_u] = px
        volumes_by_day[str(d)][sym_u] = None if vol is None else float(vol)
        if latest is None or d > latest:
            latest = str(d)
    return dict(prices_by_day), dict(volumes_by_day), latest


def _default_liquidity_config(config: RecommendationConfig) -> LiquidityConfig:
    return LiquidityConfig(
        liquidity_limit_pct=float(config.liquidity_limit_pct),
        min_daily_volume=config.min_daily_volume,
        min_daily_value=config.min_daily_value,
        volume_lookback_days=int(config.liquidity_lookback_days),
        use_average_volume=True,
    )


def _default_portfolio_risk_config(config: RecommendationConfig) -> PortfolioRiskConfig:
    sector_path = config.sector_map_path
    if not sector_path:
        candidate = Path("config/symbol_sector_map.json")
        if candidate.is_file():
            sector_path = str(candidate)
    return PortfolioRiskConfig(
        max_symbol_weight=float(config.max_position_weight),
        max_sector_weight=float(config.max_sector_weight),
        high_correlation_threshold=float(config.high_correlation_threshold),
        lookback_days=int(config.portfolio_risk_lookback_days),
        sector_map_path=sector_path,
    )


def _default_cost_model(config: RecommendationConfig) -> CostModel:
    return CostModel(
        commission_rate=float(config.cost_commission_rate),
        slippage_bps=float(config.cost_slippage_bps),
        min_order_value=float(config.cost_min_order_value),
        enabled=bool(config.cost_gates_enabled),
    )


def apply_per_symbol_quality_gates(
    rec: RecommendationResult,
    *,
    signal: dict[str, Any],
    day: str | None,
    prices_by_day: dict[str, dict[str, float]],
    volumes_by_day: dict[str, dict[str, float | None]],
    liquidity_cfg: LiquidityConfig,
    cost_model: CostModel,
    config: RecommendationConfig,
    threshold_summary: ValidationThresholdSummary | None = None,
) -> RecommendationResult:
    gates = dict(rec.quality_gates)
    blocked = list(rec.blocked_reasons)
    risk_notes = list(rec.risk_notes)
    allowed = rec.allowed_for_plan
    qty = rec.suggested_quantity
    px = rec.suggested_limit_price
    order_value = rec.estimated_order_value

    if rec.action not in {"BUY", "INCREASE"} or not config.enable_quality_gates:
        return rec

    if signal:
        fs = rec.source_signal_score
        min_score, score_src = resolve_min_final_score(
            rec.symbol,
            config=config,
            summary=threshold_summary,
            price_hint=float(px) if px and px > 0 else None,
        )
        gates["min_final_score"] = f"{min_score:.1f}({score_src})"
        if fs is not None and fs < min_score:
            blocked.append(f"below_min_final_score:{fs:.1f}<{min_score:.1f}")
            gates["score_threshold"] = "blocked"
            allowed = False

    if px and qty > 0 and day and liquidity_cfg.enabled:
        liq = check_liquidity(
            symbol=rec.symbol,
            day=day,
            price=float(px),
            requested_quantity=int(qty),
            prices_by_day=prices_by_day,
            volumes_by_day=volumes_by_day,
            config=liquidity_cfg,
        )
        gates["liquidity"] = "ok"
        if liq.skipped:
            blocked.append(liq.skip_reason or "liquidity_skipped")
            gates["liquidity"] = "blocked"
            allowed = False
        elif liq.adjusted_quantity < qty:
            qty = liq.adjusted_quantity
            order_value = float(qty) * float(px)
            risk_notes.append(
                f"liquidity: 주문수량 {rec.suggested_quantity}→{qty} (ADV 제한 {liquidity_cfg.liquidity_limit_pct:.0%})"
            )
            gates["liquidity"] = "adjusted"
            if qty <= 0:
                blocked.append("liquidity_zero_qty")
                allowed = False
        elif liq.warnings:
            gates["liquidity"] = "warning"
            risk_notes.extend(f"liquidity:{w}" for w in liq.warnings[:2])

    if cost_model.enabled and px and qty > 0:
        skip = cost_model.should_skip_order(order_value)
        est = cost_model.estimate_buy_cost(float(px), int(qty))
        gates["cost"] = "ok"
        rec_breakdown = dict(rec.score_breakdown)
        rec_breakdown["estimated_cost"] = {
            "commission": est["commission"],
            "slippage_cost": est["slippage_cost"],
            "total_cost": est["total_cost"],
            "adjusted_price": est["adjusted_price"],
        }
        if skip and config.cost_block_below_min:
            blocked.append(skip)
            gates["cost"] = "blocked"
            allowed = False
        elif skip:
            risk_notes.append(f"cost: {skip} (참고, 차단 없음)")
            gates["cost"] = "warning"
        rec = replace(rec, score_breakdown=rec_breakdown)

    return replace(
        rec,
        suggested_quantity=int(qty),
        estimated_order_value=float(order_value),
        blocked_reasons=sorted(set(blocked)),
        risk_notes=risk_notes,
        allowed_for_plan=allowed and qty > 0 and not blocked,
        quality_gates=gates,
    )


def apply_portfolio_risk_gates(
    recs: list[RecommendationResult],
    *,
    account: AccountContext,
    prices_by_day: dict[str, dict[str, float]],
    latest_day: str | None,
    config: RecommendationConfig,
) -> list[RecommendationResult]:
    if not config.enable_quality_gates or not config.portfolio_risk_enabled:
        return recs

    pr_cfg = _default_portfolio_risk_config(config)
    latest_prices: dict[str, float] = {}
    if latest_day and latest_day in prices_by_day:
        latest_prices.update(prices_by_day[latest_day])
    for rec in recs:
        if rec.suggested_limit_price and rec.suggested_limit_price > 0:
            latest_prices[rec.symbol] = float(rec.suggested_limit_price)

    positions: dict[str, int] = {}
    for row in account.positions:
        sym = str(row.get("symbol") or "").upper()
        if sym:
            positions[sym] = int(float(row.get("quantity") or 0))

    buy_recs = [r for r in recs if r.action in {"BUY", "INCREASE"} and r.allowed_for_plan]
    buy_recs.sort(key=lambda r: (r.priority, r.estimated_order_value), reverse=True)
    for rec in buy_recs:
        positions[rec.symbol] = positions.get(rec.symbol, 0) + int(rec.suggested_quantity)

    equity = _float(account.total_equity, 0.0)
    if equity <= 0:
        equity = _float(account.cash, 0.0) + sum(
            int(positions.get(s, 0)) * float(latest_prices.get(s, 0.0)) for s in positions
        )
    equity = max(equity, 1.0)

    risk = build_portfolio_risk_result(
        positions=positions,
        latest_prices=latest_prices,
        prices_by_day=prices_by_day,
        config=pr_cfg,
        total_capital=equity,
    )

    blocked_symbols: set[str] = set()
    for row in risk.overweight_symbols:
        if row.get("severity") == "blocked":
            blocked_symbols.add(str(row["symbol"]).upper())
    for row in risk.high_correlation_pairs:
        if row.get("severity") != "blocked":
            continue
        a = str(row.get("symbol_a", "")).upper()
        b = str(row.get("symbol_b", "")).upper()
        allowed_pair = [r for r in buy_recs if r.symbol in {a, b} and r.allowed_for_plan and r.symbol not in blocked_symbols]
        if len(allowed_pair) >= 2:
            drop = min(allowed_pair, key=lambda r: (r.priority, r.estimated_order_value))
            blocked_symbols.add(drop.symbol)

    sector_map = load_sector_map(pr_cfg.sector_map_path)
    sector_counts: dict[str, int] = defaultdict(int)
    for rec in buy_recs:
        if rec.allowed_for_plan and rec.symbol not in blocked_symbols:
            sec = sector_map.get(rec.symbol, "UNKNOWN")
            sector_counts[sec] += 1
    for sec, cnt in sector_counts.items():
        if cnt >= int(config.max_same_sector_buys) and config.max_same_sector_buys > 0:
            sec_recs = [
                r
                for r in buy_recs
                if r.allowed_for_plan
                and r.symbol not in blocked_symbols
                and sector_map.get(r.symbol, "UNKNOWN") == sec
            ]
            sec_recs.sort(key=lambda r: (r.priority, r.estimated_order_value))
            for drop in sec_recs[int(config.max_same_sector_buys) :]:
                blocked_symbols.add(drop.symbol)

    out: list[RecommendationResult] = []
    for rec in recs:
        gates = dict(rec.quality_gates)
        if rec.symbol in blocked_symbols and rec.action in {"BUY", "INCREASE"}:
            blocked = list(rec.blocked_reasons)
            blocked.append("portfolio_risk_concentration")
            gates["portfolio_risk"] = "blocked"
            out.append(
                replace(
                    rec,
                    allowed_for_plan=False,
                    blocked_reasons=sorted(set(blocked)),
                    quality_gates=gates,
                    risk_notes=list(rec.risk_notes) + ["portfolio_risk: 집중도/상관 한도"],
                )
            )
        elif rec.action in {"BUY", "INCREASE"}:
            gates["portfolio_risk"] = "warning" if risk.severity == "warning" else "ok"
            out.append(replace(rec, quality_gates=gates))
        else:
            out.append(rec)
    return out


def apply_recommendation_quality_gates(
    recs: list[RecommendationResult],
    *,
    db_path: str,
    account: AccountContext,
    signals: dict[str, dict[str, Any]],
    macro_context: dict[str, Any],
    config: RecommendationConfig,
) -> list[RecommendationResult]:
    if not config.enable_quality_gates:
        return recs

    symbols = {r.symbol for r in recs if r.action in {"BUY", "INCREASE"}}
    prices_by_day, volumes_by_day, latest_day = load_recent_market_history(
        db_path, symbols, lookback_days=config.liquidity_lookback_days
    )
    liquidity_cfg = _default_liquidity_config(config)
    cost_model = _default_cost_model(config)
    threshold_summary: ValidationThresholdSummary | None = None
    if config.use_validation_tuned_min_score:
        summary_path = config.validation_threshold_summary_path
        threshold_summary = load_threshold_summary(
            config.output_dir,
            path=summary_path if summary_path else None,
        )

    staged: list[RecommendationResult] = []
    for rec in recs:
        signal = signals.get(rec.symbol, {})
        breakdown = build_score_breakdown(signal, macro_context)
        gates: dict[str, str] = {}
        base = replace(rec, score_breakdown=breakdown, quality_gates=gates)
        if rec.action in {"BUY", "INCREASE"}:
            base = apply_per_symbol_quality_gates(
                base,
                signal=signal,
                day=latest_day,
                prices_by_day=prices_by_day,
                volumes_by_day=volumes_by_day,
                liquidity_cfg=liquidity_cfg,
                cost_model=cost_model,
                config=config,
                threshold_summary=threshold_summary,
            )
        staged.append(base)

    return apply_portfolio_risk_gates(
        staged,
        account=account,
        prices_by_day=prices_by_day,
        latest_day=latest_day,
        config=config,
    )
