"""Liquidity constraint helpers for AI recommendation validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LiquidityConfig:
    liquidity_limit_pct: float | None = None
    min_daily_volume: float | None = None
    min_daily_value: float | None = None
    volume_lookback_days: int = 20
    use_average_volume: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def enabled(self) -> bool:
        return self.liquidity_limit_pct is not None or self.min_daily_volume is not None or self.min_daily_value is not None


@dataclass
class LiquidityCheckResult:
    symbol: str
    requested_quantity: int
    requested_value: float
    allowed_quantity: int | None
    allowed_value: float | None
    adjusted_quantity: int
    daily_volume: float | None
    average_volume: float | None
    daily_value: float | None
    average_daily_value: float | None
    liquidity_limit_pct: float | None
    skipped: bool = False
    skip_reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def average_volume_for_symbol(
    *,
    volumes_by_day: dict[str, dict[str, float | None]],
    symbol: str,
    day: str,
    lookback_days: int,
) -> float | None:
    dates = [d for d in sorted(volumes_by_day) if d <= day]
    values: list[float] = []
    for d in dates[-max(1, int(lookback_days)) :]:
        value = volumes_by_day.get(d, {}).get(symbol)
        if value is None or float(value) <= 0:
            continue
        values.append(float(value))
    return (sum(values) / len(values)) if values else None


def average_daily_value_for_symbol(
    *,
    prices_by_day: dict[str, dict[str, float]],
    volumes_by_day: dict[str, dict[str, float | None]],
    symbol: str,
    day: str,
    lookback_days: int,
) -> float | None:
    dates = [d for d in sorted(prices_by_day) if d <= day]
    values: list[float] = []
    for d in dates[-max(1, int(lookback_days)) :]:
        price = prices_by_day.get(d, {}).get(symbol)
        volume = volumes_by_day.get(d, {}).get(symbol)
        if price is None or volume is None or float(price) <= 0 or float(volume) <= 0:
            continue
        values.append(float(price) * float(volume))
    return (sum(values) / len(values)) if values else None


def check_liquidity(
    *,
    symbol: str,
    day: str,
    price: float,
    requested_quantity: int,
    prices_by_day: dict[str, dict[str, float]],
    volumes_by_day: dict[str, dict[str, float | None]],
    config: LiquidityConfig,
) -> LiquidityCheckResult:
    requested_qty = max(0, int(requested_quantity))
    requested_value = requested_qty * float(price)
    daily_volume = volumes_by_day.get(day, {}).get(symbol)
    avg_volume = average_volume_for_symbol(volumes_by_day=volumes_by_day, symbol=symbol, day=day, lookback_days=config.volume_lookback_days)
    daily_value = (float(price) * float(daily_volume)) if daily_volume is not None and float(daily_volume) > 0 else None
    avg_daily_value = average_daily_value_for_symbol(
        prices_by_day=prices_by_day,
        volumes_by_day=volumes_by_day,
        symbol=symbol,
        day=day,
        lookback_days=config.volume_lookback_days,
    )
    result = LiquidityCheckResult(
        symbol=symbol,
        requested_quantity=requested_qty,
        requested_value=requested_value,
        allowed_quantity=None,
        allowed_value=None,
        adjusted_quantity=requested_qty,
        daily_volume=float(daily_volume) if daily_volume is not None else None,
        average_volume=avg_volume,
        daily_value=daily_value,
        average_daily_value=avg_daily_value,
        liquidity_limit_pct=config.liquidity_limit_pct,
    )
    if not config.enabled or requested_qty <= 0:
        return result
    basis_volume = avg_volume if config.use_average_volume else (float(daily_volume) if daily_volume is not None and float(daily_volume) > 0 else None)
    basis_value = avg_daily_value if config.use_average_volume else daily_value
    if basis_volume is None or basis_volume <= 0:
        result.warnings.append("LIQUIDITY_VOLUME_UNAVAILABLE")
        return result
    if config.min_daily_volume is not None and basis_volume < float(config.min_daily_volume):
        result.skipped = True
        result.skip_reason = "SKIP_LOW_VOLUME"
        result.adjusted_quantity = 0
        return result
    if config.min_daily_value is not None and (basis_value is None or basis_value < float(config.min_daily_value)):
        result.skipped = True
        result.skip_reason = "SKIP_LOW_DAILY_VALUE"
        result.adjusted_quantity = 0
        return result
    if config.liquidity_limit_pct is not None:
        allowed = int(max(0.0, basis_volume * float(config.liquidity_limit_pct)))
        result.allowed_quantity = allowed
        result.allowed_value = allowed * float(price)
        if requested_qty > allowed:
            result.adjusted_quantity = allowed
            if allowed <= 0:
                result.skipped = True
                result.skip_reason = "SKIP_LIQUIDITY_LIMIT"
    return result


def liquidity_summary(
    checks: list[LiquidityCheckResult],
    *,
    config: LiquidityConfig,
) -> dict[str, Any]:
    adjusted = [c for c in checks if not c.skipped and c.adjusted_quantity < c.requested_quantity]
    skipped = [c for c in checks if c.skipped]
    unavailable = [c for c in checks if c.warnings]
    reduced_value = sum(max(0.0, c.requested_value - (c.adjusted_quantity * (c.requested_value / c.requested_quantity if c.requested_quantity else 0.0))) for c in adjusted)
    return {
        "enabled": bool(config.enabled),
        "check_count": len(checks),
        "liquidity_adjusted_trade_count": len(adjusted),
        "skipped_by_liquidity_count": len(skipped),
        "adjusted_by_liquidity_count": len(adjusted),
        "total_liquidity_reduced_value": reduced_value,
        "liquidity_unavailable_count": len(unavailable),
    }
