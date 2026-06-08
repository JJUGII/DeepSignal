"""trading_session ([실전-9])."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from deepsignal.live_trading.trading_session import (
    TradingSessionPolicy,
    is_trading_session_open,
    parse_holidays,
)


def _kr(dt: datetime) -> datetime:
    return dt.replace(tzinfo=ZoneInfo("Asia/Seoul"))


def _pol(**kwargs: object) -> TradingSessionPolicy:
    base = TradingSessionPolicy()
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


def test_weekday_10_open() -> None:
    r = is_trading_session_open(
        now=_kr(datetime(2026, 5, 15, 10, 0, 0)),
        policy=_pol(),
    )
    assert r.is_open
    assert "regular" in r.reason.lower()


def test_weekday_0859_closed() -> None:
    r = is_trading_session_open(
        now=_kr(datetime(2026, 5, 15, 8, 59, 0)),
        policy=_pol(),
    )
    assert not r.is_open
    assert "before" in r.reason.lower()


def test_weekday_1531_closed() -> None:
    r = is_trading_session_open(
        now=_kr(datetime(2026, 5, 15, 15, 31, 0)),
        policy=_pol(),
    )
    assert not r.is_open
    assert "outside" in r.reason.lower()


def test_saturday_closed() -> None:
    r = is_trading_session_open(
        now=_kr(datetime(2026, 5, 16, 10, 0, 0)),
        policy=_pol(),
    )
    assert not r.is_open
    assert "weekend" in r.reason.lower()


def test_holiday_closed() -> None:
    r = is_trading_session_open(
        now=_kr(datetime(2026, 5, 15, 10, 0, 0)),
        policy=_pol(holidays=["2026-05-15"]),
    )
    assert not r.is_open
    assert "holiday" in r.reason.lower()


def test_allow_after_hours_outside_time_open() -> None:
    r = is_trading_session_open(
        now=_kr(datetime(2026, 5, 15, 8, 30, 0)),
        policy=_pol(allow_after_hours=True),
    )
    assert r.is_open


def test_allow_after_hours_weekend_still_closed() -> None:
    r = is_trading_session_open(
        now=_kr(datetime(2026, 5, 16, 8, 30, 0)),
        policy=_pol(allow_after_hours=True),
    )
    assert not r.is_open


def test_parse_holidays() -> None:
    assert parse_holidays("2026-01-01, 20260216") == ["2026-01-01", "2026-02-16"]


def test_1530_inclusive_open() -> None:
    r = is_trading_session_open(
        now=_kr(datetime(2026, 5, 15, 15, 30, 0)),
        policy=_pol(),
    )
    assert r.is_open
