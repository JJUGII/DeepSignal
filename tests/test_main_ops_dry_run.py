"""main.py ops-dry-run CLI smoke."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import main as main_mod
from deepsignal.live_trading.trading_session import TradingSessionResult
from deepsignal.storage.database import init_database, save_real_account_snapshot, save_real_positions


def _env(monkeypatch: pytest.MonkeyPatch, db: str) -> None:
    monkeypatch.setenv("DB_PATH", db)
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


def _seed(db: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    save_real_account_snapshot(
        db,
        ts,
        "kis",
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
        total_market_value=71_000.0,
        total_equity=1_071_000.0,
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
                "current_price": 71_000.0,
                "market_value": 71_000.0,
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


def test_ops_dry_run_cli_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "cli.db")
    _env(monkeypatch, db)
    init_database(db)
    _seed(db)
    _seed_output_reports(tmp_path)
    with patch("deepsignal.live_trading.trading_session.is_trading_session_open", return_value=_open_session()):
        rc = main_mod.main(["ops-dry-run", "--output-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "OPS_DRY_RUN.md").is_file()
    assert (tmp_path / "OPS_DASHBOARD.html").is_file()
    assert (tmp_path / "REPORT_INDEX.html").is_file()
    reports = sorted(tmp_path.glob("ops_dry_run_*.json"))
    assert reports
    data = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert data["final_status"] == "OPS_DRY_RUN_OK"


def test_ops_dry_run_cli_no_network_external_call_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "no_network.db")
    _env(monkeypatch, db)
    init_database(db)
    _seed(db)
    post = Mock()
    get = Mock()
    with patch("requests.post", post), patch("requests.get", get):
        with patch("deepsignal.live_trading.trading_session.is_trading_session_open", return_value=_open_session()):
            rc = main_mod.main(["ops-dry-run", "--output-dir", str(tmp_path)])
    assert rc == 0
    post.assert_not_called()
    get.assert_not_called()


def test_ops_dry_run_cli_markdown_json_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "reports.db")
    _env(monkeypatch, db)
    init_database(db)
    _seed(db)
    with patch("deepsignal.live_trading.trading_session.is_trading_session_open", return_value=_open_session()):
        rc = main_mod.main(["ops-dry-run", "--output-dir", str(tmp_path), "--archive-dir", str(tmp_path / "archive")])
    assert rc == 0
    md = (tmp_path / "OPS_DRY_RUN.md").read_text(encoding="utf-8")
    assert "## Steps" in md
    assert "## Generated Reports" in md
    data = json.loads(sorted(tmp_path.glob("ops_dry_run_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["actual_order_attempted"] is False
    assert data["network_called"] is False
