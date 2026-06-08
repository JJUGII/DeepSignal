"""LiveExecutionGuard / validate_live_execution ([실전-4])."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from deepsignal.live_trading.broker_interface import BrokerOrderRequest
from deepsignal.live_trading.kis_config import KISConfig
from deepsignal.live_trading.live_execution_guard import LiveExecutionPolicy, validate_live_execution
from deepsignal.live_trading.trading_session import TradingSessionPolicy
from deepsignal.live_trading.live_order_plan import LiveOrderItem, LiveOrderPlan


def _plan(**kwargs: object) -> LiveOrderPlan:
    base = LiveOrderPlan(
        date="2026-05-15",
        capital=1_000_000.0,
        investable_cash=1_000_000.0,
        cash_buffer=0.0,
        currency="KRW",
        orders=[
            LiveOrderItem(
                symbol="005930",
                side="BUY",
                target_weight=1.0,
                target_value=70_000.0,
                estimated_price=70_000.0,
                estimated_qty=1,
                estimated_order_value=70_000.0,
                reason="t",
            )
        ],
        warnings=[],
        status="PENDING_APPROVAL",
        approval_required=True,
    )
    for k, v in kwargs.items():
        setattr(base, str(k), v)
    return base


def _req(**kwargs: object) -> BrokerOrderRequest:
    d: dict[str, object] = {
        "symbol": "005930",
        "side": "BUY",
        "quantity": 1,
        "order_type": "LIMIT",
        "limit_price": 70_000.0,
        "estimated_value": 70_000.0,
    }
    d.update(kwargs)
    return BrokerOrderRequest(**d)


def _cfg(env: str = "live") -> KISConfig:
    return KISConfig(
        app_key="a",
        app_secret="b",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env=env,
    )


def _policy(**kwargs: object) -> LiveExecutionPolicy:
    return replace(
        LiveExecutionPolicy(
            allow_live_env=True,
            allow_symbols=["005930"],
            max_single_order_value=100_000.0,
            max_total_order_value=200_000.0,
        ),
        **kwargs,
    )


def test_guard_final_confirm_required() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req()],
        _policy(),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="WRONG",
    )
    assert ok is False
    assert any("final_confirm" in e for e in errs)


def test_guard_approved_required() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req()],
        _policy(),
        _cfg(),
        approved=False,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("approved" in e for e in errs)


def test_guard_execute_required() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req()],
        _policy(),
        _cfg(),
        approved=True,
        execute=False,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("execute" in e for e in errs)


def test_guard_allow_live_env_required() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req()],
        replace(_policy(), allow_live_env=False),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("allow_live_env" in e for e in errs)


def test_guard_kis_env_must_be_live() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req()],
        _policy(),
        _cfg("paper"),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("KIS_ENV" in e for e in errs)


def test_guard_max_orders() -> None:
    reqs = [
        _req(estimated_value=30_000.0, limit_price=30_000.0),
        _req(estimated_value=30_000.0, limit_price=30_000.0),
    ]
    ok, errs = validate_live_execution(
        _plan(),
        reqs,
        _policy(max_orders=1, max_total_order_value=500_000.0),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("max_orders" in e for e in errs)


def test_guard_max_total_order_value() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [
            _req(estimated_value=60_000.0, limit_price=60_000.0),
            _req(estimated_value=60_000.0, limit_price=60_000.0),
        ],
        _policy(max_orders=2, max_total_order_value=100_000.0, max_single_order_value=100_000.0),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("max_total_order_value" in e for e in errs)


def test_guard_max_single_order_value() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req(estimated_value=60_000.0, limit_price=60_000.0)],
        _policy(max_single_order_value=50_000.0),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("max_single_order_value" in e for e in errs)


def test_guard_rejects_sell() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req(side="SELL")],
        _policy(),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("BUY" in e for e in errs)


def test_guard_rejects_market() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req(order_type="MARKET")],
        _policy(),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("LIMIT" in e for e in errs)


def test_guard_rejects_non_domestic_symbol() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req(symbol="AAPL")],
        _policy(allow_symbols=None),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("6-digit" in e for e in errs)


def test_guard_allow_symbols_whitelist() -> None:
    ok, errs = validate_live_execution(
        _plan(),
        [_req()],
        _policy(allow_symbols=["000660"]),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("allow_symbols" in e for e in errs)


def test_guard_ok_minimal() -> None:
    session_now = datetime(2026, 5, 15, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    ok, errs = validate_live_execution(
        _plan(),
        [_req()],
        _policy(),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
        session_now=session_now,
    )
    assert ok is True
    assert errs == []


def test_guard_plan_status_not_pending() -> None:
    ok, errs = validate_live_execution(
        _plan(status="APPROVED"),
        [_req()],
        _policy(),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("PENDING_APPROVAL" in e for e in errs)


def test_guard_plan_approval_not_required() -> None:
    ok, errs = validate_live_execution(
        _plan(approval_required=False),
        [_req()],
        _policy(),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
    )
    assert ok is False
    assert any("approval_required" in e for e in errs)


def test_guard_trading_session_closed() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    closed_now = datetime(2026, 5, 15, 8, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    ok, errs = validate_live_execution(
        _plan(),
        [_req()],
        _policy(require_trading_session=True),
        _cfg(),
        approved=True,
        execute=True,
        final_confirm="I_UNDERSTAND_REAL_ORDER",
        session_now=closed_now,
        session_policy=TradingSessionPolicy(),
    )
    assert ok is False
    assert any("trading session closed" in e for e in errs)


def test_guard_trading_session_skipped_when_not_execute() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    closed_now = datetime(2026, 5, 15, 8, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    ok, errs = validate_live_execution(
        _plan(),
        [_req()],
        _policy(require_trading_session=True),
        _cfg("paper"),
        approved=True,
        execute=False,
        final_confirm=None,
        session_now=closed_now,
        session_policy=TradingSessionPolicy(),
    )
    assert ok is False
    assert not any("trading session closed" in e for e in errs)
