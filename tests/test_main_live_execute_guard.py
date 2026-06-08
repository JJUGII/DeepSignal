"""main.py live-approve [실전-4] CLI 가드 (mock HTTP only)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import main as main_mod


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


def test_cli_execute_without_final_confirm_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live")
    p = _kr_plan(tmp_path)
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
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 1
    audits = sorted(tmp_path.glob("live_approval_audit_*.json"))
    data = json.loads(audits[-1].read_text(encoding="utf-8"))
    assert data.get("status") == "LIVE_EXECUTION_BLOCKED"
    errs = data.get("errors") or []
    assert any("final_confirm" in str(e).lower() for e in errs)


def test_cli_full_execute_success_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "exec.db")
    monkeypatch.setenv("DB_PATH", db)
    from deepsignal.storage.database import init_database

    init_database(db)
    from datetime import datetime
    import json
    from deepsignal.live_trading.reconcile import ReconcileResult, write_latest_reconcile_state
    from deepsignal.storage.database import save_real_account_snapshot

    now = datetime.now().isoformat(timespec="seconds")
    save_real_account_snapshot(
        db,
        now,
        "kis",
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
        total_market_value=0.0,
        total_equity=1_000_000.0,
        raw_payload={"timestamp": now},
    )
    report = tmp_path / "reconcile_seed.json"
    report.write_text(json.dumps({"success": True}), encoding="utf-8")
    write_latest_reconcile_state(
        report,
        ReconcileResult(success=True, matched=["005930"]),
        output_dir=tmp_path,
    )
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live")
    p = _kr_plan(tmp_path)
    from deepsignal.live_trading.trading_session import TradingSessionResult

    open_sr = TradingSessionResult(
        is_open=True,
        reason="regular trading session",
        market="KR",
        now=now,
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )
    shared = _mock_session()
    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=shared):
        with patch(
            "deepsignal.live_trading.trading_session.is_trading_session_open",
            return_value=open_sr,
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
    audits = sorted(tmp_path.glob("live_approval_audit_*.json"))
    data = json.loads(audits[-1].read_text(encoding="utf-8"))
    assert data.get("status") == "KIS_LIVE_ORDER_COMPLETED"
    assert data.get("actual_order_attempted") is True
    assert data.get("results") and data["results"][0].get("raw")
    urls = [c[0][0] for c in shared.post.call_args_list]
    assert sum(1 for u in urls if "order-cash" in u) == 1
