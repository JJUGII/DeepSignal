"""
regime_policy.py — 레짐 연동 공격성 자동 조절 (Phase 3).

macro_regime 값(risk_on / neutral / risk_off)에 따라
포지션 크기·최소 GSQS 임계값을 자동으로 조절한다.

  risk_on  → 공격적 (포지션 +30%, GSQS 하한 완화)
  neutral  → 기준값 유지
  risk_off → 방어적 (포지션 -40%, GSQS 하한 강화)

ENV 플래그:
  CRYPTO_REGIME_POLICY_ENABLED    (기본: false)
  CRYPTO_REGIME_BULL_SIZE_MULT    (기본: 1.30)   — risk_on 포지션 배수
  CRYPTO_REGIME_BEAR_SIZE_MULT    (기본: 0.60)   — risk_off 포지션 배수
  CRYPTO_REGIME_BULL_GSQS_FLOOR  (기본: 58.0)   — risk_on 최소 GSQS
  CRYPTO_REGIME_BEAR_GSQS_FLOOR  (기본: 78.0)   — risk_off 최소 GSQS
  CRYPTO_REGIME_NEUTRAL_GSQS_FLOOR (기본: 65.0) — neutral 최소 GSQS
"""

from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class RegimePolicyConfig:
    enabled: bool = False
    bull_size_mult: float = 1.30
    bear_size_mult: float = 0.60
    bull_gsqs_floor: float = 58.0
    neutral_gsqs_floor: float = 65.0
    bear_gsqs_floor: float = 78.0


def load_regime_policy_config() -> RegimePolicyConfig:
    return RegimePolicyConfig(
        enabled=_env_bool("CRYPTO_REGIME_POLICY_ENABLED"),
        bull_size_mult=_env_float("CRYPTO_REGIME_BULL_SIZE_MULT", 1.30),
        bear_size_mult=_env_float("CRYPTO_REGIME_BEAR_SIZE_MULT", 0.60),
        bull_gsqs_floor=_env_float("CRYPTO_REGIME_BULL_GSQS_FLOOR", 58.0),
        neutral_gsqs_floor=_env_float("CRYPTO_REGIME_NEUTRAL_GSQS_FLOOR", 65.0),
        bear_gsqs_floor=_env_float("CRYPTO_REGIME_BEAR_GSQS_FLOOR", 78.0),
    )


# ──────────────────────────────────────────────
# 레짐 분류
# ──────────────────────────────────────────────

_BULL_ALIASES = frozenset({"risk_on", "bull", "risk-on"})
_BEAR_ALIASES = frozenset({"risk_off", "bear", "risk-off", "risk_off"})


def classify_regime(macro_regime: str) -> str:
    """
    macro_regime 문자열을 'bull' | 'neutral' | 'bear' 로 정규화.
    """
    r = str(macro_regime or "").strip().lower()
    if r in _BULL_ALIASES:
        return "bull"
    if r in _BEAR_ALIASES:
        return "bear"
    return "neutral"


# ──────────────────────────────────────────────
# 레짐별 파라미터 반환
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class RegimeParams:
    regime: str           # 'bull' | 'neutral' | 'bear'
    size_multiplier: float
    gsqs_floor: float
    reason: str


