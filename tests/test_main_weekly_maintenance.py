"""main.py weekly-maintenance CLI smoke tests."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import main as main_mod
from deepsignal.storage.database import init_database, save_real_account_snapshot


def _write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _seed_minimal(out: Path, db: Path) -> None:
    for name in [
        "OPS_DASHBOARD.html",
        "REPORT_INDEX.html",
        "DAILY_OPS_SUMMARY.md",
        "RISK_ALERT.md",
        "SELL_PLAN.md",
        "OPS_DRY_RUN.md",
    ]:
        _write(out / name, "<html></html>" if name.endswith(".html") else "# report")
    for name in [
        "live_account_snapshot_20260517_100000.json",
        "reconcile_live_account_20260517_100000.json",
        "risk_alert_20260517_100000.json",
        "ops_dashboard_20260517_100000.json",
        "sell_plan_20260517_100000.json",
        "daily_ops_summary_20260517_100000.json",
        "notification_audit_20260517_100000.json",
        "live_fill_summary_20260517_100000.json",
    ]:
        _write(out / name, json.dumps({"status": "OK", "success": True, "warnings": [], "items": [], "messages": [], "results": []}))
    _write(
        out / ".kis_token_cache.json",
        json.dumps({"access_token": "tok", "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat()}),
    )
    init_database(str(db))
    ts = datetime.now().isoformat(timespec="seconds")
    save_real_account_snapshot(
        str(db),
        ts,
        "kis",
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
        total_market_value=0.0,
        total_equity=1_000_000.0,
        raw_payload={"timestamp": ts},
    )


def test_weekly_maintenance_cli_smoke_creates_summary(tmp_path: Path, capsys) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_minimal(out, db)

    rc = main_mod.main(
        [
            "weekly-maintenance",
            "--output-dir",
            str(out),
            "--archive-dir",
            str(out / "archive"),
            "--db-path",
            str(db),
            "--keep-days",
            "365",
            "--keep-latest",
            "100",
        ]
    )

    assert rc == 0
    assert (out / "WEEKLY_MAINTENANCE.md").is_file()
    reports = sorted(out.glob("weekly_maintenance_*.json"))
    assert reports
    data = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert data["dry_run"] is True
    assert data["network_called"] is False
    assert data["cleanup_apply_used"] is False
    assert data["archive_move_used"] is False
    console = capsys.readouterr().out
    assert "DeepSignal weekly maintenance dry-run" in console
    assert "Final Status:" in console


def test_weekly_maintenance_parser_has_no_apply_option() -> None:
    parser = main_mod.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["weekly-maintenance", "--apply"])


def test_weekly_maintenance_cli_no_network_calls(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_minimal(out, db)
    post = Mock()
    get = Mock()
    with patch("requests.post", post), patch("requests.get", get):
        rc = main_mod.main(["weekly-maintenance", "--output-dir", str(out), "--db-path", str(db), "--keep-days", "365", "--keep-latest", "100"])
    assert rc == 0
    post.assert_not_called()
    get.assert_not_called()


def test_weekly_maintenance_cli_does_not_delete_or_move_files(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_minimal(out, db)
    old = out / "risk_alert_20200101_000000.json"
    _write(old, json.dumps({"status": "OK"}))
    old_ts = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(old, (old_ts, old_ts))

    rc = main_mod.main(["weekly-maintenance", "--output-dir", str(out), "--archive-dir", str(out / "archive"), "--db-path", str(db), "--keep-days", "1", "--keep-latest", "0"])

    assert rc == 0
    assert old.is_file()
    assert not (out / "archive" / old.name).exists()


def test_weekly_maintenance_summary_contains_steps(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_minimal(out, db)

    rc = main_mod.main(["weekly-maintenance", "--output-dir", str(out), "--db-path", str(db), "--keep-days", "365", "--keep-latest", "100"])

    assert rc == 0
    md = (out / "WEEKLY_MAINTENANCE.md").read_text(encoding="utf-8")
    assert "report_health_check" in md
    assert "cleanup_reports_dry_run" in md
    assert "daily_ops_summary" in md
    assert "html_dashboard" in md
    assert "report_index" in md
