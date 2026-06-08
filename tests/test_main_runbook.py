"""main.py pre-trade-runbook / post-trade-runbook CLI ([실전-10])."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import main as main_mod
from deepsignal.live_trading.broker_interface import BrokerPosition
from deepsignal.live_trading.reconcile import ReconcileResult, write_latest_reconcile_state
from deepsignal.live_trading.trading_session import TradingSessionResult
from deepsignal.storage.database import init_database, save_real_account_snapshot, save_real_positions


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


def _kis_session() -> MagicMock:
    class Tok:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "tok", "expires_in": 600}

    class BalResp:
        status_code = 200

        def json(self) -> dict:
            return {
                "rt_cd": "0",
                "output1": [],
                "output2": [{"dnca_tot_amt": "1000000", "ord_psbl_cash": "900000"}],
            }

    session = MagicMock()

    def get_side_effect(url: str, **kwargs: object) -> BalResp:
        if "inquire-balance" in url:
            return BalResp()
        raise AssertionError(url)

    def post_side_effect(url: str, **kwargs: object) -> Tok:
        if "oauth2/tokenP" in url:
            return Tok()
        raise AssertionError(url)

    session.get.side_effect = get_side_effect
    session.post.side_effect = post_side_effect
    return session


def test_pre_trade_runbook_cli_blocked_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")
    p = _kr_plan(tmp_path)
    closed = TradingSessionResult(
        is_open=False,
        reason="outside regular trading hours",
        market="KR",
        now="2026-05-15T08:00:00+09:00",
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )
    with patch("deepsignal.live_trading.runbook.is_trading_session_open", return_value=closed):
        rc = main_mod.main(
            [
                "pre-trade-runbook",
                "--broker",
                "kis",
                "--network",
                "--plan",
                str(p),
                "--symbol",
                "005930",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert rc == 1
    assert (tmp_path / "PRE_TRADE_RUNBOOK.md").is_file()
    reps = list(tmp_path.glob("pre_trade_runbook_*.json"))
    assert reps
    data = json.loads(sorted(reps)[-1].read_text(encoding="utf-8"))
    assert data["final_status"] == "PRE_TRADE_BLOCKED"


def test_pre_trade_runbook_cli_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "cli.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")
    p = _kr_plan(tmp_path)
    open_sr = TradingSessionResult(
        is_open=True,
        reason="regular trading session",
        market="KR",
        now=datetime(2026, 5, 15, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )
    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=_kis_session()):
        with patch("deepsignal.live_trading.runbook.is_trading_session_open", return_value=open_sr):
            rc = main_mod.main(
                [
                    "pre-trade-runbook",
                    "--broker",
                    "kis",
                    "--network",
                    "--plan",
                    str(p),
                    "--symbol",
                    "005930",
                    "--quantity",
                    "1",
                    "--limit-price",
                    "70000",
                    "--allow-symbol",
                    "005930",
                    "--output-dir",
                    str(tmp_path),
                    "--now",
                    "2026-05-15T10:00:00+09:00",
                ]
            )
    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("pre_trade_runbook_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["final_status"] == "PRE_TRADE_READY"


def test_pre_trade_requires_network(tmp_path: Path) -> None:
    rc = main_mod.main(
        [
            "pre-trade-runbook",
            "--plan",
            str(_kr_plan(tmp_path)),
            "--symbol",
            "005930",
        ]
    )
    assert rc == 1


def test_post_trade_runbook_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "post.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")
    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=_kis_session()):
        rc = main_mod.main(
            [
                "post-trade-runbook",
                "--broker",
                "kis",
                "--network",
                "--order-id",
                "1",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert rc in (0, 1)
    assert (tmp_path / "POST_TRADE_RUNBOOK.md").is_file()
    assert list(tmp_path.glob("post_trade_runbook_*.json"))


def test_post_trade_runbook_cli_with_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "post_summary.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    now = datetime.now().isoformat(timespec="seconds")
    save_real_positions(
        db,
        now,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": 1,
                "avg_price": 70_000.0,
                "current_price": 71_000.0,
                "market_value": 71_000.0,
                "raw": {},
            }
        ],
    )
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")
    match_pos = [
        BrokerPosition(
            symbol="005930",
            quantity=1,
            avg_price=70_000.0,
            current_price=71_000.0,
            market_value=71_000.0,
        )
    ]
    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=_kis_session()):
        with patch("deepsignal.live_trading.kis_broker.KISBroker.place_order") as mock_place:
            with patch("deepsignal.live_trading.kis_broker.KISBroker.get_positions", return_value=match_pos):
                with patch("deepsignal.live_trading.kis_broker.KISBroker.get_order_status", return_value=[]):
                    rc = main_mod.main(
                        [
                            "post-trade-runbook",
                            "--broker",
                            "kis",
                            "--network",
                            "--order-id",
                            "1",
                            "--with-summary",
                            "--output-dir",
                            str(tmp_path),
                        ]
                    )
    mock_place.assert_not_called()
    assert rc == 0
    assert (tmp_path / "OPS_DASHBOARD.html").is_file()
    data = json.loads(sorted(tmp_path.glob("post_trade_runbook_*.json"))[-1].read_text(encoding="utf-8"))
    reports = data["summary"]["generated_reports"]
    assert reports["daily_ops_summary_json"].endswith(".json")
    assert reports["html_dashboard"].endswith("OPS_DASHBOARD.html")
    assert any(step["step_name"] == "html_dashboard" for step in data["steps"])


def test_post_trade_runbook_cli_risk_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "risk_post.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    now = datetime.now().isoformat(timespec="seconds")
    save_real_positions(
        db,
        now,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": 1,
                "avg_price": 70_000.0,
                "current_price": 64_000.0,
                "market_value": 64_000.0,
                "raw": {},
            }
        ],
    )
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")
    match_pos = [
        BrokerPosition(
            symbol="005930",
            quantity=1,
            avg_price=70_000.0,
            current_price=64_000.0,
            market_value=64_000.0,
        )
    ]
    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=_kis_session()):
        with patch("deepsignal.live_trading.kis_broker.KISBroker.place_order") as mock_place:
            with patch(
                "deepsignal.live_trading.kis_broker.KISBroker.get_positions",
                return_value=match_pos,
            ):
                with patch(
                    "deepsignal.live_trading.kis_broker.KISBroker.get_order_status",
                    return_value=[],
                ):
                    rc = main_mod.main(
                        [
                            "post-trade-runbook",
                            "--broker",
                            "kis",
                            "--network",
                            "--order-id",
                            "1",
                            "--output-dir",
                            str(tmp_path),
                        ]
                    )
    mock_place.assert_not_called()
    assert rc == 1
    assert list(tmp_path.glob("risk_alert_*.json"))
    md = (tmp_path / "POST_TRADE_RUNBOOK.md").read_text(encoding="utf-8")
    assert "## Risk Summary" in md
    data = json.loads(sorted(tmp_path.glob("post_trade_runbook_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["final_status"] == "POST_TRADE_RISK_ALERT"
    assert data["summary"]["risk_status"] == "STOP_LOSS_ALERT"
    from deepsignal.live_trading.runbook import RunbookResult, format_runbook_console

    rb = RunbookResult(
        mode="post_trade",
        success=False,
        started_at=data["started_at"],
        finished_at=data["finished_at"],
        steps=[],
        warnings=[],
        final_status=data["final_status"],
        summary=data["summary"],
    )
    console = format_runbook_console(rb)
    assert "POST_TRADE_RISK_ALERT" in console
    assert "stop-loss" in console.lower() or "005930" in console


def test_post_trade_runbook_cli_custom_risk_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "risk_policy_post.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    _seed(db, tmp_path)
    now = datetime.now().isoformat(timespec="seconds")
    save_real_positions(
        db,
        now,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": 1,
                "avg_price": 70_000.0,
                "current_price": 66_500.0,
                "market_value": 66_500.0,
                "raw": {},
            }
        ],
    )
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")
    match_pos = [
        BrokerPosition(
            symbol="005930",
            quantity=1,
            avg_price=70_000.0,
            current_price=66_500.0,
            market_value=66_500.0,
        )
    ]
    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=_kis_session()):
        with patch("deepsignal.live_trading.kis_broker.KISBroker.place_order") as mock_place:
            with patch("deepsignal.live_trading.kis_broker.KISBroker.get_positions", return_value=match_pos):
                with patch("deepsignal.live_trading.kis_broker.KISBroker.get_order_status", return_value=[]):
                    rc = main_mod.main(
                        [
                            "post-trade-runbook",
                            "--broker",
                            "kis",
                            "--network",
                            "--order-id",
                            "1",
                            "--output-dir",
                            str(tmp_path),
                            "--stop-loss-pct",
                            "-0.05",
                            "--take-profit-pct",
                            "0.12",
                            "--warn-loss-pct",
                            "-0.02",
                            "--warn-profit-pct",
                            "0.08",
                        ]
                    )
    mock_place.assert_not_called()
    assert rc == 1
    data = json.loads(sorted(tmp_path.glob("post_trade_runbook_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["summary"]["risk_status"] == "STOP_LOSS_ALERT"
    assert data["summary"]["risk_policy"] == {
        "stop_loss_pct": -0.05,
        "take_profit_pct": 0.12,
        "warn_loss_pct": -0.02,
        "warn_profit_pct": 0.08,
    }
    risk_data = json.loads(sorted(tmp_path.glob("risk_alert_*.json"))[-1].read_text(encoding="utf-8"))
    assert risk_data["policy"]["warn_profit_pct"] == 0.08
    md = (tmp_path / "POST_TRADE_RUNBOOK.md").read_text(encoding="utf-8")
    assert "## Risk Policy" in md
    assert "Stop loss: -5%" in md
