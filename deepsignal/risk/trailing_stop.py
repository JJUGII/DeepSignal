"""트레일링 스톱 + 레버리지용 리스크 축소 (P1).

레버리지·고속 매매에서 이익을 보호하고 손실을 제한하기 위한 순수 함수 모음.
- 트레일링 스톱: 고점 대비 일정 % 하락 시 청산
- 레버리지 캡 축소: 배율이 클수록 1회·일일 한도와 손실한도를 비례 축소
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrailingState:
    entry_price: float
    peak_price: float          # 보유 중 최고가
    trail_pct: float           # 고점 대비 허용 하락 % (예: 0.08 = 8%)
    hard_stop_pct: float = 0.0 # 진입가 대비 절대 손절 % (0이면 미적용)

    def update(self, price: float) -> "TrailingState":
        if price > self.peak_price:
            self.peak_price = price
        return self

    def should_exit(self, price: float) -> tuple[bool, str]:
        """청산해야 하면 (True, 사유)."""
        if self.hard_stop_pct > 0 and self.entry_price > 0:
            if price <= self.entry_price * (1 - self.hard_stop_pct):
                return True, f"하드손절 -{self.hard_stop_pct*100:.0f}%"
        if self.peak_price > 0 and price <= self.peak_price * (1 - self.trail_pct):
            drop = (self.peak_price - price) / self.peak_price * 100
            return True, f"트레일링스톱 (고점 -{drop:.1f}%)"
        return False, ""


def trailing_pct_for_leverage(leverage: float, base_trail: float = 0.10) -> float:
    """배율이 클수록 트레일링 폭을 좁혀 빠르게 보호. 2x→base/2, 3x→base/3 (하한 3%)."""
    lev = max(1.0, abs(leverage))
    return max(0.03, base_trail / lev)


def scale_caps_for_leverage(
    leverage: float,
    base_single_cap: float,
    base_daily_loss_limit: float,
) -> tuple[float, float]:
    """배율 비례로 1회 주문 한도와 일일 손실 한도를 축소.

    노출이 L배면 같은 금액도 L배 위험 → 주문금액을 1/L로 줄이고
    손실한도도 보수적으로 1/sqrt(L) 로 축소.
    반환: (조정된 1회 한도, 조정된 일일 손실한도)
    """
    lev = max(1.0, abs(leverage))
    single = base_single_cap / lev
    daily = base_daily_loss_limit / (lev ** 0.5)
    return round(single), round(daily)
