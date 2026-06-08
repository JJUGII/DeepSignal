"""main.py live-approve --require-pre-trade-runbook ([실전-11])."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import main as main_mod
from deepsignal.live_trading.reconcile import ReconcileResult, write_latest_reconcile_state
from deepsignal.live_trading.trading_session import TradingSessionResult
from deepsignal.storage.database import init_database, save_real_account_snapshot


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
    p = tmp_path / "live_order_plan.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def _seed(db: str, out: Path) -> None:
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
    report = out / "reconcile_seed.json"
    report.write_text(json.dumps({"success": True}), encoding="utf-8")
    write_latest_reconcile_state(
        report,
        ReconcileResult(success=True, matched=[]),
        output_dir=out,
    )


def _runbook_report(tmp_path: Path, plan: Path, *, finished_at: str | None = None) -> Path:
    ts = finished_at or datetime.now().isoformat(timespec="seconds")
    body = {
        "mode": "pre_trade",
        "final_status": "PRE_TRADE_READY",
        "finished_at": ts,
        "started_at": ts,
        "summary": {
            "plan_path": plan.resolve().as_posix(),
            "symbol": "005930",
            "quantity": 1,
            "limit_price": 70000.0,
        },
    }
    p = tmp_path / "pre_trade_runbook_20260515_120000.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def _mock_session() -> MagicMock:
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
    return session


def _open_session(now: str) -> TradingSessionResult:
    return TradingSessionResult(
        is_open=True,
        reason="regular trading session",
        market="KR",
        now=now,
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )


def _live_execute_args(tmp_path: Path, plan: Path, *, require: bool) -> list[str]:
    args = [
        "live-approve",
        "--broker",
        "kis",
        "--plan",
        str(plan),
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
    if require:
        args.append("--require-pre-trade-runbook")
    return args


def test_without_require_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "norb.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live")
    plan = _kr_plan(tmp_path)
    now = datetime.now().isoformat(timespec="seconds")
    shared = _mock_session()
    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=shared):
        with patch(
            "deepsignal.live_trading.trading_session.is_trading_session_open",
            return_value=_open_session(now),
        ):
            rc = main_mod.main(_live_execute_args(tmp_path, plan, require=False))
    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("live_approval_audit_*.json"))[-1].read_text(encoding="utf-8"))
    assert data.get("require_pre_trade_runbook") is False
    urls = [c[0][0] for c in shared.post.call_args_list]
    assert sum(1 for u in urls if "order-cash" in u) == 1


def test_require_no_report_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "nobook.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live")
    plan = _kr_plan(tmp_path)
    now = datetime.now().isoformat(timespec="seconds")
    shared = _mock_session()

    def post_no_order(url: str, **kwargs: object) -> object:
        if "order-cash" in url:
            raise AssertionError("order-cash must not be called")
        r = MagicMock()
        r.raise_for_status = lambda: None
        r.json = lambda: {"access_token": "t", "expires_in": 600}
        return r

    shared.post.side_effect = post_no_order

    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=shared):
        with patch(
            "deepsignal.live_trading.trading_session.is_trading_session_open",
            return_value=_open_session(now),
        ):
            rc = main_mod.main(_live_execute_args(tmp_path, plan, require=True))
    assert rc == 1
    data = json.loads(sorted(tmp_path.glob("live_approval_audit_*.json"))[-1].read_text(encoding="utf-8"))
    assert data.get("status") == "LIVE_EXECUTION_BLOCKED_BY_RUNBOOK"
    assert data.get("pre_trade_runbook_passed") is False


def test_require_expired_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "exp.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live")
    plan = _kr_plan(tmp_path)
    old = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    _runbook_report(tmp_path, plan, finished_at=old)
    now = datetime.now().isoformat(timespec="seconds")
    shared = _mock_session()

    def post_block_order(url: str, **kwargs: object) -> object:
        if "order-cash" in url:
            raise AssertionError("order-cash must not be called")
        r = MagicMock()
        r.raise_for_status = lambda: None
        r.json = lambda: {"access_token": "t", "expires_in": 600}
        return r

    shared.post.side_effect = post_block_order

    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=shared):
        with patch(
            "deepsignal.live_trading.trading_session.is_trading_session_open",
            return_value=_open_session(now),
        ):
            rc = main_mod.main(
                _live_execute_args(tmp_path, plan, require=True)
                + ["--pre-trade-runbook-max-age-minutes", "10"]
            )
    assert rc == 1
    data = json.loads(sorted(tmp_path.glob("live_approval_audit_*.json"))[-1].read_text(encoding="utf-8"))
    assert data.get("status") == "LIVE_EXECUTION_BLOCKED_BY_RUNBOOK"


def test_require_mismatch_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "mis.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live")
    plan = _kr_plan(tmp_path)
    rb = _runbook_report(tmp_path, plan)
    body = json.loads(rb.read_text(encoding="utf-8"))
    body["summary"]["symbol"] = "000660"
    rb.write_text(json.dumps(body), encoding="utf-8")
    now = datetime.now().isoformat(timespec="seconds")
    shared = _mock_session()

    def post_block_order(url: str, **kwargs: object) -> object:
        if "order-cash" in url:
            raise AssertionError("order-cash must not be called")
        r = MagicMock()
        r.raise_for_status = lambda: None
        r.json = lambda: {"access_token": "t", "expires_in": 600}
        return r

    shared.post.side_effect = post_block_order

    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=shared):
        with patch(
            "deepsignal.live_trading.trading_session.is_trading_session_open",
            return_value=_open_session(now),
        ):
            rc = main_mod.main(_live_execute_args(tmp_path, plan, require=True))
    assert rc == 1
    data = json.loads(sorted(tmp_path.glob("live_approval_audit_*.json"))[-1].read_text(encoding="utf-8"))
    assert data.get("status") == "LIVE_EXECUTION_BLOCKED_BY_RUNBOOK"
    guard = data.get("pre_trade_runbook_guard")
    assert isinstance(guard, dict)


def test_require_valid_proceeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "ok.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live")
    plan = _kr_plan(tmp_path)
    _runbook_report(tmp_path, plan)
    now = datetime.now().isoformat(timespec="seconds")
    shared = _mock_session()
    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=shared):
        with patch(
            "deepsignal.live_trading.trading_session.is_trading_session_open",
            return_value=_open_session(now),
        ):
            rc = main_mod.main(_live_execute_args(tmp_path, plan, require=True))
    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("live_approval_audit_*.json"))[-1].read_text(encoding="utf-8"))
    assert data.get("pre_trade_runbook_passed") is True
    assert data.get("status") == "KIS_LIVE_ORDER_COMPLETED"
    urls = [c[0][0] for c in shared.post.call_args_list]
    assert sum(1 for u in urls if "order-cash" in u) == 1
