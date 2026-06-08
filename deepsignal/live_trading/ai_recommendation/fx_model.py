"""Local FX conversion helpers for AI recommendation validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass
class FXConfig:
    base_currency: str = "KRW"
    default_symbol_currency: str = "KRW"
    fx_rates_path: str | None = None
    symbol_currency_map_path: str | None = None
    fallback_rates: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FXRateSnapshot:
    date: str
    base_currency: str
    rates: dict[str, float]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FXConversionResult:
    from_currency: str
    to_currency: str
    rate: float
    original_amount: float
    converted_amount: float
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_fallback_rates(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    out: dict[str, float] = {}
    for part in str(raw).split(","):
        if not part.strip() or "=" not in part:
            continue
        key, value = part.split("=", 1)
        try:
            out[key.strip().upper()] = float(value.strip())
        except ValueError:
            continue
    return out


def load_symbol_currency_map(path: str | None) -> dict[str, str]:
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
    return {str(k).strip().upper(): str(v).strip().upper() for k, v in data.items() if str(k).strip() and str(v).strip()}


def load_fx_rates(path: str | None) -> tuple[str | None, dict[str, dict[str, float]]]:
    if not path:
        return None, {}
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return None, {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, {}
    if not isinstance(data, dict):
        return None, {}
    base = str(data.get("base_currency") or "").upper() or None
    rates_raw = data.get("rates") if isinstance(data.get("rates"), dict) else {}
    rates: dict[str, dict[str, float]] = {}
    for day, day_rates in rates_raw.items():
        if not isinstance(day_rates, dict):
            continue
        parsed: dict[str, float] = {}
        for currency, rate in day_rates.items():
            try:
                parsed[str(currency).upper()] = float(rate)
            except (TypeError, ValueError):
                continue
        if parsed:
            rates[str(day)[:10]] = parsed
    return base, rates


class FXModel:
    def __init__(self, config: FXConfig):
        self.config = config
        file_base, rates = load_fx_rates(config.fx_rates_path)
        self.base_currency = (config.base_currency or file_base or "KRW").upper()
        self.default_symbol_currency = (config.default_symbol_currency or self.base_currency).upper()
        self.rates_by_day = rates
        self.symbol_currency_map = load_symbol_currency_map(config.symbol_currency_map_path)
        self.fallback_rates = {self.base_currency: 1.0, **{k.upper(): float(v) for k, v in (config.fallback_rates or {}).items()}}
        self.warnings: list[str] = []
        self.conversion_count = 0
        self.unavailable_count = 0

    def symbol_currency(self, symbol: str) -> str:
        return self.symbol_currency_map.get(str(symbol).upper(), self.default_symbol_currency)

    def rate_for(self, currency: str, day: str) -> FXConversionResult:
        cur = (currency or self.base_currency).upper()
        if cur == self.base_currency:
            return FXConversionResult(cur, self.base_currency, 1.0, 1.0, 1.0)
        candidates = [d for d in sorted(self.rates_by_day) if d <= str(day)[:10]]
        if candidates:
            latest = candidates[-1]
            rate = self.rates_by_day.get(latest, {}).get(cur)
            if rate is not None and rate > 0:
                return FXConversionResult(cur, self.base_currency, float(rate), 1.0, float(rate))
        if cur in self.fallback_rates and self.fallback_rates[cur] > 0:
            warning = f"FX_FALLBACK_USED:{cur}:{day}"
            self.warnings.append(warning)
            return FXConversionResult(cur, self.base_currency, float(self.fallback_rates[cur]), 1.0, float(self.fallback_rates[cur]), warning)
        warning = f"FX_RATE_UNAVAILABLE:{cur}:{day}"
        self.warnings.append(warning)
        self.unavailable_count += 1
        return FXConversionResult(cur, self.base_currency, 1.0, 1.0, 1.0, warning)

    def convert(self, amount: float, from_currency: str, day: str) -> FXConversionResult:
        rate_result = self.rate_for(from_currency, day)
        self.conversion_count += 1
        return FXConversionResult(
            from_currency=rate_result.from_currency,
            to_currency=self.base_currency,
            rate=rate_result.rate,
            original_amount=float(amount),
            converted_amount=float(amount) * rate_result.rate,
            warning=rate_result.warning,
        )

    def summary(
        self,
        *,
        cash_by_currency: dict[str, float],
        position_value_by_currency: dict[str, float],
        final_equity_base: float,
    ) -> dict[str, Any]:
        exposure = {currency: cash_by_currency.get(currency, 0.0) + position_value_by_currency.get(currency, 0.0) for currency in set(cash_by_currency) | set(position_value_by_currency)}
        foreign_value_base = 0.0
        for currency, amount in exposure.items():
            if currency != self.base_currency:
                foreign_value_base += self.convert(amount, currency, "9999-12-31").converted_amount
        return {
            "base_currency": self.base_currency,
            "currency_exposure": exposure,
            "cash_by_currency": dict(cash_by_currency),
            "position_value_by_currency": dict(position_value_by_currency),
            "fx_unavailable_count": int(self.unavailable_count),
            "fx_conversion_count": int(self.conversion_count),
            "fx_impact_estimate": 0.0,
            "foreign_currency_exposure_pct": (foreign_value_base / final_equity_base) if final_equity_base > 0 else 0.0,
        }

    def config_dict(self) -> dict[str, Any]:
        data = self.config.to_dict()
        data["base_currency"] = self.base_currency
        data["default_symbol_currency"] = self.default_symbol_currency
        return data
