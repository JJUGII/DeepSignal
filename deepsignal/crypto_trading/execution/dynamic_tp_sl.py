"""
dynamic_tp_sl.py — 다이나믹 TP/SL + 구간별 부분 익절 (Phase 5).

레짐·ATR 기반으로 익절(TP)·손절(SL) 수준을 자동 조절하고,
단일 부분익절 대신 다단계(multi-tier) 부분 익절 스케줄을 생성한다.

레짐별 TP/SL 기준:
  bull  (risk_on) : TP × 1.4, SL × 0.85  (더 크게 먹고 손절 타이트)
  bear  (risk_off): TP × 0.75, SL × 1.20 (빠르게 익절, 손절 넉넉)
  neutral          : 기준값 유지

ATR 보정:
  ATR_pct 가 NORMAL_ATR_PCT(3.0) 대비 높을수록 TP·SL 폭 확대
  (노이즈로 인한 오작동 방지)
  adjustment = clamp(atr_pct / NORMAL_ATR_PCT, 0.7, 1.8)

부분 익절 스케줄:
  TP 수준을 N등분하여 구간별로 지정 비율을 매도
  예) tp=2.4%:
    tier1: +0.8% → 25% 매도
    tier2: +1.6% → 25% 매도
    tier3: +2.4% → 나머지 전부 매도

ENV 플래그:
  CRYPTO_DYNAMIC_TP_SL_ENABLED     (기본: false)
  CRYPTO_DYN_BULL_TP_MULT          (기본: 1.40)
  CRYPTO_DYN_BULL_SL_MULT          (기본: 0.85)
  CRYPTO_DYN_BEAR_TP_MULT          (기본: 0.75)
  CRYPTO_DYN_BEAR_SL_MULT          (기본: 1.20)
  CRYPTO_DYN_ATR_NORMAL_PCT        (기본: 3.0)
  CRYPTO_DYN_PARTIAL_TIERS         (기본: 3)    — 부분 익절 구간 수 (1=단순, 2~4)
  CRYPTO_DYN_PARTIAL_FINAL_FRAC    (기본: 0.50) — 마지막 구간 이전까지 매도 비율 합계
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

def _env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, "")
    return v.strip().lower() in ("1", "true", "yes", "on") if v else default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class DynamicTpSlConfig:
    enabled: bool = False
    bull_tp_mult: float = 1.40
    bull_sl_mult: float = 0.85
    bear_tp_mult: float = 0.75
    bear_sl_mult: float = 1.20
    atr_normal_pct: float = 3.0
    partial_tiers: int = 3         # 부분 익절 구간 수 (1~4)
    partial_final_frac: float = 0.50  # 마지막 구간 전까지 누적 매도 비율


def load_dynamic_tp_sl_config() -> DynamicTpSlConfig:
    return DynamicTpSlConfig(
        enabled=_env_bool("CRYPTO_DYNAMIC_TP_SL_ENABLED"),
        bull_tp_mult=_env_float("CRYPTO_DYN_BULL_TP_MULT", 1.40),
        bull_sl_mult=_env_float("CRYPTO_DYN_BULL_SL_MULT", 0.85),
        bear_tp_mult=_env_float("CRYPTO_DYN_BEAR_TP_MULT", 0.75),
        bear_sl_mult=_env_float("CRYPTO_DYN_BEAR_SL_MULT", 1.20),
        atr_normal_pct=_env_float("CRYPTO_DYN_ATR_NORMAL_PCT", 3.0),
        partial_tiers=_env_int("CRYPTO_DYN_PARTIAL_TIERS", 3),
        partial_final_frac=_env_float("CRYPTO_DYN_PARTIAL_FINAL_FRAC", 0.50),
    )


# ──────────────────────────────────────────────
# 레짐 분류 (regime_policy와 동일 로직)
# ──────────────────────────────────────────────

_BULL = frozenset({"risk_on", "bull", "risk-on"})
_BEAR = frozenset({"risk_off", "bear", "risk-off"})


def _classify(regime: str) -> str:
    r = str(regime or "").strip().lower()
    if r in _BULL:
        return "bull"
    if r in _BEAR:
        return "bear"
    return "neutral"


# ──────────────────────────────────────────────
# 다이나믹 TP/SL 계산
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class DynamicTpSlResult:
    take_profit_pct: float
    stop_loss_pct: float
    regime: str
    atr_pct: float | None
    atr_adj: float
    reason: str


def compute_dynamic_tp_sl(
    base_tp_pct: float,
    base_sl_pct: float,
    *,
    macro_regime: str = "neutral",
    atr_pct: float | None = None,
    cfg: DynamicTpSlConfig | None = None,
) -> DynamicTpSlResult:
    """
    레짐·ATR 기반 동적 TP/SL 계산.

    Args:
        base_tp_pct : 기준 익절 비율 (양수, e.g. 2.0 → +2%)
        base_sl_pct : 기준 손절 비율 (양수 절댓값, e.g. 2.0 → -2%)
        macro_regime: 'risk_on' | 'neutral' | 'risk_off' 등
        atr_pct     : 최근 14일 ATR / 현재가 × 100 (%)
        cfg         : ENV 설정

    Returns:
        DynamicTpSlResult
    """
    c = cfg or load_dynamic_tp_sl_config()

    if not c.enabled:
        return DynamicTpSlResult(
            take_profit_pct=base_tp_pct,
            stop_loss_pct=base_sl_pct,
            regime=_classify(macro_regime),
            atr_pct=atr_pct,
            atr_adj=1.0,
            reason="dynamic_tp_sl_disabled",
        )

    regime = _classify(macro_regime)

    # 레짐 배수
    if regime == "bull":
        tp_mult, sl_mult = c.bull_tp_mult, c.bull_sl_mult
    elif regime == "bear":
        tp_mult, sl_mult = c.bear_tp_mult, c.bear_sl_mult
    else:
        tp_mult, sl_mult = 1.0, 1.0

    # ATR 보정
    atr_adj = 1.0
    if atr_pct is not None and atr_pct > 0 and c.atr_normal_pct > 0:
        raw_adj = atr_pct / c.atr_normal_pct
        atr_adj = max(0.7, min(1.8, raw_adj))

    final_tp = max(0.5, base_tp_pct * tp_mult * atr_adj)
    final_sl = max(0.5, base_sl_pct * sl_mult * atr_adj)

    # 상한 클램프 (너무 넓어지지 않도록)
    final_tp = min(final_tp, base_tp_pct * 2.5)
    final_sl = min(final_sl, base_sl_pct * 2.5)

    reason = (
        f"regime={regime} tp×{tp_mult:.2f} sl×{sl_mult:.2f}"
        f" atr_adj×{atr_adj:.2f} → tp={final_tp:.2f}% sl={final_sl:.2f}%"
    )
    logger.debug("dynamic_tp_sl: %s", reason)

    return DynamicTpSlResult(
        take_profit_pct=round(final_tp, 3),
        stop_loss_pct=round(final_sl, 3),
        regime=regime,
        atr_pct=atr_pct,
        atr_adj=atr_adj,
        reason=reason,
    )


# ──────────────────────────────────────────────
# 다단계 부분 익절 스케줄
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class PartialTierSpec:
    """단일 부분익절 구간 명세."""
    tier: int          # 1-based 인덱스
    trigger_pct: float # 이 수익률 도달 시 실행
    sell_fraction: float  # 보유 수량 대비 매도 비율


def build_partial_tiers(
    tp_pct: float,
    cfg: DynamicTpSlConfig | None = None,
) -> list[PartialTierSpec]:
    """
    TP 수준을 n개 구간으로 나누어 부분 익절 스케줄을 생성한다.

    partial_tiers=3, tp=2.4%, partial_final_frac=0.50 일 때:
      tier1: +0.8%  → 25% 매도
      tier2: +1.6%  → 25% 매도
      tier3: +2.4%  → 잔여 전부 매도

    partial_tiers=1 일 때 (단순 모드):
      tier1: tp_pct → 50% 매도 (기존 동작과 동일)
    """
    c = cfg or load_dynamic_tp_sl_config()
    n = max(1, min(4, c.partial_tiers))
    tp = max(0.1, tp_pct)

    tiers: list[PartialTierSpec] = []

    if n == 1:
        # 단순 모드: 단일 부분익절
        tiers.append(PartialTierSpec(tier=1, trigger_pct=tp, sell_fraction=0.5))
        return tiers

    # 다단계: n-1 구간은 equal step, 마지막은 나머지 전부
    step = tp / n
    # 마지막 전까지의 누적 매도 비율 = partial_final_frac
    per_tier_frac = c.partial_final_frac / (n - 1) if n > 1 else 0.5

    for i in range(1, n):
        tiers.append(PartialTierSpec(
            tier=i,
            trigger_pct=round(step * i, 3),
            sell_fraction=round(per_tier_frac, 4),
        ))

    # 마지막 tier: 잔여 전부
    tiers.append(PartialTierSpec(
        tier=n,
        trigger_pct=tp,
        sell_fraction=1.0,   # 잔여 전부 (engine이 remaining_fraction 처리)
    ))

    return tiers


# ──────────────────────────────────────────────
# 플랜에 동적 TP/SL 적용
# ──────────────────────────────────────────────

def apply_dynamic_tp_sl_to_plan(
    plan: Any,
    cfg: DynamicTpSlConfig | None = None,
) -> tuple[Any, DynamicTpSlResult]:
    """
    plan의 take_profit_pct / stop_loss_pct 를 동적 값으로 교체한 새 plan 반환.
    CRYPTO_DYNAMIC_TP_SL_ENABLED=false 면 원본 plan 반환.

    Returns: (adjusted_plan, result)
    """
    c = cfg or load_dynamic_tp_sl_config()

    base_tp = float(getattr(plan, "take_profit_pct", 0) or 0) or 2.0
    base_sl = float(getattr(plan, "stop_loss_pct", 0) or 0) or 2.0
    macro_regime = str(getattr(plan, "macro_regime", "") or "")

    # ATR은 score_breakdown에서 추출
    sb = getattr(plan, "score_breakdown", {}) or {}
    qd = sb.get("quality_diag") or {}
    atr_raw = qd.get("atr_pct") if isinstance(qd, dict) else None
    try:
        atr_pct = float(atr_raw) if atr_raw is not None else None
    except (TypeError, ValueError):
        atr_pct = None

    result = compute_dynamic_tp_sl(
        base_tp, base_sl,
        macro_regime=macro_regime,
        atr_pct=atr_pct,
        cfg=c,
    )

    if not c.enabled:
        return plan, result

    try:
        adjusted = dataclasses.replace(
            plan,
            take_profit_pct=result.take_profit_pct,
            stop_loss_pct=result.stop_loss_pct,
        )
    except Exception:
        logger.warning("dynamic_tp_sl: dataclasses.replace 실패 — 원본 plan 사용")
        return plan, result

    return adjusted, result


# ──────────────────────────────────────────────
# 다단계 부분익절 상태 관리 (runner_state 연동)
# ──────────────────────────────────────────────

def get_partial_tier(runner_state: dict[str, Any], market: str) -> int:
    """현재 시장의 부분익절 완료 tier 반환 (0=없음)."""
    partial_state = runner_state.get("partial_tier") or {}
    return int(partial_state.get(market.upper(), 0) or 0)


def set_partial_tier(runner_state: dict[str, Any], market: str, tier: int) -> None:
    """부분익절 완료 tier를 runner_state에 기록."""
    if "partial_tier" not in runner_state or not isinstance(runner_state["partial_tier"], dict):
        runner_state["partial_tier"] = {}
    runner_state["partial_tier"][market.upper()] = tier


def reset_partial_tier(runner_state: dict[str, Any], market: str) -> None:
    """포지션 청산/매수 시 tier 초기화."""
    partial_state = runner_state.get("partial_tier") or {}
    partial_state.pop(market.upper(), None)
    runner_state["partial_tier"] = partial_state


def check_partial_sell(
    market: str,
    current_pnl_pct: float,
    tiers: list[PartialTierSpec],
    runner_state: dict[str, Any],
) -> PartialTierSpec | None:
    """
    현재 수익률에서 실행해야 할 다음 부분익절 tier 반환.
    이미 해당 tier 이상 실행됐으면 None.
    """
    current_tier = get_partial_tier(runner_state, market)
    for spec in tiers:
        if spec.tier <= current_tier:
            continue
        if current_pnl_pct >= spec.trigger_pct:
            return spec
    return None
