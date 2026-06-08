"""Deterministic cost model for AI recommendation validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class CostModel:
    commission_rate: float = 0.001
    tax_rate: float = 0.0
    slippage_bps: float = 5.0
    min_order_value: float = 10_000.0
    max_order_value: float | None = None
    liquidity_limit_pct: float | None = None
    apply_tax_on_sell_only: bool = True
    currency: str = "KRW"
    enabled: bool = True

    @classmethod
    def no_costs(cls, *, currency: str = "KRW") -> "CostModel":
        return cls(
            commission_rate=0.0,
            tax_rate=0.0,
            slippage_bps=0.0,
            min_order_value=0.0,
            max_order_value=None,
            liquidity_limit_pct=None,
            apply_tax_on_sell_only=True,
            currency=currency,
            enabled=False,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def adjusted_buy_price(self, price: float) -> float:
        return float(price) * (1.0 + max(0.0, float(self.slippage_bps)) / 10_000.0)

    def adjusted_sell_price(self, price: float) -> float:
        return max(0.0, float(price) * (1.0 - max(0.0, float(self.slippage_bps)) / 10_000.0))

    def should_skip_order(self, order_value: float) -> str | None:
        value = float(order_value)
        if value <= 0:
            return "SKIP_ZERO_ORDER_VALUE"
        if value < max(0.0, float(self.min_order_value)):
            return "SKIP_COST_MIN_ORDER"
        if self.max_order_value is not None and value > float(self.max_order_value):
            return "SKIP_COST_MAX_ORDER"
        return None

    def estimate_buy_cost(self, price: float, quantity: int) -> dict[str, float]:
        qty = max(0, int(quantity))
        raw_price = float(price)
        adjusted = self.adjusted_buy_price(raw_price)
        raw_value = raw_price * qty
        value = adjusted * qty
        commission = value * max(0.0, float(self.commission_rate))
        slippage_cost = max(0.0, value - raw_value)
        return {
            "raw_price": raw_price,
            "adjusted_price": adjusted,
            "value": value,
            "commission": commission,
            "tax": 0.0,
            "slippage_cost": slippage_cost,
            "total_cost": commission + slippage_cost,
            "cash_delta": -(value + commission),
        }

    def estimate_sell_proceeds(self, price: float, quantity: int) -> dict[str, float]:
        qty = max(0, int(quantity))
        raw_price = float(price)
        adjusted = self.adjusted_sell_price(raw_price)
        raw_value = raw_price * qty
        value = adjusted * qty
        commission = value * max(0.0, float(self.commission_rate))
        tax = value * max(0.0, float(self.tax_rate)) if self.apply_tax_on_sell_only else raw_value * max(0.0, float(self.tax_rate))
        slippage_cost = max(0.0, raw_value - value)
        return {
            "raw_price": raw_price,
            "adjusted_price": adjusted,
            "value": value,
            "commission": commission,
            "tax": tax,
            "slippage_cost": slippage_cost,
            "total_cost": commission + tax + slippage_cost,
            "cash_delta": value - commission - tax,
        }

    def explain_costs(self, order: dict[str, Any]) -> str:
        return (
            f"commission_rate={self.commission_rate}, tax_rate={self.tax_rate}, "
            f"slippage_bps={self.slippage_bps}, min_order_value={self.min_order_value}, "
            f"max_order_value={self.max_order_value}, order_value={order.get('value')}"
        )
