"""포트폴리오 배분 모델 (분석·보조용, 실주문 없음)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PortfolioAllocation:
    symbol: str
    final_score: float | None
    target_weight: float
    """전체 자본(`PortfolioSnapshot.total_cash`) 대비 목표 비중(0~1)."""
    target_amount: float
    rationale: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioSnapshot:
    analyzed_at: str
    total_cash: float
    market_regime: str
    allocations: list[PortfolioAllocation]
    raw: dict[str, Any] = field(default_factory=dict)
