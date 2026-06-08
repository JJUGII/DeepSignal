"""Metrics helpers for AI recommendation validation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import math
import statistics
from typing import Any

from deepsignal.live_trading.ai_recommendation.validation_model import EquityPoint, ValidationTrade


def max_drawdown(equity_values: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in equity_values:
        peak = max(peak, float(value))
        if peak <= 0:
            continue
        dd = (float(value) - peak) / peak
        worst = min(worst, dd)
    return worst


def max_drawdown_detail(equity_curve: list[EquityPoint]) -> tuple[float, str | None, str | None]:
    peak = 0.0
    peak_date: str | None = None
    worst = 0.0
    start: str | None = None
    end: str | None = None
    for point in equity_curve:
        value = float(point.equity)
        if value > peak:
            peak = value
            peak_date = point.date
        if peak <= 0:
            continue
        dd = (value - peak) / peak
        if dd < worst:
            worst = dd
            start = peak_date
            end = point.date
    return worst, start, end


def _max_streak(values: list[float], *, wins: bool) -> int:
    best = 0
    cur = 0
    for value in values:
        ok = value > 0 if wins else value < 0
        cur = cur + 1 if ok else 0
        best = max(best, cur)
    return best


def _days_between(start: str | None, end: str | None) -> int:
    try:
        if not start or not end:
            return 0
        return max(0, (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days)
    except ValueError:
        return 0


def summarize_trades(trades: list[ValidationTrade], final_prices: dict[str, float], positions: dict[str, int]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    action_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "buy_value": 0.0, "sell_value": 0.0, "quantity": 0, "realized_pnl": 0.0})
    symbol_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "buy_value": 0.0, "sell_value": 0.0, "quantity": 0, "realized_pnl": 0.0, "open_quantity": 0, "open_value": 0.0})
    for trade in trades:
        a = action_stats[trade.action]
        s = symbol_stats[trade.symbol]
        a["trades"] += 1
        s["trades"] += 1
        a["quantity"] += int(trade.quantity)
        s["quantity"] += int(trade.quantity)
        a["realized_pnl"] += float(trade.realized_pnl)
        s["realized_pnl"] += float(trade.realized_pnl)
        if trade.action in {"BUY", "INCREASE"}:
            a["buy_value"] += float(trade.value)
            s["buy_value"] += float(trade.value)
        elif trade.action in {"SELL", "REDUCE"}:
            a["sell_value"] += float(trade.value)
            s["sell_value"] += float(trade.value)
    for symbol, qty in positions.items():
        price = float(final_prices.get(symbol, 0.0))
        symbol_stats[symbol]["open_quantity"] = int(qty)
        symbol_stats[symbol]["open_value"] = int(qty) * price
    return dict(action_stats), dict(symbol_stats)


def calculate_metrics(
    *,
    initial_cash: float,
    final_equity: float,
    trades: list[ValidationTrade],
    equity_curve: list[EquityPoint],
    closed_trade_pnls: list[float],
    risk_off_trade_count: int,
) -> dict[str, Any]:
    total_return = (float(final_equity) - float(initial_cash)) / float(initial_cash) if initial_cash > 0 else 0.0
    wins = [p for p in closed_trade_pnls if p > 0]
    losses = [p for p in closed_trade_pnls if p < 0]
    return {
        "initial_cash": float(initial_cash),
        "final_equity": float(final_equity),
        "total_return": total_return,
        "max_drawdown": max_drawdown([p.equity for p in equity_curve]),
        "trade_count": len(trades),
        "closed_trade_count": len(closed_trade_pnls),
        "win_rate": (len(wins) / len(closed_trade_pnls)) if closed_trade_pnls else 0.0,
        "average_profit": (sum(wins) / len(wins)) if wins else 0.0,
        "average_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "risk_off_trade_count": int(risk_off_trade_count),
    }


def calculate_advanced_metrics(
    *,
    initial_cash: float,
    final_equity: float,
    trades: list[ValidationTrade],
    equity_curve: list[EquityPoint],
    risk_free_rate: float = 0.0,
    gross_final_equity: float | None = None,
    total_commission: float = 0.0,
    total_tax: float = 0.0,
    total_slippage_cost: float = 0.0,
    skipped_by_min_order_count: int = 0,
    skipped_by_max_order_count: int = 0,
    liquidity_summary: dict[str, Any] | None = None,
    fx_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total_return = (float(final_equity) - float(initial_cash)) / float(initial_cash) if initial_cash > 0 else 0.0
    gross_equity = float(gross_final_equity) if gross_final_equity is not None else float(final_equity)
    gross_return = (gross_equity - float(initial_cash)) / float(initial_cash) if initial_cash > 0 else total_return
    total_trading_cost = float(total_commission) + float(total_tax) + float(total_slippage_cost)
    days = _days_between(equity_curve[0].date if equity_curve else None, equity_curve[-1].date if equity_curve else None)
    annualized = ((1.0 + total_return) ** (365.0 / days) - 1.0) if days > 0 and total_return > -1.0 else 0.0
    daily_returns = [float(p.daily_return_pct) for p in equity_curve[1:] if p.daily_return_pct is not None]
    volatility = statistics.pstdev(daily_returns) * math.sqrt(252.0) if len(daily_returns) >= 2 else 0.0
    avg_daily = statistics.mean(daily_returns) if daily_returns else 0.0
    daily_rf = float(risk_free_rate) / 252.0
    sharpe = ((avg_daily - daily_rf) / statistics.pstdev(daily_returns) * math.sqrt(252.0)) if len(daily_returns) >= 2 and statistics.pstdev(daily_returns) > 0 else None
    pnl_values = [float(t.realized_pnl) for t in trades if t.action in {"SELL", "REDUCE"}]
    wins = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    exposure_values = [float(p.exposure_pct) for p in equity_curve if p.equity > 0]
    turnover = sum(float(t.value) for t in trades) / float(initial_cash) if initial_cash > 0 else 0.0
    holding_days = [int(t.holding_days) for t in trades if t.holding_days is not None]
    best = max((t for t in trades if t.action in {"SELL", "REDUCE"}), key=lambda t: t.realized_pnl, default=None)
    worst = min((t for t in trades if t.action in {"SELL", "REDUCE"}), key=lambda t: t.realized_pnl, default=None)
    dd, dd_start, dd_end = max_drawdown_detail(equity_curve)
    trade_count_by_action: dict[str, int] = defaultdict(int)
    pnl_by_action: dict[str, float] = defaultdict(float)
    pnl_by_symbol: dict[str, float] = defaultdict(float)
    for trade in trades:
        trade_count_by_action[trade.action] += 1
        pnl_by_action[trade.action] += float(trade.realized_pnl)
        pnl_by_symbol[trade.symbol] += float(trade.realized_pnl)
    liq = liquidity_summary or {}
    fx = fx_summary or {}
    return {
        "total_return_pct": total_return,
        "gross_return_pct": gross_return,
        "net_return_pct": total_return,
        "annualized_return_pct": annualized,
        "volatility_pct": volatility,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": dd,
        "max_drawdown_start": dd_start,
        "max_drawdown_end": dd_end,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "expectancy": (sum(pnl_values) / len(pnl_values)) if pnl_values else 0.0,
        "average_win": (sum(wins) / len(wins)) if wins else 0.0,
        "average_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "win_rate": (len(wins) / len(pnl_values)) if pnl_values else 0.0,
        "loss_rate": (len(losses) / len(pnl_values)) if pnl_values else 0.0,
        "consecutive_wins_max": _max_streak(pnl_values, wins=True),
        "consecutive_losses_max": _max_streak(pnl_values, wins=False),
        "exposure_ratio": (sum(exposure_values) / len(exposure_values)) if exposure_values else 0.0,
        "turnover_ratio": turnover,
        "average_holding_days": (sum(holding_days) / len(holding_days)) if holding_days else 0.0,
        "best_trade": None if best is None else {"date": best.date, "symbol": best.symbol, "action": best.action, "realized_pnl": best.realized_pnl},
        "worst_trade": None if worst is None else {"date": worst.date, "symbol": worst.symbol, "action": worst.action, "realized_pnl": worst.realized_pnl},
        "trade_count_by_action": dict(trade_count_by_action),
        "pnl_by_action": dict(pnl_by_action),
        "pnl_by_symbol": dict(pnl_by_symbol),
        "total_commission": float(total_commission),
        "total_tax": float(total_tax),
        "total_slippage_cost": float(total_slippage_cost),
        "total_trading_cost": total_trading_cost,
        "cost_drag_pct": (gross_return - total_return),
        "skipped_by_min_order_count": int(skipped_by_min_order_count),
        "skipped_by_max_order_count": int(skipped_by_max_order_count),
        "average_cost_per_trade": (total_trading_cost / len(trades)) if trades else 0.0,
        "net_profit_factor": (gross_profit / (gross_loss + total_trading_cost)) if (gross_loss + total_trading_cost) > 0 else None,
        "liquidity_adjusted_trade_count": int(liq.get("liquidity_adjusted_trade_count", 0) or 0),
        "skipped_by_liquidity_count": int(liq.get("skipped_by_liquidity_count", 0) or 0),
        "adjusted_by_liquidity_count": int(liq.get("adjusted_by_liquidity_count", 0) or 0),
        "total_liquidity_reduced_value": float(liq.get("total_liquidity_reduced_value", 0.0) or 0.0),
        "liquidity_unavailable_count": int(liq.get("liquidity_unavailable_count", 0) or 0),
        "base_currency": fx.get("base_currency"),
        "currency_exposure": fx.get("currency_exposure", {}),
        "cash_by_currency": fx.get("cash_by_currency", {}),
        "position_value_by_currency": fx.get("position_value_by_currency", {}),
        "fx_unavailable_count": int(fx.get("fx_unavailable_count", 0) or 0),
        "fx_conversion_count": int(fx.get("fx_conversion_count", 0) or 0),
        "fx_impact_estimate": float(fx.get("fx_impact_estimate", 0.0) or 0.0),
        "foreign_currency_exposure_pct": float(fx.get("foreign_currency_exposure_pct", 0.0) or 0.0),
    }
