"""IPS형 단일 종목 비중(기본 5%) 자동 검사."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS


@dataclass
class ConcentrationItem:
    symbol: str
    weight: float
    market_value: float
    severity: str
    message: str


@dataclass
class ConcentrationCheckResult:
    status: str
    cap_fraction: float
    total_equity: float | None
    items: list[ConcentrationItem] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "cap_fraction": self.cap_fraction,
            "total_equity": self.total_equity,
            "items": [
                {
                    "symbol": i.symbol,
                    "weight": i.weight,
                    "market_value": i.market_value,
                    "severity": i.severity,
                    "message": i.message,
                }
                for i in self.items
            ],
            "alerts": list(self.alerts),
            "warnings": list(self.warnings),
        }


def check_position_concentration(
    positions: list[Mapping[str, Any] | Any],
    *,
    total_equity: float | None,
    cap_fraction: float | None = None,
) -> ConcentrationCheckResult:
    """포지션 시장가치 / 총자산 대비 단일 종목 비중 검사."""
    cap = float(
        cap_fraction
        if cap_fraction is not None
        else DEFAULT_ANALYSIS_CONDITIONS.portfolio.advisory_concentrated_name_cap
    )
    if total_equity is None or total_equity <= 0:
        return ConcentrationCheckResult(
            status="NO_DATA",
            cap_fraction=cap,
            total_equity=total_equity,
            warnings=["total_equity missing or zero — concentration check skipped"],
        )

    items: list[ConcentrationItem] = []
    alerts: list[str] = []
    warnings: list[str] = []
    worst = "OK"

    for pos in positions:
        if isinstance(pos, Mapping):
            sym = str(pos.get("symbol") or "").strip()
            qty = int(pos.get("quantity") or 0)
            mv = pos.get("market_value")
        else:
            sym = str(getattr(pos, "symbol", "") or "").strip()
            qty = int(getattr(pos, "quantity", 0) or 0)
            mv = getattr(pos, "market_value", None)
        if qty <= 0 or not sym:
            continue
        try:
            market_value = float(mv or 0)
        except (TypeError, ValueError):
            market_value = 0.0
        if market_value <= 0:
            continue
        weight = market_value / float(total_equity)
        if weight > cap * 1.5:
            severity = "alert"
            worst = "ALERT"
            msg = f"{sym} weight {weight:.2%} exceeds IPS cap {cap:.2%} (severe)"
            alerts.append(msg)
        elif weight > cap:
            severity = "warning"
            if worst == "OK":
                worst = "WARNING"
            msg = f"{sym} weight {weight:.2%} exceeds IPS advisory cap {cap:.2%}"
            warnings.append(msg)
        else:
            severity = "ok"
            msg = f"{sym} weight {weight:.2%} within cap"
        items.append(
            ConcentrationItem(
                symbol=sym,
                weight=weight,
                market_value=market_value,
                severity=severity,
                message=msg,
            )
        )

    if not items:
        return ConcentrationCheckResult(
            status="NO_DATA",
            cap_fraction=cap,
            total_equity=total_equity,
            warnings=["no positions with market_value > 0"],
        )

    return ConcentrationCheckResult(
        status=worst,
        cap_fraction=cap,
        total_equity=total_equity,
        items=sorted(items, key=lambda x: x.weight, reverse=True),
        alerts=alerts,
        warnings=warnings,
    )


def load_real_concentration_from_db(
    db_path: str,
    *,
    broker: str = "kis",
) -> ConcentrationCheckResult:
    """최신 real_account_snapshots + real_positions 기준 집중도."""
    from deepsignal.storage.database import load_latest_real_account_snapshot, load_latest_real_positions

    snap = load_latest_real_account_snapshot(db_path, broker=broker)
    positions = load_latest_real_positions(db_path, broker=broker)
    equity = None
    if snap:
        equity = snap.get("total_equity")
        if equity is None and snap.get("cash") is not None:
            try:
                equity = float(snap["cash"]) + float(snap.get("total_market_value") or 0)
            except (TypeError, ValueError):
                equity = None
    return check_position_concentration(positions, total_equity=equity)
