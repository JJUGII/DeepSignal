"""Binary labels: forward return after N minutes vs fee+slippage hurdle."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScalpLabelConfig:
    """Label definition for short-horizon entry quality."""

    horizon_minutes: int = 5
    cost_pct: float = 0.2
    """Round-trip cost hurdle in percent points (0.2 => 0.2%)."""

    @property
    def hurdle_fraction(self) -> float:
        return float(self.cost_pct) / 100.0

    def label_from_prices(self, entry_price: float, exit_price: float) -> int | None:
        if entry_price <= 0 or exit_price <= 0:
            return None
        fwd = (exit_price / entry_price) - 1.0
        return 1 if fwd > self.hurdle_fraction else 0

    def forward_return(self, entry_price: float, exit_price: float) -> float | None:
        if entry_price <= 0 or exit_price <= 0:
            return None
        return (exit_price / entry_price) - 1.0
