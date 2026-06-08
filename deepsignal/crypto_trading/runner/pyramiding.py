"""
pyramiding.py — 피라미딩(Add-on) 전략 (Phase 6).

보유 포지션이 수익 구간(ADD_ON_TRIGGER_PCT) 진입 시
기존 포지션에 추가 매수(add-on)를 실행한다.

원칙:
  - 이미 수익 중인 포지션에만 추가 — 손실 평균매수(물타기) 아님
  - 원래 매수 금액의 일부(ADD_ON_SIZE_MULT)만 추가
  - 하루 최대 ADD_ON_MAX_TOTAL 건, 시장별 ADD_ON_MAX_PER_MARKET 건
  - 직전 GSQS 점수가 ADD_ON_MIN_GSQS 이상일 때만 (runner state 캐시 활용)
  - ADD_ON 쿨다운 기간(분) 내 동일 시장 재진입 금지

ENV 플래그:
  CRYPTO_PYRAMIDING_ENABLED           (기본: false)
  CRYPTO_PYRAMID_TRIGGER_PCT          (기본: 1.5)   — 추가매수 발동 최소 수익률 (%)
  CRYPTO_PYRAMID_SIZE_MULT            (기본: 0.50)  — 원래 주문금액 대비 추가매수 비율
  CRYPTO_PYRAMID_MAX_TOTAL_PER_DAY    (기본: 3)     — 일일 최대 추가매수 건수 (전체)
  CRYPTO_PYRAMID_MAX_PER_MARKET       (기본: 1)     — 시장당 일일 최대 추가매수 건수
  CRYPTO_PYRAMID_MIN_GSQS             (기본: 68.0)  — 추가매수 허용 최소 GSQS
  CRYPTO_PYRAMID_COOLDOWN_MIN         (기본: 60)    — 추가매수 쿨다운 (분)
  CRYPTO_PYRAMID_MAX_KRW              (기본: 150000) — 추가매수 최대 금액 (원)
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time as _time
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
class PyramidingConfig:
    enabled: bool = False
    trigger_pct: float = 1.5
    size_mult: float = 0.50
    max_total_per_day: int = 3
    max_per_market: int = 1
    min_gsqs: float = 68.0
    cooldown_minutes: float = 60.0
    max_krw: float = 150_000.0


def load_pyramiding_config() -> PyramidingConfig:
    return PyramidingConfig(
        enabled=_env_bool("CRYPTO_PYRAMIDING_ENABLED"),
        trigger_pct=_env_float("CRYPTO_PYRAMID_TRIGGER_PCT", 1.5),
        size_mult=_env_float("CRYPTO_PYRAMID_SIZE_MULT", 0.50),
        max_total_per_day=_env_int("CRYPTO_PYRAMID_MAX_TOTAL_PER_DAY", 3),
        max_per_market=_env_int("CRYPTO_PYRAMID_MAX_PER_MARKET", 1),
        min_gsqs=_env_float("CRYPTO_PYRAMID_MIN_GSQS", 68.0),
        cooldown_minutes=_env_float("CRYPTO_PYRAMID_COOLDOWN_MIN", 60.0),
        max_krw=_env_float("CRYPTO_PYRAMID_MAX_KRW", 150_000.0),
    )


# ──────────────────────────────────────────────
# 상태 헬퍼
# ──────────────────────────────────────────────

def _pyramid_count_today(state: dict[str, Any], today_key: str) -> int:
    if state.get("pyramid_day") != today_key:
        return 0
    return int(state.get("pyramid_count_today", 0) or 0)


def _pyramid_market_count_today(
    state: dict[str, Any], market: str, today_key: str
) -> int:
    if state.get("pyramid_day") != today_key:
        return 0
    counts = state.get("pyramid_market_counts") or {}
    return int(counts.get(market.upper(), 0) or 0)


def _last_pyramid_ts(state: dict[str, Any], market: str) -> float | None:
    ts_map = state.get("pyramid_last_ts") or {}
    v = ts_map.get(market.upper())
    return float(v) if v is not None else None


# ──────────────────────────────────────────────
# 핵심 판정 함수
# ──────────────────────────────────────────────

def should_pyramid(
    holding: Any,
    state: dict[str, Any],
    today_key: str,
    cfg: PyramidingConfig | None = None,
    *,
    last_gsqs: float | None = None,
) -> tuple[bool, str]:
    """
    피라미딩 추가매수 여부 판정.

    Args:
        holding   : Holding 객체 (pnl_pct, market, valuation_krw 등)
        state     : runner state dict (카운터 포함)
        today_key : 'YYYYMMDD' 형식 날짜 키
        cfg       : 설정 (None 시 ENV 로드)
        last_gsqs : 마지막 분석 틱에서 계산된 GSQS 점수

    Returns:
        (allowed: bool, reason: str)
    """
    c = cfg or load_pyramiding_config()

    if not c.enabled:
        return False, "pyramiding_disabled"

    market = str(getattr(holding, "market", "") or "").upper()
    if not market:
        return False, "invalid_market"

    pnl_pct = float(getattr(holding, "pnl_pct", 0) or 0)
    if pnl_pct < c.trigger_pct:
        return False, f"pnl_insufficient:{pnl_pct:.2f}%<{c.trigger_pct}%"

    # 일일 전체 한도
    total_today = _pyramid_count_today(state, today_key)
    if total_today >= c.max_total_per_day:
        return False, f"daily_total_cap:{total_today}/{c.max_total_per_day}"

    # 시장별 일일 한도
    market_today = _pyramid_market_count_today(state, market, today_key)
    if market_today >= c.max_per_market:
        return False, f"market_cap:{market}:{market_today}/{c.max_per_market}"

    # 쿨다운
    last_ts = _last_pyramid_ts(state, market)
    if last_ts is not None:
        elapsed_min = (_time.time() - last_ts) / 60.0
        if elapsed_min < c.cooldown_minutes:
            return False, f"cooldown:{market}:{elapsed_min:.1f}min<{c.cooldown_minutes}min"

    # GSQS 체크
    if last_gsqs is not None and last_gsqs < c.min_gsqs:
        return False, f"gsqs_insufficient:{last_gsqs:.1f}<{c.min_gsqs}"

    return True, (
        f"ok:pnl={pnl_pct:.2f}%≥{c.trigger_pct}%"
        f",day={total_today+1}/{c.max_total_per_day}"
        f",gsqs={last_gsqs:.1f}" if last_gsqs is not None else
        f"ok:pnl={pnl_pct:.2f}%≥{c.trigger_pct}%"
        f",day={total_today+1}/{c.max_total_per_day}"
    )


# ──────────────────────────────────────────────
# 추가매수 플랜 생성
# ──────────────────────────────────────────────

def build_addon_plan(
    holding: Any,
    base_order_krw: float,
    cfg: PyramidingConfig | None = None,
    *,
    current_price: float | None = None,
    macro_regime: str = "",
    final_score: float | None = None,
) -> Any:
    """
    보유 포지션에 대한 추가매수 CryptoOrderPlan 생성.

    Args:
        holding       : Holding 객체 (market, avg_buy_price 등)
        base_order_krw: 원래 매수 주문 금액
        cfg           : 설정
        current_price : 현재가 (None이면 avg_buy_price 사용)
        macro_regime  : 레짐 (plan에 전달)
        final_score   : GSQS 점수 (plan에 전달)

    Returns: CryptoOrderPlan
    """
    from deepsignal.crypto_trading.execution.order_plan import CryptoOrderPlan
    from deepsignal.live_trading.time_utils import now_kst_iso

    c = cfg or load_pyramiding_config()

    market = str(getattr(holding, "market", "") or "")
    display_name = str(getattr(holding, "display_name", None) or market)
    avg_buy = float(getattr(holding, "avg_buy_price", 0) or 0)
    pnl_pct = float(getattr(holding, "pnl_pct", 0) or 0)
    cur = float(current_price or avg_buy * (1 + pnl_pct / 100) or avg_buy)

    addon_krw = min(base_order_krw * c.size_mult, c.max_krw)
    addon_krw = max(addon_krw, 5_000.0)   # Upbit 최소 주문

    return CryptoOrderPlan(
        broker="upbit",
        market=market,
        side="buy",
        order_type="limit",
        krw_amount=round(addon_krw, 0),
        volume=round(addon_krw / cur, 8) if cur > 0 else 0,
        limit_price=round(cur, 1),
        avg_buy_price=avg_buy,
        pnl_pct=pnl_pct,
        display_name=display_name,
        reason=(
            f"피라미딩 추가매수 (수익률 {pnl_pct:+.2f}% → add-on {addon_krw:,.0f}원)"
        ),
        status="CRYPTO_PLAN_READY",
        created_at=now_kst_iso(),
        macro_regime=macro_regime,
        final_score=final_score,
    )


# ──────────────────────────────────────────────
# 상태 기록
# ──────────────────────────────────────────────

def record_pyramid(
    state: dict[str, Any],
    *,
    market: str,
    today_key: str,
) -> None:
    """피라미딩 실행 후 runner state 갱신."""
    m = market.upper()

    if state.get("pyramid_day") != today_key:
        state["pyramid_day"] = today_key
        state["pyramid_count_today"] = 0
        state["pyramid_market_counts"] = {}
        state["pyramid_log_today"] = []

    state["pyramid_count_today"] = int(state.get("pyramid_count_today", 0) or 0) + 1

    counts = state.get("pyramid_market_counts") or {}
    counts[m] = int(counts.get(m, 0) or 0) + 1
    state["pyramid_market_counts"] = counts

    ts_map = state.get("pyramid_last_ts") or {}
    ts_map[m] = _time.time()
    state["pyramid_last_ts"] = ts_map

    log: list[dict[str, Any]] = list(state.get("pyramid_log_today") or [])
    try:
        from deepsignal.live_trading.time_utils import now_kst_iso
        ts_str = now_kst_iso()
    except Exception:
        ts_str = ""
    log.append({"market": m, "ts": ts_str})
    state["pyramid_log_today"] = log[-20:]


# ──────────────────────────────────────────────
# 보유 포지션 스캔 — 추가매수 후보 탐색
# ──────────────────────────────────────────────

def scan_pyramid_candidates(
    holdings: list[Any],
    state: dict[str, Any],
    today_key: str,
    cfg: PyramidingConfig | None = None,
    *,
    gsqs_by_market: dict[str, float] | None = None,
    base_order_krw: float = 300_000.0,
) -> list[tuple[Any, str]]:
    """
    보유 포지션 중 피라미딩 추가매수 후보를 반환.

    Args:
        holdings      : 현재 보유 포지션 리스트
        state         : runner state
        today_key     : 날짜 키
        cfg           : 설정
        gsqs_by_market: 마지막 분석 틱의 시장별 GSQS {'KRW-BTC': 75.0, ...}
        base_order_krw: 원래 기준 주문금액

    Returns: [(plan, reason), ...]  — 실행할 피라미딩 플랜과 사유
    """
    c = cfg or load_pyramiding_config()
    if not c.enabled:
        return []

    result: list[tuple[Any, str]] = []

    for holding in holdings:
        market = str(getattr(holding, "market", "") or "").upper()
        if not market:
            continue

        gsqs = (gsqs_by_market or {}).get(market)

        ok, reason = should_pyramid(holding, state, today_key, cfg=c, last_gsqs=gsqs)
        if not ok:
            logger.debug("pyramiding: %s → 스킵 (%s)", market, reason)
            continue

        plan = build_addon_plan(
            holding,
            base_order_krw,
            cfg=c,
            final_score=gsqs,
        )
        logger.info("pyramiding: 추가매수 후보 %s → %s", market, reason)
        result.append((plan, reason))

    return result


# ──────────────────────────────────────────────
# 텔레그램 사후 통보
# ──────────────────────────────────────────────

def notify_pyramid_result(
    output_dir: str,
    plan: Any,
    *,
    success: bool,
    reason: str,
    order_uuid: str | None = None,
) -> None:
    """피라미딩 체결 결과 텔레그램 통보 (실패해도 삼킴)."""
    try:
        from deepsignal.crypto_trading.telegram.flow import (
            load_crypto_telegram_config_from_env,
            telegram_send_plain,
        )
        tg = load_crypto_telegram_config_from_env(output_dir=output_dir)
        if not tg.bot_token or not tg.allowed_chat_id:
            return

        name = getattr(plan, "display_name", None) or getattr(plan, "market", "-")
        krw = int(getattr(plan, "krw_amount", 0) or 0)
        pnl = float(getattr(plan, "pnl_pct", 0) or 0)
        gsqs = getattr(plan, "final_score", None)
        gsqs_str = f"{gsqs:.1f}pt" if gsqs is not None else "-"

        if success:
            uuid_line = f"\n  주문ID: {order_uuid}" if order_uuid else ""
            msg = (
                f"📈 [피라미딩 추가매수] {name}\n"
                f"  보유 수익률: {pnl:+.2f}%\n"
                f"  추가 금액: ₩{krw:,}\n"
                f"  GSQS: {gsqs_str}{uuid_line}\n"
                f"  ✅ 자동 체결됨"
            )
        else:
            msg = (
                f"📈 [피라미딩 실패] {name}\n"
                f"  사유: {reason}\n"
                f"  보유 수익률: {pnl:+.2f}%"
            )
        telegram_send_plain(tg, msg)
    except Exception as exc:
        logger.debug("pyramid notify 실패 (무시): %s", exc)
