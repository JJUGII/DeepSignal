"""main.py live-order-guard-check 및 live-approve guard 통합 (mock only)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import main as main_mod
from deepsignal.live_trading.reconcile import ReconcileResult, write_latest_reconcile_state
from deepsignal.live_trading.trading_session import TradingSessionResult
from deepsignal.storage.database import (
    init_database,
    save_real_account_snapshot,
    save_real_order_history,
)


def _seed_guard_ok(db: str, out: Path) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    save_real_account_snapshot(
        db,
        now,
        "kis",
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
        total_market_value=0.0,
        total_equity=1_000_000.0,
        raw_payload={"timestamp": now, "positions": []},
    )
    report = out / "reconcile_live_account_seed.json"
    report.write_text(json.dumps({"success": True, "matched": ["005930"]}), encoding="utf-8")
    write_latest_reconcile_state(
        report,
        ReconcileResult(success=True, matched=["005930"]),
        output_dir=out,
    )


def _open_session_result() -> TradingSessionResult:
    now = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    return TradingSessionResult(
        is_open=True,
        reason="regular trading session",
        market="KR",
        now=now,
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )


def _kr_plan(tmp_path: Path) -> Path:
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


def test_live_order_guard_check_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "g.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed_guard_ok(db, tmp_path)
    rc = main_mod.main(
        [
            "live-order-guard-check",
            "--symbol",
            "005930",
            "--broker",
            "kis",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0


def test_live_order_guard_check_blocked_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "g2.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed_guard_ok(db, tmp_path)
    save_real_order_history(
        db,
        broker="kis",
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        status="KIS_ORDER_SUBMITTED",
        raw_payload={},
    )
    rc = main_mod.main(
        [
            "live-order-guard-check",
            "--symbol",
            "005930",
            "--broker",
            "kis",
            "--quantity",
            "1",
            "--limit-price",
            "70000",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 1


def test_live_approve_guard_blocked_no_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "block.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed_guard_ok(db, tmp_path)
    save_real_order_history(
        db,
        broker="kis",
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        status="KIS_ORDER_SUBMITTED",
        raw_payload={},
    )
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live")
    p = _kr_plan(tmp_path)

    session = MagicMock()

    def post(url: str, **kwargs: object) -> MagicMock:
        raise AssertionError(f"unexpected POST {url}")

    session.post.side_effect = post

    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=session):
        with patch(
            "deepsignal.live_trading.trading_session.is_trading_session_open",
            return_value=_open_session_result(),
        ):
            rc = main_mod.main(
                [
                    "live-approve",
                    "--broker",
                    "kis",
                    "--plan",
                    str(p),
                    "--approved",
                    "--execute",
                    "--allow-live-env",
                    "--final-confirm",
                    "I_UNDERSTAND_REAL_ORDER",
                    "--allow-symbol",
                    "005930",
                    "--max-single-order-value",
                    "100000",
                    "--max-total-order-value",
                    "200000",
                    "--output-dir",
                    str(tmp_path),
                ]
            )
    assert rc == 1
    audits = sorted(tmp_path.glob("live_approval_audit_*.json"))
    data = json.loads(audits[-1].read_text(encoding="utf-8"))
    assert data.get("status") == "LIVE_ORDER_BLOCKED_BY_GUARD"
    assert data.get("duplicate_risk_detected") is True
    assert data.get("guard_result")
    assert session.post.call_count == 0


def test_live_approve_execute_success_with_guard_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "ok.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed_guard_ok(db, tmp_path)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live")
    p = _kr_plan(tmp_path)

    class TokResp:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "tok", "expires_in": 600}

    class OrderResp:
        status_code = 200
        text = '{"rt_cd":"0","msg1":"ok","output":{"ODNO":"1"}}'

        def json(self) -> dict:
            return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "1"}}

    session = MagicMock()

    def post(url: str, **kwargs: object) -> TokResp | OrderResp:
        if "oauth2/tokenP" in url:
            return TokResp()
        if "order-cash" in url:
            return OrderResp()
        raise AssertionError(url)

    session.post.side_effect = post

    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=session):
        with patch(
            "deepsignal.live_trading.trading_session.is_trading_session_open",
            return_value=_open_session_result(),
        ):
            rc = main_mod.main(
                [
                    "live-approve",
                    "--broker",
                    "kis",
                    "--plan",
                    str(p),
                    "--approved",
                    "--execute",
                    "--allow-live-env",
                    "--final-confirm",
                    "I_UNDERSTAND_REAL_ORDER",
                    "--allow-symbol",
                    "005930",
                    "--max-single-order-value",
                    "100000",
                    "--max-total-order-value",
                    "200000",
                    "--output-dir",
                    str(tmp_path),
                ]
            )
    assert rc == 0
    urls = [c[0][0] for c in session.post.call_args_list]
    assert sum(1 for u in urls if "order-cash" in u) == 1
