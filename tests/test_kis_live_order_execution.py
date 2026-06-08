"""[실전-4] execute_live_order_plan + KISBroker mock session (no real HTTP)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deepsignal.live_trading.trading_session import TradingSessionResult

from deepsignal.live_trading.kis_broker import KISBroker
from deepsignal.live_trading.kis_config import KISConfig
from deepsignal.live_trading.live_execution_guard import LiveExecutionPolicy
from deepsignal.live_trading.live_order_executor import execute_live_order_plan


def _plan_path(tmp_path: Path) -> Path:
    d = {
        "date": "2026-05-15",
        "status": "PENDING_APPROVAL",
        "approval_required": True,
        "dry_run": True,
        "capital": 1_000_000.0,
        "investable_cash": 1_000_000.0,
        "cash_buffer": 0.0,
        "currency": "KRW",
        "orders": [
            {
                "symbol": "005930",
                "side": "BUY",
                "target_weight": 1.0,
                "target_value": 70_000.0,
                "estimated_price": 70_000.0,
                "estimated_qty": 1,
                "estimated_order_value": 70_000.0,
                "reason": "t",
                "warnings": [],
            }
        ],
        "warnings": [],
    }
    p = tmp_path / "live_order_plan_20260515.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def _live_cfg() -> KISConfig:
    return KISConfig(
        app_key="app",
        app_secret="sec",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="live",
    )


def _mock_session_order_success() -> MagicMock:
    class TokResp:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "tok", "expires_in": 600}

    class OrderResp:
        status_code = 200
        text = '{"rt_cd":"0","msg1":"ok","output":{"ODNO":"777"}}'

        def json(self) -> dict:
            return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "777"}}

    session = MagicMock()

    def post(url: str, **kwargs: object) -> TokResp | OrderResp:
        if "oauth2/tokenP" in url:
            return TokResp()
        if "order-cash" in url:
            return OrderResp()
        raise AssertionError(url)

    session.post.side_effect = post
    return session


def _mock_session_order_fail() -> MagicMock:
    class TokResp:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "tok", "expires_in": 600}

    class BadResp:
        status_code = 200
        text = '{"rt_cd":"9","msg1":"mock failure"}'

        def json(self) -> dict:
            return {"rt_cd": "9", "msg1": "mock failure"}

    session = MagicMock()

    def post(url: str, **kwargs: object) -> TokResp | BadResp:
        if "oauth2/tokenP" in url:
            return TokResp()
        if "order-cash" in url:
            return BadResp()
        raise AssertionError(url)

    session.post.side_effect = post
    return session


def test_execute_single_order_post_once(tmp_path: Path) -> None:
    p = _plan_path(tmp_path)
    session = _mock_session_order_success()
    br = KISBroker(_live_cfg(), safe_mode=True, session=session)
    pol = LiveExecutionPolicy(
        allow_live_env=True,
        allow_symbols=["005930"],
        max_single_order_value=100_000.0,
        max_total_order_value=200_000.0,
    )
    open_sr = TradingSessionResult(
        is_open=True,
        reason="regular trading session",
        market="KR",
        now="2026-05-15T10:00:00+09:00",
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )
    with patch(
        "deepsignal.live_trading.trading_session.is_trading_session_open",
        return_value=open_sr,
    ):
        r = execute_live_order_plan(
            p,
            br,
            approved=True,
            execute=True,
            dry_run=True,
            final_confirm="I_UNDERSTAND_REAL_ORDER",
            live_policy=pol,
        )
    assert r["status"] == "KIS_LIVE_ORDER_COMPLETED"
    assert r["actual_order_attempted"] is True
    assert r["actual_order_count"] == 1
    urls = [c[0][0] for c in session.post.call_args_list]
    assert sum(1 for u in urls if "order-cash" in u) == 1
    raw = r["results"][0]["raw"]
    assert isinstance(raw, dict)
    assert "response_body" in raw


def test_execute_failure_still_has_raw_in_audit(tmp_path: Path) -> None:
    p = _plan_path(tmp_path)
    session = _mock_session_order_fail()
    br = KISBroker(_live_cfg(), safe_mode=True, session=session)
    pol = LiveExecutionPolicy(
        allow_live_env=True,
        allow_symbols=["005930"],
        max_single_order_value=100_000.0,
        max_total_order_value=200_000.0,
    )
    open_sr = TradingSessionResult(
        is_open=True,
        reason="regular trading session",
        market="KR",
        now="2026-05-15T10:00:00+09:00",
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )
    with patch(
        "deepsignal.live_trading.trading_session.is_trading_session_open",
        return_value=open_sr,
    ):
        r = execute_live_order_plan(
            p,
            br,
            approved=True,
            execute=True,
            dry_run=True,
            final_confirm="I_UNDERSTAND_REAL_ORDER",
            live_policy=pol,
        )
    assert r["status"] == "KIS_LIVE_ORDER_FAILED"
    assert r["actual_order_attempted"] is True
    raw = r["results"][0]["raw"]
    assert isinstance(raw, dict)
    assert r["results"][0]["status"] == "KIS_ORDER_REJECTED"