def get_regime_params(
    macro_regime: str,
    cfg: RegimePolicyConfig | None = None,
) -> RegimeParams:
    """
    macro_regime 문자열에서 레짐별 조절 파라미터 반환.
    CRYPTO_REGIME_POLICY_ENABLED=false 면 neutral 기준값 반환.
    """
    c = cfg or load_regime_policy_config()
    regime = classify_regime(macro_regime)

    if not c.enabled:
        return RegimeParams(
            regime=regime,
            size_multiplier=1.0,
            gsqs_floor=c.neutral_gsqs_floor,
            reason="regime_policy_disabled",
        )

    if regime == "bull":
        return RegimeParams(
            regime="bull",
            size_multiplier=c.bull_size_mult,
            gsqs_floor=c.bull_gsqs_floor,
            reason=f"bull(risk_on): size×{c.bull_size_mult}, gsqs≥{c.bull_gsqs_floor}",
        )
    if regime == "bear":
        return RegimeParams(
            regime="bear",
            size_multiplier=c.bear_size_mult,
            gsqs_floor=c.bear_gsqs_floor,
            reason=f"bear(risk_off): size×{c.bear_size_mult}, gsqs≥{c.bear_gsqs_floor}",
        )
    return RegimeParams(
        regime="neutral",
        size_multiplier=1.0,
        gsqs_floor=c.neutral_gsqs_floor,
        reason=f"neutral: size×1.0, gsqs≥{c.neutral_gsqs_floor}",
    )


# ──────────────────────────────────────────────
# 플랜에 레짐 조절 적용
# ──────────────────────────────────────────────

def apply_regime_to_plan(plan: Any, cfg: RegimePolicyConfig | None = None) -> Any:
    """
    plan.macro_regime 기반으로 포지션 크기를 조절한 새 CryptoOrderPlan 반환.
    CRYPTO_REGIME_POLICY_ENABLED=false 면 원본 plan 반환.
    레짐이 bear이고 GSQS가 floor 미달이면 (plan, blocked=True, reason) 반환.

    Returns:
        (adjusted_plan, blocked: bool, reason: str)
    """
    c = cfg or load_regime_policy_config()
    macro_regime = str(getattr(plan, "macro_regime", "") or "")
    params = get_regime_params(macro_regime, c)

    # disabled → 그대로 반환
    if not c.enabled:
        return plan, False, params.reason

    # GSQS 체크
    gsqs = float(getattr(plan, "final_score", None) or 0)
    if gsqs < params.gsqs_floor:
        reason = (
            f"regime={params.regime} gsqs_floor={params.gsqs_floor:.1f} "
            f"but gsqs={gsqs:.1f} → blocked"
        )
        logger.info("regime_policy: %s", reason)
        return plan, True, reason

    # krw_amount 조절
    orig_krw = float(getattr(plan, "krw_amount", 0) or 0)
    adjusted_krw = orig_krw * params.size_multiplier

    if params.size_multiplier != 1.0:
        logger.info(
            "regime_policy: %s → krw_amount %.0f → %.0f (×%.2f)",
            params.reason, orig_krw, adjusted_krw, params.size_multiplier,
        )

    try:
        adjusted_plan = dataclasses.replace(plan, krw_amount=adjusted_krw)
    except Exception:
        # dataclass replace 실패 시 원본 반환
        logger.warning("regime_policy: dataclasses.replace 실패 — 원본 plan 사용")
        return plan, False, params.reason

    return adjusted_plan, False, params.reason


# ──────────────────────────────────────────────
# GSQS 게이트 단독 판정 (fastlane 연동용)
# ──────────────────────────────────────────────

def regime_gsqs_check(plan: Any, cfg: RegimePolicyConfig | None = None) -> tuple[bool, str]:
    """
    레짐 GSQS 하한 체크만 수행 (포지션 크기 조절 없이).
    Returns (passed: bool, reason: str).
    """
    c = cfg or load_regime_policy_config()
    if not c.enabled:
        return True, "regime_policy_disabled"

    macro_regime = str(getattr(plan, "macro_regime", "") or "")
    params = get_regime_params(macro_regime, c)
    gsqs = float(getattr(plan, "final_score", None) or 0)

    if gsqs < params.gsqs_floor:
        return False, (
            f"regime_gsqs_gate: {params.regime} 레짐에서 "
            f"GSQS {gsqs:.1f} < floor {params.gsqs_floor:.1f}"
        )
    return True, f"regime_gsqs_ok({params.regime},gsqs={gsqs:.1f}≥{params.gsqs_floor:.1f})"
