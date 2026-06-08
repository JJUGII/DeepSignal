"""Portfolio risk helpers for AI recommendation validation.

All inputs are local validation data. No network or broker lookup is performed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from typing import Any

from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_PR = DEFAULT_ANALYSIS_CONDITIONS.portfolio_risk


@dataclass
class PortfolioRiskConfig:
    max_symbol_weight: float = _PR.max_symbol_weight
    max_sector_weight: float = _PR.max_sector_weight
    high_correlation_threshold: float = _PR.high_correlation_threshold
    lookback_days: int = _PR.correlation_lookback_days
    min_correlation_points: int = _PR.min_correlation_points
    sector_map_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PortfolioRiskResult:
    config: dict[str, Any]
    symbol_weights: dict[str, float]
    sector_weights: dict[str, float]
    overweight_symbols: list[dict[str, Any]] = field(default_factory=list)
    overweight_sectors: list[dict[str, Any]] = field(default_factory=list)
    high_correlation_pairs: list[dict[str, Any]] = field(default_factory=list)
    concentration_score: float = 0.0
    diversification_score: float = 100.0
    severity: str = "ok"
    risk_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_sector_map(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip().upper(): str(v).strip() or "UNKNOWN" for k, v in data.items() if str(k).strip()}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x <= 0 or den_y <= 0:
        return None
    return num / (den_x * den_y)


def _return_series(prices_by_day: dict[str, dict[str, float]], symbols: list[str], lookback_days: int) -> dict[str, dict[str, float]]:
    dates = sorted(prices_by_day)[-max(2, int(lookback_days) + 1) :]
    out: dict[str, dict[str, float]] = {symbol: {} for symbol in symbols}
    for symbol in symbols:
        prev: float | None = None
        for day in dates:
            price = prices_by_day.get(day, {}).get(symbol)
            if price is None or price <= 0:
                continue
            if prev is not None and prev > 0:
                out[symbol][day] = (float(price) - prev) / prev
            prev = float(price)
    return out


def _correlation_pairs(
    prices_by_day: dict[str, dict[str, float]],
    symbols: list[str],
    config: PortfolioRiskConfig,
) -> tuple[list[dict[str, Any]], list[str]]:
    high_pairs: list[dict[str, Any]] = []
    warnings: list[str] = []
    if len(symbols) < 2:
        warnings.append("Correlation unavailable: fewer than two open symbols.")
        return high_pairs, warnings
    returns = _return_series(prices_by_day, symbols, config.lookback_days)
    unavailable = 0
    for i, symbol_a in enumerate(symbols):
        for symbol_b in symbols[i + 1 :]:
            common = sorted(set(returns.get(symbol_a, {})) & set(returns.get(symbol_b, {})))
            if len(common) < int(config.min_correlation_points):
                unavailable += 1
                continue
            corr = _pearson([returns[symbol_a][d] for d in common], [returns[symbol_b][d] for d in common])
            if corr is None:
                unavailable += 1
                continue
            if corr >= float(config.high_correlation_threshold):
                high_pairs.append(
                    {
                        "symbol_a": symbol_a,
                        "symbol_b": symbol_b,
                        "correlation": corr,
                        "points": len(common),
                        "severity": "blocked" if corr >= min(0.99, float(config.high_correlation_threshold) + 0.10) else "warning",
                    }
                )
    if unavailable:
        warnings.append(f"Correlation unavailable for {unavailable} symbol pairs due to insufficient points.")
    return high_pairs, warnings


def build_portfolio_risk_result(
    *,
    positions: dict[str, int],
    latest_prices: dict[str, float],
    prices_by_day: dict[str, dict[str, float]],
    config: PortfolioRiskConfig,
    total_capital: float | None = None,
) -> PortfolioRiskResult:
    sector_map = load_sector_map(config.sector_map_path)
    position_values = {
        str(symbol).upper(): int(qty) * float(latest_prices.get(str(symbol).upper(), 0.0))
        for symbol, qty in positions.items()
        if int(qty) > 0 and float(latest_prices.get(str(symbol).upper(), 0.0)) > 0
    }
    total_value = float(total_capital) if total_capital is not None and float(total_capital) > 0 else sum(position_values.values())
    warnings: list[str] = []
    if total_value <= 0:
        warnings.append("Portfolio risk unavailable: no open positions at validation end.")
        return PortfolioRiskResult(config=config.to_dict(), symbol_weights={}, sector_weights={}, risk_warnings=warnings)

    symbol_weights = {symbol: value / total_value for symbol, value in sorted(position_values.items())}
    sector_values: dict[str, float] = {}
    for symbol, value in position_values.items():
        sector = sector_map.get(symbol, "UNKNOWN")
        sector_values[sector] = sector_values.get(sector, 0.0) + value
    sector_weights = {sector: value / total_value for sector, value in sorted(sector_values.items())}

    overweight_symbols = [
        {
            "symbol": symbol,
            "weight": weight,
            "threshold": float(config.max_symbol_weight),
            "severity": "blocked" if weight >= float(config.max_symbol_weight) * 1.25 else "warning",
        }
        for symbol, weight in symbol_weights.items()
        if weight > float(config.max_symbol_weight)
    ]
    overweight_sectors = [
        {
            "sector": sector,
            "weight": weight,
            "threshold": float(config.max_sector_weight),
            "severity": "blocked" if weight >= float(config.max_sector_weight) * 1.25 else "warning",
        }
        for sector, weight in sector_weights.items()
        if weight > float(config.max_sector_weight)
    ]
    high_pairs, corr_warnings = _correlation_pairs(prices_by_day, sorted(position_values), config)
    warnings.extend(corr_warnings)

    concentration = min(
        100.0,
        len(overweight_symbols) * 20.0 + len(overweight_sectors) * 25.0 + len(high_pairs) * 10.0,
    )
    severity = "blocked" if concentration >= 70 else ("warning" if concentration > 0 or warnings else "ok")
    if overweight_symbols:
        warnings.append("Single-symbol concentration exceeds configured threshold.")
    if overweight_sectors:
        warnings.append("Sector concentration exceeds configured threshold.")
    if high_pairs:
        warnings.append("High-correlation symbol pairs exceed configured threshold.")

    return PortfolioRiskResult(
        config=config.to_dict(),
        symbol_weights=symbol_weights,
        sector_weights=sector_weights,
        overweight_symbols=overweight_symbols,
        overweight_sectors=overweight_sectors,
        high_correlation_pairs=high_pairs,
        concentration_score=concentration,
        diversification_score=max(0.0, 100.0 - concentration),
        severity=severity,
        risk_warnings=warnings,
    )


def portfolio_risk_csv_rows(result: PortfolioRiskResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cfg = result.config
    for symbol, weight in result.symbol_weights.items():
        rows.append({"category": "symbol_weight", "key": symbol, "value": weight, "threshold": cfg.get("max_symbol_weight"), "severity": "ok", "note": ""})
    for sector, weight in result.sector_weights.items():
        rows.append({"category": "sector_weight", "key": sector, "value": weight, "threshold": cfg.get("max_sector_weight"), "severity": "ok", "note": ""})
    for row in result.overweight_symbols:
        rows.append({"category": "overweight_symbol", "key": row["symbol"], "value": row["weight"], "threshold": row["threshold"], "severity": row["severity"], "note": "single symbol weight threshold exceeded"})
    for row in result.overweight_sectors:
        rows.append({"category": "overweight_sector", "key": row["sector"], "value": row["weight"], "threshold": row["threshold"], "severity": row["severity"], "note": "sector weight threshold exceeded"})
    for row in result.high_correlation_pairs:
        rows.append({"category": "high_correlation_pair", "key": f"{row['symbol_a']}:{row['symbol_b']}", "value": row["correlation"], "threshold": cfg.get("high_correlation_threshold"), "severity": row["severity"], "note": f"points={row.get('points')}"})
    rows.append({"category": "score", "key": "concentration_score", "value": result.concentration_score, "threshold": 70, "severity": result.severity, "note": "higher means more concentrated"})
    rows.append({"category": "score", "key": "diversification_score", "value": result.diversification_score, "threshold": 30, "severity": result.severity, "note": "higher means more diversified"})
    for warning in result.risk_warnings:
        rows.append({"category": "warning", "key": "risk_warning", "value": "", "threshold": "", "severity": result.severity, "note": warning})
    return rows
