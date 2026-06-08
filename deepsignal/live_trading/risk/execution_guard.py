"""승인형 실매수 1회 실행 전 안전 검증 ([실전-4])."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from deepsignal.live_trading.broker_interface import BrokerOrderRequest

if TYPE_CHECKING:
    from deepsignal.live_trading.trading_session import TradingSessionPolicy, TradingSessionResult
from deepsignal.live_trading.kis_config import KISConfig
from deepsignal.live_trading.live_order_plan import LiveOrderPlan


class LiveExecutionBlocked(Exception):
    """가드 실패. 메시지는 `args[0]` 또는 `errors` 리스트로 전달."""

    def __init__(self, message: str | None = None, *, errors: list[str] | None = None) -> None:
        self.errors = list(errors or ([] if message is None else [message]))
        msg = "; ".join(self.errors) if self.errors else "live execution blocked"
        super().__init__(msg)


@dataclass
class LiveExecutionPolicy:
    """단발 실매수 한도·허용 조건 (기본은 소액·1건)."""

    max_total_order_value: float = 100_000.0
    max_single_order_value: float = 50_000.0
    max_orders: int = 1
    allow_live_env: bool = False
    require_final_confirm_text: str = "I_UNDERSTAND_REAL_ORDER"
    allow_symbols: list[str] | None = None
    require_trading_session: bool = True


@dataclass
class PriceDivergencePolicy:
    """실주문 LIMIT 가격이 실시간 호가와 너무 벌어졌을 때 차단하는 정책 (#5)."""

    enabled: bool = True
    max_divergence_pct: float = 3.0
    require_quote: bool = False  # 실시간 호가를 못 받으면 차단할지(기본: 경고만)


def price_divergence_policy_from_env() -> "PriceDivergencePolicy":
    import os

    def _b(name: str, dflt: bool) -> bool:
        v = os.environ.get(name)
        if v is None or not str(v).strip():
            return dflt
        return str(v).strip().lower() in ("1", "true", "yes")

    def _f(name: str, dflt: float) -> float:
        v = os.environ.get(name)
        if v is None or not str(v).strip():
            return dflt
        try:
            return float(v)
        except ValueError:
            return dflt

    return PriceDivergencePolicy(
        enabled=_b("KIS_PRICE_DIVERGENCE_CHECK", True),
        max_divergence_pct=_f("KIS_MAX_PRICE_DIVERGENCE_PCT", 3.0),
        require_quote=_b("KIS_REQUIRE_LIVE_QUOTE", False),
    )


def check_order_price_divergence(
    broker: object,
    requests: list[BrokerOrderRequest],
    policy: "PriceDivergencePolicy",
) -> tuple[bool, list[str], list[str], dict[str, float]]:
    """각 주문의 limit_price를 브로커 실시간 호가와 대조. (ok, errors, warnings, quotes).

    yfinance 일봉 종가로 산정된 LIMIT가가 갭 등으로 실시간과 크게 벌어지면 차단한다.
    """
    if not policy.enabled:
        return (True, [], [], {})
    getq = getattr(broker, "get_current_price", None)
    if not callable(getq):
        return (True, [], ["broker가 get_current_price 미지원 — 호가 괴리 검증 생략"], {})
    errors: list[str] = []
    warnings: list[str] = []
    quotes: dict[str, float] = {}
    for req in requests:
        sym = str(req.symbol)
        try:
            q = getq(sym)
        except Exception:  # noqa: BLE001 — 조회 실패는 None과 동일 취급
            q = None
        if q is None or float(q) <= 0:
            msg = f"{sym}: 실시간 호가 없음 — 가격 괴리 검증 불가"
            (errors if policy.require_quote else warnings).append(msg)
            continue
        q = float(q)
        quotes[sym] = q
        lim = float(req.limit_price or 0)
        if lim <= 0:
            errors.append(f"{sym}: limit_price 없음")
            continue
        div = abs(lim - q) / q * 100.0
        if div > policy.max_divergence_pct:
            errors.append(
                f"{sym}: 주문가 {lim:,.0f} vs 실시간 {q:,.0f} "
                f"괴리 {div:.2f}% > 한도 {policy.max_divergence_pct:.2f}%"
            )
    return (len(errors) == 0, errors, warnings, quotes)


def _order_value(req: BrokerOrderRequest) -> float:
    if req.estimated_value is not None and float(req.estimated_value) > 0:
        return float(req.estimated_value)
    if req.limit_price is not None:
        return float(req.limit_price) * int(req.quantity)
    return 0.0


def validate_live_execution(
    plan: LiveOrderPlan,
    requests: list[BrokerOrderRequest],
    policy: LiveExecutionPolicy,
    config: KISConfig,
    *,
    approved: bool,
    execute: bool,
    final_confirm: str | None,
    session_now: datetime | None = None,
    session_policy: "TradingSessionPolicy | None" = None,
) -> tuple[bool, list[str]]:
    """
    실매수 직전 검증. (True, []) 이면 통과.

    - `KIS_ENV=live` 및 `policy.allow_live_env` 필수.
    - 주문 수·금액·BUY/LIMIT·6자리 종목·allow_symbols 화이트리스트.
    """
    errs: list[str] = []

    if policy.require_trading_session and execute:
        from deepsignal.live_trading.trading_session import (
            is_trading_session_open,
            load_trading_session_policy_from_env,
        )

        sp = session_policy or load_trading_session_policy_from_env()
        sr = is_trading_session_open(now=session_now, policy=sp)
        if not sr.is_open:
            errs.append(f"trading session closed: {sr.reason}")

    if not approved:
        errs.append("approved must be True")
    if not execute:
        errs.append("execute must be True for live path")
    fc = (final_confirm or "").strip()
    if fc != policy.require_final_confirm_text:
        errs.append(
            f"final_confirm must be exactly {policy.require_final_confirm_text!r}, "
            f"got {final_confirm!r}"
        )
    if not policy.allow_live_env:
        errs.append("allow_live_env policy flag must be True (--allow-live-env)")
    if (config.env or "").strip().lower() != "live":
        errs.append("KIS_ENV must be 'live' for real order submission")

    if plan.status != "PENDING_APPROVAL":
        errs.append(f"plan.status must be PENDING_APPROVAL, got {plan.status!r}")
    if not plan.approval_required:
        errs.append("plan.approval_required must be true")

    if len(requests) > int(policy.max_orders):
        errs.append(f"order count {len(requests)} exceeds max_orders={policy.max_orders}")

    allow = policy.allow_symbols
    if allow is not None and len(allow) == 0:
        errs.append("allow_symbols is set but empty (no symbols permitted)")
    if allow is not None:
        allow_set = {s.strip().zfill(6) if s.strip().isdigit() else s.strip() for s in allow}
        for i, r in enumerate(requests):
            sym = (r.symbol or "").strip().zfill(6) if re.fullmatch(r"\d{1,6}", (r.symbol or "").strip()) else (r.symbol or "").strip()
            if sym not in allow_set:
                errs.append(f"requests[{i}] symbol {r.symbol!r} not in allow_symbols whitelist")

    total_val = 0.0
    for i, r in enumerate(requests):
        if (r.side or "").strip().upper() != "BUY":
            errs.append(f"requests[{i}]: only BUY allowed, got {r.side!r}")
        if (r.order_type or "").strip().upper() != "LIMIT":
            errs.append(f"requests[{i}]: only LIMIT allowed, got {r.order_type!r}")
        if int(r.quantity) <= 0:
            errs.append(f"requests[{i}]: quantity must be > 0")
        if r.limit_price is None or float(r.limit_price) <= 0:
            errs.append(f"requests[{i}]: limit_price must be > 0")
        sym = (r.symbol or "").strip()
        pdno = sym.zfill(6) if re.fullmatch(r"\d{1,6}", sym) else sym
        if not re.fullmatch(r"\d{6}", pdno):
            errs.append(f"requests[{i}]: symbol must be domestic 6-digit code, got {r.symbol!r}")
        ov = _order_value(r)
        total_val += ov
        if ov > float(policy.max_single_order_value):
            errs.append(
                f"requests[{i}]: order value {ov} exceeds max_single_order_value="
                f"{policy.max_single_order_value}"
            )

    if total_val > float(policy.max_total_order_value):
        errs.append(
            f"total order value {total_val} exceeds max_total_order_value={policy.max_total_order_value}"
        )

    return (len(errs) == 0, errs)


def session_blocked_errors_only(errors: list[str]) -> bool:
    """오류가 전부 trading session 관련인지."""
    if not errors:
        return False
    return all("trading session closed" in e for e in errors)
