"""
fastlane.py — 패스트레인 자동 실행 정책.

P(win) ≥ CRYPTO_FASTLANE_MIN_PWIN  AND
GSQS 점수 ≥ CRYPTO_FASTLANE_MIN_GSQS

두 조건을 동시에 충족하는 BUY 신호는 텔레그램 승인 없이 즉시 자동 체결.
체결 후 텔레그램으로 사후 통보.

ENV 플래그:
  CRYPTO_FASTLANE_ENABLED        (기본: false)  — 전체 on/off
  CRYPTO_FASTLANE_MIN_PWIN       (기본: 0.65)   — 최소 승률 임계값
  CRYPTO_FASTLANE_MIN_GSQS       (기본: 72.0)   — 최소 GSQS 점수
  CRYPTO_FASTLANE_MAX_DAILY      (기본: 10)     — 일일 자동 실행 최대 건수
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class FastlaneConfig:
    enabled: bool = False
    min_pwin: float = 0.65
    min_gsqs: float = 72.0
    max_daily: int = 10


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def load_fastlane_config() -> FastlaneConfig:
    def _f(key: str, default: float) -> float:
        try:
            return float(os.environ.get(key) or default)
        except (TypeError, ValueError):
            return default

    def _i(key: str, default: int) -> int:
        try:
            return int(os.environ.get(key) or default)
        except (TypeError, ValueError):
            return default

    return FastlaneConfig(
        enabled=_truthy(os.environ.get("CRYPTO_FASTLANE_ENABLED")),
        min_pwin=_f("CRYPTO_FASTLANE_MIN_PWIN", 0.65),
        min_gsqs=_f("CRYPTO_FASTLANE_MIN_GSQS", 72.0),
        max_daily=_i("CRYPTO_FASTLANE_MAX_DAILY", 10),
    )


# ──────────────────────────────────────────────
# P(win) 추출 (CryptoOrderPlan에서)
# ──────────────────────────────────────────────

def extract_pwin(plan: Any) -> float | None:
    """
    plan.score_breakdown 또는 plan.quality_gates 에서 승률 추출.
    모델 미사용 시 None 반환.
    """
    bd = plan.score_breakdown if isinstance(getattr(plan, "score_breakdown", None), dict) else {}
    for key in ("win_probability", "p_win", "ml_win_prob"):
        if key in bd:
            try:
                return float(bd[key])
            except (TypeError, ValueError):
                pass

    gates = plan.quality_gates if isinstance(getattr(plan, "quality_gates", None), dict) else {}
    if "win_probability" in gates:
        try:
            return float(gates["win_probability"])
        except (TypeError, ValueError):
            pass

    return None


# ──────────────────────────────────────────────
# 일일 카운터 헬퍼
# ──────────────────────────────────────────────

def _fastlane_count_today(state: dict[str, Any], today_key: str) -> int:
    if state.get("fastlane_day") != today_key:
        return 0
    return int(state.get("fastlane_count_today", 0) or 0)


# ──────────────────────────────────────────────
# 핵심 판정 함수
# ──────────────────────────────────────────────

def should_fastlane(
    plan: Any,
    state: dict[str, Any],
    today_key: str,
    cfg: FastlaneConfig | None = None,
    output_dir: str | None = None,
) -> tuple[bool, str]:
    """
    패스트레인 자동 실행 여부 판정.

    Phase 4 피드백 연동:
      CRYPTO_FEEDBACK_TUNING_ENABLED=true 이고 output_dir 전달 시
      피드백 임계값을 ENV 값 대신 사용한다.

    Returns: (allowed: bool, reason: str)
    """
    g = cfg or load_fastlane_config()

    if not g.enabled:
        return False, "fastlane_disabled"

    if getattr(plan, "side", "").lower() != "buy":
        return False, "only_buy_supported"

    # 일일 한도
    count = _fastlane_count_today(state, today_key)
    if count >= g.max_daily:
        return False, f"daily_limit_reached:{count}/{g.max_daily}"

    # ── Phase 4: 피드백 연동 임계값 ──────────────
    effective_min_gsqs = g.min_gsqs
    effective_min_pwin = g.min_pwin
    threshold_source = "env"
    if output_dir:
        try:
            from deepsignal.crypto_trading.runner.feedback_tuner import (
                get_effective_fastlane_thresholds,
            )
            effective_min_gsqs, effective_min_pwin, threshold_source = (
                get_effective_fastlane_thresholds(
                    output_dir,
                    base_min_gsqs=g.min_gsqs,
                    base_min_pwin=g.min_pwin,
                )
            )
        except Exception:
            pass
    # ─────────────────────────────────────────────

    # GSQS 총점
    gsqs = float(getattr(plan, "final_score", None) or 0)
    if gsqs < effective_min_gsqs:
        return False, f"gsqs_insufficient:{gsqs:.1f}<{effective_min_gsqs:.1f}({threshold_source})"

    # P(win)
    pwin = extract_pwin(plan)
    if pwin is None:
        return False, "pwin_unavailable(모델 미학습)"
    if pwin < effective_min_pwin:
        return False, f"pwin_insufficient:{pwin:.3f}<{effective_min_pwin:.3f}({threshold_source})"

    return True, f"ok:gsqs={gsqs:.1f},pwin={pwin:.3f},thr={threshold_source}"


# ──────────────────────────────────────────────
# 상태 기록
# ──────────────────────────────────────────────

def record_fastlane(
    state: dict[str, Any],
    *,
    market: str,
    today_key: str,
    pwin: float | None = None,
    gsqs: float | None = None,
) -> None:
    """패스트레인 실행 후 runner state 갱신."""
    if state.get("fastlane_day") != today_key:
        state["fastlane_day"] = today_key
        state["fastlane_count_today"] = 0
        state["fastlane_log_today"] = []

    state["fastlane_count_today"] = int(state.get("fastlane_count_today", 0) or 0) + 1

    log: list[dict[str, Any]] = list(state.get("fastlane_log_today") or [])
    entry: dict[str, Any] = {"market": market}
    if pwin is not None:
        entry["pwin"] = round(pwin, 4)
    if gsqs is not None:
        entry["gsqs"] = round(gsqs, 1)
    try:
        from deepsignal.live_trading.time_utils import now_kst_iso
        entry["ts"] = now_kst_iso()
    except Exception:
        pass
    log.append(entry)
    state["fastlane_log_today"] = log[-50:]  # 최근 50건만 유지


# ──────────────────────────────────────────────
# 텔레그램 사후 통보
# ──────────────────────────────────────────────

def notify_fastlane_result(
    output_dir: str,
    plan: Any,
    *,
    success: bool,
    pwin: float | None,
    reasons: list[str],
    order_uuid: str | None = None,
) -> None:
    """
    패스트레인 체결 결과를 텔레그램으로 전송.
    실패해도 예외를 삼켜 메인 플로우에 영향 없음.
    """
    try:
        from deepsignal.crypto_trading.telegram.flow import (
            load_crypto_telegram_config_from_env,
            telegram_send_plain,
        )
        tg = load_crypto_telegram_config_from_env(output_dir=output_dir)
        if not tg.bot_token or not tg.allowed_chat_id:
            return

        gsqs_str  = f"{plan.final_score:.1f}pt" if getattr(plan, "final_score", None) else "-"
        pwin_str  = f"{pwin * 100:.1f}%" if pwin is not None else "-"
        name      = getattr(plan, "display_name", None) or getattr(plan, "market", "-")
        krw       = int(getattr(plan, "krw_amount", 0) or 0)
        limit_px  = getattr(plan, "limit_price", 0) or 0

        if success:
            uuid_line = f"\n  주문ID: {order_uuid}" if order_uuid else ""
            msg = (
                f"⚡ [패스트레인 자동 체결] {name}\n"
                f"  방향: 매수\n"
                f"  금액: ₩{krw:,}\n"
                f"  주문가: {limit_px:,.1f}원\n"
                f"  GSQS: {gsqs_str}  P(win): {pwin_str}{uuid_line}\n"
                f"  ✅ 승인 없이 즉시 체결됨"
            )
        else:
            reason_str = "; ".join(reasons[:2]) if reasons else "알 수 없음"
            msg = (
                f"⚡ [패스트레인 체결 실패] {name}\n"
                f"  사유: {reason_str}\n"
                f"  GSQS: {gsqs_str}  P(win): {pwin_str}"
            )

        telegram_send_plain(tg, msg)
        logger.info("fastlane notify 전송 완료: success=%s", success)

    except Exception as exc:
        logger.warning("fastlane notify 실패 (무시): %s", exc)
