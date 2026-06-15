"""[Phase5] 배포 검증 게이트 — 셋업이 실거래로 나갈 자격이 있는지 정량 판정.

낙관(반등으로 하락장 수익)을 *추구*하되, 이 게이트가 통과시켜야만 실거래로 나간다.
기준(외부 검토 §9): 표본 200+ / 하락장 구간 net 양수 / PF≥1.2 / MDD 한도 / 연속손실≤5 /
shadow 100+ 양수. 하나라도 미달이면 deploy=False(=계속 paper).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class ValidationResult:
    n: int
    n_downtrend: int
    net_pct_downtrend: float
    profit_factor: float
    mdd_pct: float                 # 음수(낙폭)
    max_consec_losses: int
    shadow_n: int
    shadow_net_pct: float
    deploy: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def max_drawdown_pct(returns_pct: Sequence[float]) -> float:
    """수익률(%) 시퀀스의 누적 자본곡선 최대낙폭(%). 음수 반환."""
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns_pct:
        eq *= (1.0 + float(r) / 100.0)
        peak = max(peak, eq)
        dd = (eq / peak - 1.0) * 100.0
        mdd = min(mdd, dd)
    return mdd


def max_consecutive_losses(returns_pct: Sequence[float]) -> int:
    cur = best = 0
    for r in returns_pct:
        if float(r) <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _profit_factor(returns_pct: Sequence[float]) -> float:
    gains = sum(r for r in returns_pct if r > 0)
    losses = abs(sum(r for r in returns_pct if r <= 0))
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def validate_strategy(
    trades: Sequence[dict],
    *,
    min_n: int = 200,
    min_pf: float = 1.2,
    max_mdd_pct: float = -15.0,
    max_consec: int = 5,
    min_shadow: int = 100,
) -> ValidationResult:
    """trades: [{regime, net_return_pct, is_shadow(bool)}, ...] → 배포 판정.

    하락장(DOWN_TREND/PANIC) 거래만 별도 집계해 net·PF·MDD·연속손실을 본다.
    """
    dn = [t for t in trades if str(t.get("regime")) in ("DOWN_TREND", "PANIC")]
    dn_ret = [float(t.get("net_return_pct", 0) or 0) for t in dn]
    shadow = [float(t.get("net_return_pct", 0) or 0) for t in trades if t.get("is_shadow")]

    net = sum(dn_ret)
    pf = _profit_factor(dn_ret)
    mdd = max_drawdown_pct(dn_ret)
    consec = max_consecutive_losses(dn_ret)
    shadow_net = sum(shadow)

    reasons: list[str] = []
    ok_n = len(dn) >= min_n
    if not ok_n:
        reasons.append(f"하락장 표본 {len(dn)} < {min_n}")
    ok_net = net > 0
    if not ok_net:
        reasons.append(f"하락장 net {net:+.2f}% ≤ 0")
    ok_pf = pf >= min_pf
    if not ok_pf:
        reasons.append(f"PF {pf:.2f} < {min_pf}")
    ok_mdd = mdd >= max_mdd_pct
    if not ok_mdd:
        reasons.append(f"MDD {mdd:.1f}% < 한도 {max_mdd_pct}%")
    ok_consec = consec <= max_consec
    if not ok_consec:
        reasons.append(f"연속손실 {consec} > {max_consec}")
    ok_shadow = len(shadow) >= min_shadow and shadow_net > 0
    if not ok_shadow:
        reasons.append(f"shadow {len(shadow)}건 net {shadow_net:+.2f}% (기준 {min_shadow}건·양수)")

    deploy = all([ok_n, ok_net, ok_pf, ok_mdd, ok_consec, ok_shadow])
    if deploy:
        reasons.append("전 기준 통과 — 배포 가능")
    return ValidationResult(
        n=len(trades), n_downtrend=len(dn), net_pct_downtrend=round(net, 3),
        profit_factor=round(pf, 3) if pf != float("inf") else 999.0,
        mdd_pct=round(mdd, 2), max_consec_losses=consec,
        shadow_n=len(shadow), shadow_net_pct=round(shadow_net, 3),
        deploy=deploy, reasons=reasons,
    )
