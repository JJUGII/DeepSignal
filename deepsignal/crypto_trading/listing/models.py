"""Cross-exchange listing watch — dataclasses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ListingCandidate:
    market: str
    display_name: str
    exchange_side: str  # bithumb_only | upbit_only | both
    trade_price: float = 0.0
    signed_change_rate: float = 0.0
    acc_trade_price_24h: float = 0.0
    vol_ratio: float | None = None
    chg_1h_pct: float | None = None
    anomaly_score: float = 0.0
    tags: list[str] = field(default_factory=list)
    is_new: bool = False
    new_on: str = ""  # bithumb | upbit | ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ListingScanResult:
    generated_at: str
    upbit_total: int
    bithumb_total: int
    both_count: int
    bithumb_only_count: int
    upbit_only_count: int
    new_on_bithumb: list[str]
    new_on_upbit: list[str]
    candidates: list[ListingCandidate]
    alerts_sent: list[str] = field(default_factory=list)
    disclaimer: str = (
        "조회·알림 전용입니다. 자동 매수 없음. 상장·급등 신호는 오탐이 많으며 투자 판단은 사용자 책임입니다."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "upbit_total": self.upbit_total,
            "bithumb_total": self.bithumb_total,
            "both_count": self.both_count,
            "bithumb_only_count": self.bithumb_only_count,
            "upbit_only_count": self.upbit_only_count,
            "new_on_bithumb": list(self.new_on_bithumb),
            "new_on_upbit": list(self.new_on_upbit),
            "candidates": [c.to_dict() for c in self.candidates],
            "alerts_sent": list(self.alerts_sent),
            "disclaimer": self.disclaimer,
        }
