"""Backtest/paper validation for AI recommendation policy v1.

Reads only local DB market/signals/macro data and uses an in-memory portfolio.
No KIS calls, live-approve calls, --execute calls, or paper_* writes.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from deepsignal.live_trading.ai_recommendation.validation_metrics import (
    calculate_advanced_metrics,
    calculate_metrics,
    max_drawdown,
    summarize_trades,
)
from deepsignal.live_trading.ai_recommendation.cost_model import CostModel
from deepsignal.live_trading.ai_recommendation.portfolio_risk_model import (
    build_portfolio_risk_result,
    portfolio_risk_csv_rows,
)
from deepsignal.live_trading.ai_recommendation.liquidity_model import (
    LiquidityCheckResult,
    check_liquidity,
    liquidity_summary,
)
from deepsignal.live_trading.ai_recommendation.fx_model import FXModel
from deepsignal.live_trading.ai_recommendation.validation_model import (
    EquityPoint,
    ValidationConfig,
    ValidationResult,
    ValidationTrade,
)
from deepsignal.live_trading.ai_recommendation.validation_report import render_validation_markdown


def _symbol_set(symbols: list[str] | None) -> set[str] | None:
    if not symbols:
        return None
    out = {str(s).strip().upper() for s in symbols if str(s).strip()}
    return out or None


def _date_filter_sql(config: ValidationConfig, column: str) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if config.start_date:
        where.append(f"{column} >= ?")
        params.append(config.start_date)
    if config.end_date:
        where.append(f"{column} <= ?")
        params.append(config.end_date)
    return (" AND ".join(where), params)


def _load_price_rows(db_path: str, config: ValidationConfig) -> list[dict[str, Any]]:
    selected = _symbol_set(config.symbols)
    where, params = _date_filter_sql(config, "bar_time")
    clauses = ["timeframe = '1d'"]
    if where:
        clauses.append(where)
    if selected:
        clauses.append("symbol IN (" + ",".join("?" for _ in selected) + ")")
        params.extend(sorted(selected))
    sql = (
        "SELECT symbol, bar_time AS trade_date, close, volume FROM market_prices WHERE "
        + " AND ".join(clauses)
        + " ORDER BY bar_time ASC, symbol ASC"
    )
    with sqlite3.connect(str(Path(db_path).expanduser().resolve())) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return []
    return [
        {
            "symbol": str(r["symbol"]).upper(),
            "date": str(r["trade_date"])[:10],
            "close": float(r["close"]),
            "volume": None if r["volume"] is None else float(r["volume"]),
        }
        for r in rows
        if r["close"] is not None
    ]


def _load_signal_rows(db_path: str, config: ValidationConfig) -> dict[tuple[str, str], dict[str, Any]]:
    selected = _symbol_set(config.symbols)
    where, params = _date_filter_sql(config, "signal_date")
    clauses = ["strategy_name = 'technical_v1'"]
    if where:
        clauses.append(where)
    if selected:
        clauses.append("symbol IN (" + ",".join("?" for _ in selected) + ")")
        params.extend(sorted(selected))
    sql = (
        "SELECT symbol, signal_date, action, final_score, confidence, reason FROM signals WHERE "
        + " AND ".join(clauses)
        + " ORDER BY signal_date ASC, id ASC"
    )
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with sqlite3.connect(str(Path(db_path).expanduser().resolve())) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return {}
    for r in rows:
        key = (str(r["signal_date"])[:10], str(r["symbol"]).upper())
        out[key] = {k: r[k] for k in r.keys()}
    return out


def _load_macro_by_day(db_path: str, config: ValidationConfig) -> dict[str, float]:
    where, params = _date_filter_sql(config, "indicator_date")
    sql = "SELECT indicator_date, indicator_name, value FROM economic_indicators"
    if where:
        sql += " WHERE " + where
    with sqlite3.connect(str(Path(db_path).expanduser().resolve())) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return {}
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        try:
            by_day[str(r["indicator_date"])[:10]].append(float(r["value"]))
        except (TypeError, ValueError):
            continue
    return {day: sum(vals) / len(vals) for day, vals in by_day.items() if vals}


def _action_for_signal(signal: dict[str, Any] | None, qty: int) -> str:
    if not signal:
        return "HOLD" if qty > 0 else "SKIP"
    try:
        score = float(signal.get("final_score"))
    except (TypeError, ValueError):
        score = 0.0
    action = str(signal.get("action") or "").upper()
    if action == "BUY_CANDIDATE" or score >= 60:
        return "INCREASE" if qty > 0 else "BUY"
    if action == "SELL_CANDIDATE" or score <= -60:
        if qty <= 0:
            return "SKIP"
        return "SELL" if score <= -75 else "REDUCE"
    return "HOLD" if qty > 0 else "SKIP"


def _target_value(action: str, *, equity: float, current_value: float, risk_off: bool, config: ValidationConfig) -> float:
    if action == "BUY":
        weight = config.max_position_weight * (0.5 if risk_off else 1.0)
        return max(0.0, equity * weight)
    if action == "INCREASE":
        weight = config.max_position_weight * (0.5 if risk_off else 1.0)
        return max(0.0, equity * weight - current_value)
    return 0.0


def _portfolio_value(cash: float, positions: dict[str, int], prices: dict[str, float]) -> float:
    return float(cash) + sum(int(qty) * float(prices.get(sym, 0.0)) for sym, qty in positions.items())


def _portfolio_value_base(cash_base: float, positions: dict[str, int], prices: dict[str, float], fx: FXModel, day: str) -> float:
    value = float(cash_base)
    for sym, qty in positions.items():
        currency = fx.symbol_currency(sym)
        native_value = int(qty) * float(prices.get(sym, 0.0))
        value += fx.convert(native_value, currency, day).converted_amount
    return value


def _position_value_by_currency(positions: dict[str, int], prices: dict[str, float], fx: FXModel) -> dict[str, float]:
    out: dict[str, float] = {}
    for sym, qty in positions.items():
        currency = fx.symbol_currency(sym)
        out[currency] = out.get(currency, 0.0) + int(qty) * float(prices.get(sym, 0.0))
    return out


def _cost_model(config: ValidationConfig) -> CostModel:
    return config.cost_model if config.costs_enabled else CostModel.no_costs(currency=config.cost_model.currency)


def _days_between(start: str | None, end: str | None) -> int | None:
    try:
        if not start or not end:
            return None
        return max(0, (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days)
    except ValueError:
        return None


def _build_benchmark(
    *,
    prices_by_day: dict[str, dict[str, float]],
    symbols: list[str],
    initial_cash: float,
    cost_model: CostModel,
) -> dict[str, Any]:
    if not symbols or not prices_by_day:
        return {
            "available": False,
            "reason": "missing symbols or prices",
            "benchmark_costs_applied": bool(cost_model.enabled),
            "benchmark_total_cost": 0.0,
            "benchmark_net_return_pct": None,
        }
    dates = sorted(prices_by_day)
    first_prices: dict[str, float] = {}
    last_prices: dict[str, float] = {}
    for symbol in symbols:
        for day in dates:
            if symbol in prices_by_day[day]:
                first_prices[symbol] = prices_by_day[day][symbol]
                break
        for day in reversed(dates):
            if symbol in prices_by_day[day]:
                last_prices[symbol] = prices_by_day[day][symbol]
                break
    usable = [symbol for symbol in symbols if first_prices.get(symbol, 0.0) > 0 and last_prices.get(symbol, 0.0) > 0]
    if not usable:
        return {
            "available": False,
            "reason": "no symbols with start/end prices",
            "benchmark_costs_applied": bool(cost_model.enabled),
            "benchmark_total_cost": 0.0,
            "benchmark_net_return_pct": None,
        }
    alloc = float(initial_cash) / len(usable)
    quantities: dict[str, int] = {}
    total_cost = 0.0
    spent = 0.0
    for symbol in usable:
        adjusted = cost_model.adjusted_buy_price(first_prices[symbol])
        qty = int(alloc // (adjusted * (1.0 + cost_model.commission_rate))) if adjusted > 0 else 0
        estimate = cost_model.estimate_buy_cost(first_prices[symbol], qty)
        quantities[symbol] = qty
        spent += estimate["value"] + estimate["commission"]
        total_cost += estimate["total_cost"]
    leftover = float(initial_cash) - spent
    curve: list[float] = []
    for day in dates:
        value = leftover
        for symbol in usable:
            px = prices_by_day[day].get(symbol)
            if px is None:
                px = last((prices_by_day[d][symbol] for d in dates if d <= day and symbol in prices_by_day[d]), first_prices[symbol])
            value += quantities[symbol] * px
        curve.append(float(value))
    final_equity = curve[-1] if curve else float(initial_cash)
    ret = (final_equity - float(initial_cash)) / float(initial_cash) if initial_cash > 0 else 0.0
    return {
        "available": True,
        "symbols": usable,
        "benchmark_final_equity": final_equity,
        "benchmark_return_pct": ret,
        "benchmark_net_return_pct": ret,
        "benchmark_max_drawdown_pct": max_drawdown(curve),
        "benchmark_costs_applied": bool(cost_model.enabled),
        "benchmark_total_cost": total_cost,
    }


def last(values, default):
    out = default
    for out in values:
        pass
    return out


def run_validation(db_path: str, *, config: ValidationConfig) -> ValidationResult:
    price_rows = _load_price_rows(db_path, config)
    signals = _load_signal_rows(db_path, config)
    macro_by_day = _load_macro_by_day(db_path, config)
    warnings: list[str] = []
    if not price_rows:
        warnings.append("No market_prices rows found for validation period/symbols.")

    prices_by_day: dict[str, dict[str, float]] = defaultdict(dict)
    volumes_by_day: dict[str, dict[str, float | None]] = defaultdict(dict)
    all_symbols: set[str] = set()
    for row in price_rows:
        if not math.isfinite(float(row["close"])) or float(row["close"]) <= 0:
            continue
        prices_by_day[row["date"]][row["symbol"]] = float(row["close"])
        volumes_by_day[row["date"]][row["symbol"]] = row.get("volume")
        all_symbols.add(row["symbol"])

    dates = sorted(prices_by_day)
    cash = float(config.initial_cash)
    gross_cash = float(config.initial_cash)
    positions: dict[str, int] = defaultdict(int)
    gross_positions: dict[str, int] = defaultdict(int)
    avg_cost: dict[str, float] = defaultdict(float)
    entry_date: dict[str, str] = {}
    latest_prices: dict[str, float] = {}
    trades: list[ValidationTrade] = []
    skipped_orders: list[dict[str, Any]] = []
    liquidity_checks: list[LiquidityCheckResult] = []
    liquidity_skipped_orders: list[dict[str, Any]] = []
    liquidity_adjusted_orders: list[dict[str, Any]] = []
    equity_curve: list[EquityPoint] = []
    closed_pnls: list[float] = []
    risk_off_trade_count = 0
    peak = cash
    previous_equity = cash
    cm = _cost_model(config)
    fx = FXModel(config.fx_config)
    cost_summary = {
        "total_commission": 0.0,
        "total_tax": 0.0,
        "total_slippage_cost": 0.0,
        "total_trading_cost": 0.0,
        "skipped_by_min_order_count": 0,
        "skipped_by_max_order_count": 0,
        "skipped_by_cash_count": 0,
    }

    for day in dates:
        latest_prices.update(prices_by_day[day])
        equity_before = _portfolio_value_base(cash, positions, latest_prices, fx, day)
        risk_off = float(macro_by_day.get(day, 0.0)) <= -40.0
        for symbol in sorted(prices_by_day[day]):
            price = prices_by_day[day][symbol]
            qty = int(positions.get(symbol, 0))
            symbol_currency = fx.symbol_currency(symbol)
            fx_conv = fx.convert(1.0, symbol_currency, day)
            current_value = fx.convert(qty * price, symbol_currency, day).converted_amount
            signal = signals.get((day, symbol))
            action = _action_for_signal(signal, qty)
            reason = str(signal.get("reason") if signal else "no signal")
            trade_qty = 0
            value = 0.0
            liquidity_skip_reason = ""
            if action in {"BUY", "INCREASE"}:
                desired = _target_value(action, equity=equity_before, current_value=current_value, risk_off=risk_off, config=config)
                value = min(max(0.0, desired), cash)
                adjusted_price = cm.adjusted_buy_price(price)
                trade_qty = int((value / fx_conv.rate) // adjusted_price) if adjusted_price > 0 and fx_conv.rate > 0 else 0
                if action == "INCREASE" and trade_qty <= 0 and desired > 0 and cash >= price:
                    trade_qty = 1
                liq = check_liquidity(
                    symbol=symbol,
                    day=day,
                    price=price,
                    requested_quantity=trade_qty,
                    prices_by_day=prices_by_day,
                    volumes_by_day=volumes_by_day,
                    config=config.liquidity_config,
                )
                if config.liquidity_config.enabled and trade_qty > 0:
                    liquidity_checks.append(liq)
                    if liq.warnings:
                        warnings.extend(f"{symbol} {warning}" for warning in liq.warnings)
                    if liq.skipped:
                        liquidity_skipped_orders.append(liq.to_dict())
                        liquidity_skip_reason = liq.skip_reason
                        trade_qty = 0
                    elif liq.adjusted_quantity < trade_qty:
                        liquidity_adjusted_orders.append(liq.to_dict())
                        trade_qty = liq.adjusted_quantity
                estimate = cm.estimate_buy_cost(price, trade_qty)
                value = estimate["value"]
                raw_value = float(price) * trade_qty
                value_base = fx.convert(value, symbol_currency, day).converted_amount
                commission_base = fx.convert(estimate["commission"], symbol_currency, day).converted_amount
                skip_reason = liquidity_skip_reason or cm.should_skip_order(value)
                cash_needed = value_base + commission_base
                if not skip_reason and cash_needed > cash:
                    affordable_qty = int((cash / fx_conv.rate) // (adjusted_price * (1.0 + cm.commission_rate))) if adjusted_price > 0 and fx_conv.rate > 0 else 0
                    trade_qty = max(0, affordable_qty)
                    estimate = cm.estimate_buy_cost(price, trade_qty)
                    value = estimate["value"]
                    raw_value = float(price) * trade_qty
                    value_base = fx.convert(value, symbol_currency, day).converted_amount
                    commission_base = fx.convert(estimate["commission"], symbol_currency, day).converted_amount
                    skip_reason = cm.should_skip_order(value)
                    cash_needed = value_base + commission_base
                    if cash_needed > cash or trade_qty <= 0:
                        skip_reason = "SKIP_CASH_SHORTAGE"
                if skip_reason:
                    skipped_orders.append({"date": day, "symbol": symbol, "action": action, "order_value": value, "skip_reason": skip_reason})
                    if skip_reason == "SKIP_COST_MIN_ORDER":
                        cost_summary["skipped_by_min_order_count"] += 1
                    elif skip_reason == "SKIP_COST_MAX_ORDER":
                        cost_summary["skipped_by_max_order_count"] += 1
                    elif skip_reason == "SKIP_CASH_SHORTAGE":
                        cost_summary["skipped_by_cash_count"] += 1
                    trade_qty = 0
                elif value >= config.min_trade_value and trade_qty > 0:
                    prev_qty = positions[symbol]
                    prev_cost = avg_cost[symbol] * prev_qty
                    positions[symbol] += trade_qty
                    avg_cost[symbol] = (prev_cost + value) / positions[symbol]
                    entry_date.setdefault(symbol, day)
                    cash -= cash_needed
                    gross_positions[symbol] += trade_qty
                    gross_cash -= fx.convert(raw_value, symbol_currency, day).converted_amount
                    cost_summary["total_commission"] += estimate["commission"]
                    cost_summary["total_tax"] += estimate["tax"]
                    cost_summary["total_slippage_cost"] += estimate["slippage_cost"]
                else:
                    trade_qty = 0
            elif action in {"SELL", "REDUCE"} and config.include_sell_reduce and qty > 0:
                trade_qty = qty if action == "SELL" else max(1, qty // 2)
                liq = check_liquidity(
                    symbol=symbol,
                    day=day,
                    price=price,
                    requested_quantity=trade_qty,
                    prices_by_day=prices_by_day,
                    volumes_by_day=volumes_by_day,
                    config=config.liquidity_config,
                )
                if config.liquidity_config.enabled and trade_qty > 0:
                    liquidity_checks.append(liq)
                    if liq.warnings:
                        warnings.extend(f"{symbol} {warning}" for warning in liq.warnings)
                    if liq.skipped:
                        liquidity_skipped_orders.append(liq.to_dict())
                        liquidity_skip_reason = liq.skip_reason
                        trade_qty = 0
                    elif liq.adjusted_quantity < trade_qty:
                        liquidity_adjusted_orders.append(liq.to_dict())
                        trade_qty = liq.adjusted_quantity
                estimate = cm.estimate_sell_proceeds(price, trade_qty)
                value = estimate["value"]
                raw_value = float(price) * trade_qty
                value_base = fx.convert(value, symbol_currency, day).converted_amount
                cost_base = fx.convert(estimate["commission"] + estimate["tax"], symbol_currency, day).converted_amount
                skip_reason = liquidity_skip_reason or cm.should_skip_order(value)
                if skip_reason:
                    skipped_orders.append({"date": day, "symbol": symbol, "action": action, "order_value": value, "skip_reason": skip_reason})
                    if skip_reason == "SKIP_COST_MIN_ORDER":
                        cost_summary["skipped_by_min_order_count"] += 1
                    elif skip_reason == "SKIP_COST_MAX_ORDER":
                        cost_summary["skipped_by_max_order_count"] += 1
                    trade_qty = 0
                else:
                    cost_summary["total_commission"] += estimate["commission"]
                    cost_summary["total_tax"] += estimate["tax"]
                    cost_summary["total_slippage_cost"] += estimate["slippage_cost"]
                    positions[symbol] -= trade_qty
                    cash += value_base - cost_base
                    gross_positions[symbol] -= trade_qty
                    gross_cash += fx.convert(raw_value, symbol_currency, day).converted_amount
                    realized_pnl = (estimate["adjusted_price"] - avg_cost[symbol]) * trade_qty - estimate["commission"] - estimate["tax"]
                    closed_pnls.append(realized_pnl)
                    holding_days = _days_between(entry_date.get(symbol), day)
                    if positions[symbol] <= 0:
                        positions.pop(symbol, None)
                        avg_cost.pop(symbol, None)
                        entry_date.pop(symbol, None)
                    if gross_positions.get(symbol, 0) <= 0:
                        gross_positions.pop(symbol, None)
            else:
                realized_pnl = 0.0
                holding_days = None
                estimate = {"raw_price": price, "adjusted_price": price, "value": 0.0, "commission": 0.0, "tax": 0.0, "slippage_cost": 0.0, "total_cost": 0.0}
                skip_reason = ""
                liq = LiquidityCheckResult(symbol=symbol, requested_quantity=0, requested_value=0.0, allowed_quantity=None, allowed_value=None, adjusted_quantity=0, daily_volume=None, average_volume=None, daily_value=None, average_daily_value=None, liquidity_limit_pct=None)
            if trade_qty > 0:
                cost_summary["total_trading_cost"] = cost_summary["total_commission"] + cost_summary["total_tax"] + cost_summary["total_slippage_cost"]
                equity_after = _portfolio_value_base(cash, positions, latest_prices, fx, day)
                if risk_off:
                    risk_off_trade_count += 1
                trades.append(
                    ValidationTrade(
                        date=day,
                        symbol=symbol,
                        action=action,
                        quantity=int(trade_qty),
                        price=float(estimate["adjusted_price"]),
                        value=float(value),
                        cash_after=float(cash),
                        equity_after=float(equity_after),
                        reason=reason,
                        realized_pnl=float(realized_pnl) if action in {"SELL", "REDUCE"} else 0.0,
                        holding_days=holding_days if action in {"SELL", "REDUCE"} else None,
                        action_group="exit" if action in {"SELL", "REDUCE"} else "entry",
                        raw_price=float(price),
                        adjusted_price=float(estimate["adjusted_price"]),
                        commission=float(estimate["commission"]),
                        tax=float(estimate["tax"]),
                        slippage_cost=float(estimate["slippage_cost"]),
                        total_cost=float(estimate["total_cost"]),
                        skip_reason="",
                        liquidity_requested_quantity=liq.requested_quantity,
                        liquidity_allowed_quantity=liq.allowed_quantity,
                        liquidity_adjusted_quantity=liq.adjusted_quantity,
                        liquidity_skip_reason=liq.skip_reason,
                        liquidity_warning=";".join(liq.warnings),
                        symbol_currency=symbol_currency,
                        base_currency=fx.base_currency,
                        fx_rate=fx_conv.rate,
                        value_base_currency=fx.convert(value, symbol_currency, day).converted_amount,
                        cost_base_currency=fx.convert(float(estimate["total_cost"]), symbol_currency, day).converted_amount,
                    )
                )
        equity = _portfolio_value_base(cash, positions, latest_prices, fx, day)
        peak = max(peak, equity)
        drawdown = ((equity - peak) / peak) if peak > 0 else 0.0
        positions_value = float(equity - cash)
        pv_by_currency = _position_value_by_currency(positions, latest_prices, fx)
        fx_rates_used = {currency: fx.convert(1.0, currency, day).rate for currency in set(pv_by_currency) | {fx.base_currency}}
        daily_return = ((equity - previous_equity) / previous_equity) if previous_equity > 0 else 0.0
        exposure = (positions_value / equity) if equity > 0 else 0.0
        equity_curve.append(
            EquityPoint(
                date=day,
                cash=float(cash),
                positions_value=positions_value,
                equity=float(equity),
                drawdown=float(drawdown),
                daily_return_pct=float(daily_return),
                drawdown_pct=float(drawdown),
                exposure_pct=float(exposure),
                equity_base_currency=float(equity),
                cash_by_currency={fx.base_currency: float(cash)},
                position_value_by_currency=pv_by_currency,
                fx_rates_used=fx_rates_used,
            )
        )
        previous_equity = equity

    final_equity = equity_curve[-1].equity if equity_curve else cash
    gross_final_equity = _portfolio_value_base(gross_cash, gross_positions, latest_prices, fx, dates[-1] if dates else "")
    liq_summary = liquidity_summary(liquidity_checks, config=config.liquidity_config)
    final_position_by_currency = _position_value_by_currency(positions, latest_prices, fx)
    fx_summary = fx.summary(
        cash_by_currency={fx.base_currency: float(cash)},
        position_value_by_currency=final_position_by_currency,
        final_equity_base=float(final_equity),
    )
    portfolio_risk_result = build_portfolio_risk_result(
        positions=dict(positions),
        latest_prices=latest_prices,
        prices_by_day=prices_by_day,
        config=config.portfolio_risk_config,
    )
    warnings.extend(portfolio_risk_result.risk_warnings)
    action_breakdown, symbol_breakdown = summarize_trades(trades, latest_prices, dict(positions))
    metrics = calculate_metrics(
        initial_cash=float(config.initial_cash),
        final_equity=float(final_equity),
        trades=trades,
        equity_curve=equity_curve,
        closed_trade_pnls=closed_pnls,
        risk_off_trade_count=risk_off_trade_count,
    )
    advanced_metrics = calculate_advanced_metrics(
        initial_cash=float(config.initial_cash),
        final_equity=float(final_equity),
        trades=trades,
        equity_curve=equity_curve,
        risk_free_rate=float(config.risk_free_rate),
        gross_final_equity=float(gross_final_equity),
        total_commission=float(cost_summary["total_commission"]),
        total_tax=float(cost_summary["total_tax"]),
        total_slippage_cost=float(cost_summary["total_slippage_cost"]),
        skipped_by_min_order_count=int(cost_summary["skipped_by_min_order_count"]),
        skipped_by_max_order_count=int(cost_summary["skipped_by_max_order_count"]),
        liquidity_summary=liq_summary,
        fx_summary=fx_summary,
    )
    benchmark = _build_benchmark(
        prices_by_day=prices_by_day,
        symbols=sorted(_symbol_set(config.symbols) or all_symbols),
        initial_cash=float(config.initial_cash),
        cost_model=cm,
    ) if config.benchmark else {
        "available": False,
        "reason": "benchmark disabled",
        "benchmark_costs_applied": bool(cm.enabled),
        "benchmark_total_cost": 0.0,
        "benchmark_net_return_pct": None,
    }
    if benchmark.get("available"):
        benchmark_return = float(benchmark.get("benchmark_return_pct") or 0.0)
        excess = float(advanced_metrics.get("total_return_pct") or 0.0) - benchmark_return
        benchmark["excess_return_pct"] = excess
        benchmark["strategy_vs_benchmark"] = "OUTPERFORM" if excess > 0 else ("UNDERPERFORM" if excess < 0 else "MATCH")
    summary = {
        "start_date": dates[0] if dates else config.start_date,
        "end_date": dates[-1] if dates else config.end_date,
        "symbols": sorted(_symbol_set(config.symbols) or all_symbols),
        "initial_cash": float(config.initial_cash),
        "final_equity": float(final_equity),
        "include_sell_reduce": bool(config.include_sell_reduce),
        "costs_enabled": bool(cm.enabled),
        "data_days": len(dates),
        "trade_count": len(trades),
    }
    cost_summary["total_trading_cost"] = cost_summary["total_commission"] + cost_summary["total_tax"] + cost_summary["total_slippage_cost"]
    from deepsignal.live_trading.ai_recommendation.validation_threshold_tuning import compute_threshold_tuning

    threshold_summary = compute_threshold_tuning(
        prices_by_day=dict(prices_by_day),
        signals=signals,
    )
    threshold_summary.generated_at = datetime.now().isoformat(timespec="microseconds")
    return ValidationResult(
        generated_at=datetime.now().isoformat(timespec="microseconds"),
        summary=summary,
        metrics=metrics,
        advanced_metrics=advanced_metrics,
        benchmark=benchmark,
        cost_model=cm.to_dict(),
        cost_summary=dict(cost_summary),
        skipped_orders=skipped_orders,
        cost_adjusted_metrics={
            "gross_return_pct": advanced_metrics.get("gross_return_pct"),
            "net_return_pct": advanced_metrics.get("net_return_pct"),
            "cost_drag_pct": advanced_metrics.get("cost_drag_pct"),
            "total_trading_cost": cost_summary["total_trading_cost"],
        },
        liquidity_model={
            "config": config.liquidity_config.to_dict(),
            "summary": liq_summary,
            "skipped_orders": liquidity_skipped_orders,
            "adjusted_orders": liquidity_adjusted_orders,
            "warnings": [warning for check in liquidity_checks for warning in check.warnings],
        },
        fx_model={
            "config": fx.config_dict(),
            "summary": fx_summary,
            "currency_exposure": fx_summary.get("currency_exposure", {}),
            "cash_by_currency": fx_summary.get("cash_by_currency", {}),
            "position_value_by_currency": fx_summary.get("position_value_by_currency", {}),
            "warnings": list(fx.warnings),
        },
        portfolio_risk=portfolio_risk_result.to_dict(),
        trades=trades,
        equity_curve=equity_curve,
        action_breakdown=action_breakdown,
        symbol_breakdown=symbol_breakdown,
        warnings=warnings,
        threshold_tuning=threshold_summary.to_dict(),
    )


def write_validation_outputs(result: ValidationResult, output_dir: str | Path = "outputs") -> tuple[Path, Path, Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    dt = datetime.fromisoformat(result.generated_at)
    ts = dt.strftime("%Y%m%d_%H%M%S")
    json_path = root / f"ai_recommendation_validation_{ts}.json"
    if json_path.exists():
        json_path = root / f"ai_recommendation_validation_{dt.strftime('%Y%m%d_%H%M%S_%f')}.json"
    md_path = root / "AI_RECOMMENDATION_VALIDATION.md"
    csv_path = root / "AI_RECOMMENDATION_VALIDATION_TRADES.csv"
    risk_csv_path = root / "AI_RECOMMENDATION_PORTFOLIO_RISK.csv"
    result.output_files.update({"json": json_path.name, "markdown": md_path.name, "trades_csv": csv_path.name, "portfolio_risk_csv": risk_csv_path.name})
    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_validation_markdown(result), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date",
                "symbol",
                "action",
                "quantity",
                "price",
                "value",
                "cash_after",
                "equity_after",
                "reason",
                "realized_pnl",
                "holding_days",
                "action_group",
                "raw_price",
                "adjusted_price",
                "commission",
                "tax",
                "slippage_cost",
                "total_cost",
                "skip_reason",
                "liquidity_requested_quantity",
                "liquidity_allowed_quantity",
                "liquidity_adjusted_quantity",
                "liquidity_skip_reason",
                "liquidity_warning",
                "symbol_currency",
                "base_currency",
                "fx_rate",
                "value_base_currency",
                "cost_base_currency",
            ],
        )
        writer.writeheader()
        for trade in result.trades:
            writer.writerow(
                {
                    "date": trade.date,
                    "symbol": trade.symbol,
                    "action": trade.action,
                    "quantity": trade.quantity,
                    "price": trade.price,
                    "value": trade.value,
                    "cash_after": trade.cash_after,
                    "equity_after": trade.equity_after,
                    "reason": trade.reason,
                    "realized_pnl": trade.realized_pnl,
                    "holding_days": "" if trade.holding_days is None else trade.holding_days,
                    "action_group": trade.action_group,
                    "raw_price": trade.raw_price,
                    "adjusted_price": trade.adjusted_price,
                    "commission": trade.commission,
                    "tax": trade.tax,
                    "slippage_cost": trade.slippage_cost,
                    "total_cost": trade.total_cost,
                    "skip_reason": trade.skip_reason,
                    "liquidity_requested_quantity": trade.liquidity_requested_quantity,
                    "liquidity_allowed_quantity": "" if trade.liquidity_allowed_quantity is None else trade.liquidity_allowed_quantity,
                    "liquidity_adjusted_quantity": trade.liquidity_adjusted_quantity,
                    "liquidity_skip_reason": trade.liquidity_skip_reason,
                    "liquidity_warning": trade.liquidity_warning,
                    "symbol_currency": trade.symbol_currency,
                    "base_currency": trade.base_currency,
                    "fx_rate": trade.fx_rate,
                    "value_base_currency": trade.value_base_currency,
                    "cost_base_currency": trade.cost_base_currency,
                }
            )
    with risk_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["category", "key", "value", "threshold", "severity", "note"])
        writer.writeheader()
        for row in portfolio_risk_csv_rows_for_dict(result.portfolio_risk):
            writer.writerow(row)
    threshold_path: Path | None = None
    if result.threshold_tuning:
        from deepsignal.live_trading.ai_recommendation.validation_threshold_tuning import (
            summary_from_dict,
            write_threshold_summary,
        )

        summary = summary_from_dict(result.threshold_tuning)
        summary.generated_at = result.generated_at
        threshold_path = write_threshold_summary(summary, root, source_validation_json=json_path.name)
        result.output_files["threshold_summary"] = threshold_path.name
    return json_path, md_path, csv_path, risk_csv_path


def portfolio_risk_csv_rows_for_dict(portfolio_risk: dict[str, Any]) -> list[dict[str, Any]]:
    from deepsignal.live_trading.ai_recommendation.portfolio_risk_model import PortfolioRiskResult

    result = PortfolioRiskResult(
        config=dict(portfolio_risk.get("config") or {}),
        symbol_weights=dict(portfolio_risk.get("symbol_weights") or {}),
        sector_weights=dict(portfolio_risk.get("sector_weights") or {}),
        overweight_symbols=list(portfolio_risk.get("overweight_symbols") or []),
        overweight_sectors=list(portfolio_risk.get("overweight_sectors") or []),
        high_correlation_pairs=list(portfolio_risk.get("high_correlation_pairs") or []),
        concentration_score=float(portfolio_risk.get("concentration_score") or 0.0),
        diversification_score=float(portfolio_risk.get("diversification_score") or 0.0),
        severity=str(portfolio_risk.get("severity") or "ok"),
        risk_warnings=list(portfolio_risk.get("risk_warnings") or []),
    )
    return portfolio_risk_csv_rows(result)


def run_ai_recommendation_validation(db_path: str, *, config: ValidationConfig) -> tuple[ValidationResult, Path, Path, Path, Path]:
    result = run_validation(db_path, config=config)
    paths = write_validation_outputs(result, output_dir=config.output_dir)
    return result, *paths


def format_validation_console(result: ValidationResult, json_path: Path, md_path: Path, csv_path: Path, risk_csv_path: Path | None = None) -> str:
    m = result.metrics
    lines = [
        "DeepSignal AI recommendation validation created",
        f"Period: {result.summary.get('start_date')} ~ {result.summary.get('end_date')}",
        f"Trades: {m.get('trade_count')}",
        f"Total Return: {float(result.advanced_metrics.get('total_return_pct') or 0.0) * 100:.2f}%",
        f"Max Drawdown: {float(result.advanced_metrics.get('max_drawdown_pct') or 0.0) * 100:.2f}%",
        f"JSON: {json_path.as_posix()}",
        f"Markdown: {md_path.as_posix()}",
        f"Trades CSV: {csv_path.as_posix()}",
        f"Portfolio Risk CSV: {risk_csv_path.as_posix() if risk_csv_path else '-'}",
    ]
    thr_name = result.output_files.get("threshold_summary")
    if thr_name:
        lines.append(f"Threshold summary: {(json_path.parent / thr_name).as_posix()}")
    if result.threshold_tuning:
        gt = result.threshold_tuning.get("global_threshold")
        if gt is not None:
            lines.append(f"Tuned min_final_score (global): {float(gt):.1f}")
    lines.append("Note: validation is DB read-only and does not call KIS, live-approve, --execute, or paper_* writes.")
    return "\n".join(lines)
