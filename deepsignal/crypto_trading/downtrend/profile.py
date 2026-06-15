"""[Phase1] 하락장 보수 단타 프로파일 + L10 paper-only 가드.

L10(상승추격·안전off)은 하락장 반등 스캘핑과 정반대다. 하락장 모드는 '거의 안 사되,
사면 타이트하게': 좁은 스프레드·강한 호가벽·작은 포지션·짧은 청산.
주의: 좁은 스프레드(0.15%)는 하락장에 호가가 0.6~0.8%로 벌어지면 사실상 '대부분 무매매'를
뜻한다 — 그것이 의도된 결과(손실 회피).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DowntrendScalpProfile:
    """하락장 반등 스캘핑 파라미터 (보수)."""

    max_spread_pct: float = 0.15        # 단타 수수료 왕복 0.10% 대비 합리적 상한
    min_bid_ask_ratio: float = 1.2      # 매수세 우위 호가만
    aggressive_fill: bool = False       # 시장가성 추격 금지(슬리피지 차단)
    position_mult: float = 0.3          # 작은 베팅
    rebuy_cooldown_min: int = 30        # 같은 패턴 반복손실 방지
    trailing_stop_pct: float = 0.35     # 짧은 반등 — 좁은 트레일
    time_stop_min: float = 4.0          # 빠른 손절(반등 실패 시)
    max_hold_min: float = 10.0          # 드리프트 영구보유 차단
    tp_pct: float = 0.6                 # 작고 빠른 익절
    sl_pct: float = -0.4                # 타이트 손절
    min_score: float = 70.0             # 하락장 진입 문턱 상향(거의 안 산다)

    def to_dict(self) -> dict:
        return asdict(self)


DOWNTREND_SCALP = DowntrendScalpProfile()


def apply_downtrend_profile(env: dict[str, str] | None = None, *, profile: DowntrendScalpProfile | None = None) -> dict[str, str]:
    """프로파일을 CRYPTO_* env로 적용. 반환: 설정한 키-값(검증·로깅용).

    레짐이 DOWN_TREND/PANIC일 때 러너가 호출해 진입/청산을 보수화한다.
    """
    e = env if env is not None else os.environ
    p = profile or DOWNTREND_SCALP
    applied = {
        "CRYPTO_MAX_SPREAD_PCT": str(p.max_spread_pct),
        "CRYPTO_MIN_BID_ASK_RATIO": str(p.min_bid_ask_ratio),
        "CRYPTO_AGGRESSIVE_FILL": "true" if p.aggressive_fill else "false",
        "DEEPSIGNAL_POSITION_MULT": str(p.position_mult),
        "CRYPTO_REBUY_COOLDOWN_MINUTES": str(p.rebuy_cooldown_min),
        "CRYPTO_TRAILING_STOP_PCT": str(p.trailing_stop_pct),
        "CRYPTO_TIME_STOP_MINUTES": str(p.time_stop_min),
        "CRYPTO_MAX_HOLD_MINUTES": str(p.max_hold_min),
        "CRYPTO_MIN_FINAL_SCORE": str(p.min_score),
    }
    e.update(applied)
    return applied


def l10_is_paper_only(env: dict[str, str] | None = None) -> bool:
    """공격성 ≥9(도박밴드)는 실거래 금지(paper/shadow 전용).

    명시적 override(DEEPSIGNAL_ALLOW_LIVE_L9_PLUS=true)가 없으면 True(=paper 강제).
    """
    e = env if env is not None else os.environ
    try:
        lvl = int(round(float(e.get("DEEPSIGNAL_AGGRESSION", "1"))))
    except (TypeError, ValueError):
        lvl = 1
    if lvl < 9:
        return False
    override = str(e.get("DEEPSIGNAL_ALLOW_LIVE_L9_PLUS", "false")).strip().lower()
    return override not in ("1", "true", "yes", "on")
