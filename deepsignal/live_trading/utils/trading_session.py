"""국내 정규장 주문 가능 시간 가드 ([실전-9])."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo


@dataclass
class TradingSessionPolicy:
    market: str = "KR"
    timezone: str = "Asia/Seoul"
    regular_open: str = "09:00"
    regular_close: str = "15:30"
    allow_weekends: bool = False
    holidays: list[str] | None = None
    allow_after_hours: bool = False


@dataclass
class TradingSessionResult:
    is_open: bool
    reason: str
    market: str
    now: str
    timezone: str
    session_open: str
    session_close: str
    warnings: list[str] = field(default_factory=list)


def parse_holidays(value: str | None) -> list[str]:
    """`2026-01-01,2026-02-16` 형식 → `YYYY-MM-DD` 목록."""
    if not value or not str(value).strip():
        return []
    out: list[str] = []
    for part in str(value).replace(" ", "").split(","):
        p = part.strip()
        if not p:
            continue
        if len(p) == 8 and p.isdigit():
            p = f"{p[0:4]}-{p[4:6]}-{p[6:8]}"
        out.append(p)
    return out


def _parse_hhmm(s: str) -> time:
    parts = str(s).strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"invalid time {s!r}, expected HH:MM")
    return time(int(parts[0]), int(parts[1]))


def load_trading_session_policy_from_env() -> TradingSessionPolicy:
    """환경 변수에서 `TradingSessionPolicy` 로드."""
    holidays_raw = os.environ.get("DEEPSIGNAL_MARKET_HOLIDAYS", "")
    allow_ah = os.environ.get("DEEPSIGNAL_ALLOW_AFTER_HOURS", "false").strip().lower()
    return TradingSessionPolicy(
        market=os.environ.get("DEEPSIGNAL_MARKET", "KR").strip() or "KR",
        timezone=os.environ.get("DEEPSIGNAL_MARKET_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul",
        regular_open=os.environ.get("DEEPSIGNAL_MARKET_OPEN", "09:00").strip() or "09:00",
        regular_close=os.environ.get("DEEPSIGNAL_MARKET_CLOSE", "15:30").strip() or "15:30",
        allow_weekends=False,
        holidays=parse_holidays(holidays_raw) or None,
        allow_after_hours=allow_ah in ("1", "true", "yes", "on"),
    )


def is_trading_session_open(
    now: datetime | None = None,
    policy: TradingSessionPolicy | None = None,
) -> TradingSessionResult:
    """
    현재(또는 `now`) 주문 가능 세션 여부.

    기본: 평일 09:00~15:30 (Asia/Seoul), 주말·`holidays`·장외 차단.
    """
    pol = policy or load_trading_session_policy_from_env()
    warnings: list[str] = []
    try:
        tz = ZoneInfo(pol.timezone)
    except Exception as e:
        return TradingSessionResult(
            is_open=False,
            reason=f"invalid timezone {pol.timezone!r}: {e}",
            market=pol.market,
            now="",
            timezone=pol.timezone,
            session_open=pol.regular_open,
            session_close=pol.regular_close,
            warnings=warnings,
        )

    if now is None:
        dt = datetime.now(tz)
    elif now.tzinfo is None:
        dt = now.replace(tzinfo=tz)
    else:
        dt = now.astimezone(tz)

    now_iso = dt.isoformat(timespec="seconds")
    d = dt.date()
    holiday_set = set(pol.holidays or [])
    date_key = d.isoformat()

    if date_key in holiday_set:
        return TradingSessionResult(
            is_open=False,
            reason=f"market holiday ({date_key})",
            market=pol.market,
            now=now_iso,
            timezone=pol.timezone,
            session_open=pol.regular_open,
            session_close=pol.regular_close,
            warnings=warnings,
        )

    wd = dt.weekday()
    if wd >= 5 and not pol.allow_weekends:
        day_name = "Saturday" if wd == 5 else "Sunday"
        return TradingSessionResult(
            is_open=False,
            reason=f"weekend ({day_name})",
            market=pol.market,
            now=now_iso,
            timezone=pol.timezone,
            session_open=pol.regular_open,
            session_close=pol.regular_close,
            warnings=warnings,
        )

    if not pol.allow_after_hours:
        try:
            open_t = _parse_hhmm(pol.regular_open)
            close_t = _parse_hhmm(pol.regular_close)
        except ValueError as e:
            return TradingSessionResult(
                is_open=False,
                reason=str(e),
                market=pol.market,
                now=now_iso,
                timezone=pol.timezone,
                session_open=pol.regular_open,
                session_close=pol.regular_close,
                warnings=warnings,
            )
        cur = dt.time().replace(second=0, microsecond=0)
        if cur < open_t:
            return TradingSessionResult(
                is_open=False,
                reason="before regular trading session",
                market=pol.market,
                now=now_iso,
                timezone=pol.timezone,
                session_open=pol.regular_open,
                session_close=pol.regular_close,
                warnings=warnings,
            )
        if cur > close_t:
            return TradingSessionResult(
                is_open=False,
                reason="outside regular trading hours",
                market=pol.market,
                now=now_iso,
                timezone=pol.timezone,
                session_open=pol.regular_open,
                session_close=pol.regular_close,
                warnings=warnings,
            )

    return TradingSessionResult(
        is_open=True,
        reason="regular trading session",
        market=pol.market,
        now=now_iso,
        timezone=pol.timezone,
        session_open=pol.regular_open,
        session_close=pol.regular_close,
        warnings=warnings,
    )


def trading_session_result_to_audit_fields(result: TradingSessionResult) -> dict[str, Any]:
    """감사 로그용 세션 요약."""
    return {
        "trading_session": {
            "is_open": result.is_open,
            "reason": result.reason,
            "market": result.market,
            "now": result.now,
            "timezone": result.timezone,
            "session_open": result.session_open,
            "session_close": result.session_close,
            "warnings": list(result.warnings),
        },
        "trading_session_open": result.is_open,
        "trading_session_reason": result.reason,
    }
