"""ops_dry_run: one-command dry-run operations."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deepsignal.live_trading.broker_interface import BrokerCashBalance, BrokerPosition
from deepsignal.live_trading.ops_dry_run import (
    STATUS_OK,
    STATUS_WARNING,
    format_ops_dry_run_console,
    run_ops_dry_run,
    write_ops_dry_run_report,
)
from deepsignal.live_trading.trading_session import TradingSessionResult
from deepsignal.storage.database import init_database, save_real_account_snapshot, save_real_positions


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")


def _open_session() -> TradingSessionResult:
    return TradingSessionResult(
        is_open=True,
        reason="regular trading session",
        market="KR",
        now="2026-05-15T10:00:00+09:00",
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )


def _seed_position(db: str, *, current_price: float = 71_000.0) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    save_real_account_snapshot(
        db,
        ts,
        "kis",
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
        total_market_value=current_price,
        total_equity=1_000_000.0 + current_price,
        raw_payload={"timestamp": ts},
    )
    save_real_positions(
        db,
        ts,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": 1,
                "avg_price": 70_000.0,
                "current_price": current_price,
                "market_value": current_price,
                "raw": {},
            }
        ],
    )


def _seed_output_reports(out: Path) -> None:
    token = datetime.now().strftime("%Y%m%d")
    (out / f"live_account_snapshot_{token}_000001.json").write_text(
        json.dumps({"timestamp": datetime.now().isoformat(timespec="seconds"), "cash": {"cash": 1_000_000}, "positions": []}),
        encoding="utf-8",
    )
    (out / f"reconcile_live_account_{token}_000001.json").write_text(
        json.dumps({"success": True, "matched": []}),
        encoding="utf-8",
    )
    (out / f"notification_audit_{token}_000001.json").write_text(
        json.dumps({"dry_run": True, "channel": "telegram", "messages": [], "results": []}),
        encoding="utf-8",
    )


def _mock_broker() -> MagicMock:
    br = MagicMock()
    br.config.env = "paper"
    br.get_access_token.return_value = "tok"
    br.get_cash_balance.return_value = BrokerCashBalance(cash=1_000_000.0, withdrawable_cash=900_000.0)
    br.get_positions.return_value = [
        BrokerPosition(symbol="005930", quantity=1, avg_price=70_000.0, current_price=71_000.0, market_value=71_000.0)
    ]
    return br


def test_no_network_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    db = str(tmp_path / "ops.db")
    init_database(db)
    _seed_position(db)
    _seed_output_reports(tmp_path)
    monkeypatch.setattr("deepsignal.live_trading.trading_session.is_trading_session_open", lambda policy=None: _open_session())
    result = run_ops_dry_run(db_path=db, output_dir=tmp_path, network=False)
    assert result.final_status == STATUS_OK
    names = [s.name for s in result.steps]
    assert names == [
        "trading_session",
        "kis_check_offline",
        "risk_check",
        "ops_dashboard",
        "sell_plan",
        "daily_ops_summary",
        "html_dashboard",
        "report_index",
    ]
    assert "account_sync" not in names
    assert "reconcile" not in names


def test_network_false_skips_kis_network_functions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    db = str(tmp_path / "skip.db")
    init_database(db)
    _seed_position(db)
    br = _mock_broker()
    monkeypatch.setattr("deepsignal.live_trading.trading_session.is_trading_session_open", lambda policy=None: _open_session())
    run_ops_dry_run(db_path=db, output_dir=tmp_path, network=False, kis_broker=br)
    br.get_access_token.assert_not_called()
    br.get_cash_balance.assert_not_called()
    br.get_positions.assert_not_called()


def test_network_true_includes_sync_and_reconcile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    db = str(tmp_path / "network.db")
    init_database(db)
    _seed_position(db)
    _seed_output_reports(tmp_path)
    monkeypatch.setattr("deepsignal.live_trading.trading_session.is_trading_session_open", lambda policy=None: _open_session())
    br = _mock_broker()
    result = run_ops_dry_run(db_path=db, output_dir=tmp_path, network=True, kis_broker=br)
    names = [s.name for s in result.steps]
    assert "kis_check_network" in names
    assert "account_sync" in names
    assert "reconcile" in names
    br.get_access_token.assert_called_once()
    assert result.final_status == STATUS_OK


def test_risk_warning_sets_final_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    db = str(tmp_path / "risk.db")
    init_database(db)
    _seed_position(db, current_price=67_000.0)
    monkeypatch.setattr("deepsignal.live_trading.trading_session.is_trading_session_open", lambda policy=None: _open_session())
    result = run_ops_dry_run(db_path=db, output_dir=tmp_path, network=False)
    assert result.final_status == STATUS_WARNING
    assert any(s.name == "risk_check" and s.status == "WARNING" for s in result.steps)


def test_generated_report_paths_included(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    db = str(tmp_path / "paths.db")
    init_database(db)
    _seed_position(db)
    _seed_output_reports(tmp_path)
    monkeypatch.setattr("deepsignal.live_trading.trading_session.is_trading_session_open", lambda policy=None: _open_session())
    result = run_ops_dry_run(db_path=db, output_dir=tmp_path, archive_dir=tmp_path / "archive", network=False)
    jp, mp = write_ops_dry_run_report(result, output_dir=tmp_path)
    assert jp.is_file()
    assert mp.is_file()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["no_orders_placed"] is True
    assert any(s["name"] == "html_dashboard" and s["output_paths"]["html"].endswith("OPS_DASHBOARD.html") for s in data["steps"])
    md = mp.read_text(encoding="utf-8")
    assert "REPORT_INDEX.html" in md
    console = format_ops_dry_run_console(result)
    assert "Final: OPS_DRY_RUN_OK" in console


def test_ops_dry_run_source_has_no_order_trigger_strings() -> None:
    src = Path("deepsignal/live_trading/ops_dry_run.py").read_text(encoding="utf-8")
    assert "live-approve" not in src
    assert "live_approve" not in src
    assert "order-cash" not in src
    assert "--execute" not in src
