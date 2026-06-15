"""[Phase5] 하락장 반등 단타 오케스트레이터.

전 Phase를 한 결정 함수로 묶는다:
  레짐 판정(Phase2) → UP/RANGE면 일반 엔진에 위임 / DOWN·PANIC이면:
    ① 반등 셋업(Phase3) 발화 필요
    ② 점수 ≥ 장세별 문턱(Phase4)
    ③ EV 게이트(Phase4) 통과
  셋 다 충족해야 매수 허용 — 아니면 차단(=대부분 안 산다).

순수 함수: 라이브 배선은 이 결정을 호출만 하면 된다(기본 off, env DOWNTREND_ENGINE_ENABLED).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from deepsignal.crypto_trading.downtrend.ev_gate import TradeStats, ev_allows_buy, regime_min_score
from deepsignal.crypto_trading.downtrend.regime import Regime, allows_new_long_by_default
from deepsignal.crypto_trading.downtrend.setups import SetupFeatures, detect_setups


@dataclass
class DowntrendDecision:
    allow: bool                       # 매수 허용(이 엔진 관할일 때)
    defer: bool                       # True면 이 엔진 관할 아님(UP/RANGE) — 일반 엔진이 처리
    regime: str
    setups: list[str] = field(default_factory=list)
    ev_pct: float = 0.0
    score: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def downtrend_engine_enabled(env: dict[str, str] | None = None) -> bool:
    e = env if env is not None else os.environ
    return str(e.get("DOWNTREND_ENGINE_ENABLED", "false")).strip().lower() in ("1", "true", "yes", "on")


def decide_downtrend_buy(
    regime: Regime | str,
    setup_features: SetupFeatures,
    stats: TradeStats,
    score: float,
    *,
    allow_panic: bool = False,
    min_ev_pct: float = 0.15,
    min_win_rate: float = 0.45,
) -> DowntrendDecision:
    """하락장 신규 롱 결정.

    Args:
        regime: Phase2 판정 결과.
        setup_features: Phase3 셋업 입력.
        stats: 해당 셋업의 실측 통계(Phase4 EV용).
        score: 종목 점수(0~100 또는 동등 스케일).
        allow_panic: PANIC에서도 (셋업+EV 충족 시) 마이크로 반등 허용 여부. 기본 차단.
    """
    rg = regime.value if isinstance(regime, Regime) else str(regime)

    # UP/RANGE — 이 엔진 관할 아님(일반 모멘텀/평균회귀 엔진이 처리)
    if allows_new_long_by_default(Regime(rg) if rg in Regime.__members__ else Regime.RANGE):
        return DowntrendDecision(allow=False, defer=True, regime=rg, score=score,
                                 reason="비하락장 — 일반 엔진 위임")

    # PANIC — 기본 무매매
    if rg == Regime.PANIC.value and not allow_panic:
        return DowntrendDecision(allow=False, defer=False, regime=rg, score=score,
                                 reason="PANIC — 무매매(기본)")

    # ① 반등 셋업 발화 필요
    setups = detect_setups(setup_features)
    if not setups:
        return DowntrendDecision(allow=False, defer=False, regime=rg, score=score,
                                 reason="반등 셋업 미발화 — 차단")

    # ② 장세별 점수 문턱
    floor = regime_min_score(rg)
    if score < floor:
        return DowntrendDecision(allow=False, defer=False, regime=rg, setups=setups, score=score,
                                 reason=f"점수 {score:.0f} < 하락장 문턱 {floor:.0f}")

    # ③ EV 게이트(실측 통계)
    ev_ok, ev, ev_reason = ev_allows_buy(stats, min_ev_pct=min_ev_pct, min_win_rate=min_win_rate)
    if not ev_ok:
        return DowntrendDecision(allow=False, defer=False, regime=rg, setups=setups,
                                 ev_pct=ev, score=score, reason=f"EV 게이트 차단 — {ev_reason}")

    return DowntrendDecision(allow=True, defer=False, regime=rg, setups=setups,
                             ev_pct=ev, score=score,
                             reason=f"허용 — {','.join(setups)} · {ev_reason}")
