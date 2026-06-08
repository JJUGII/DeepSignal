"""main.py report-health-check CLI smoke tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import main as main_mod
from deepsignal.storage.database import init_database, save_real_account_snapshot


def _seed_minimal(out: Path, db: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name in [
        "OPS_DASHBOARD.html",
        "REPORT_INDEX.html",
        "DAILY_OPS_SUMMARY.md",
        "RISK_ALERT.md",
        "SELL_PLAN.md",
        "OPS_DRY_RUN.md",
    ]:
        (out / name).write_text("<html></html>" if name.endswith(".html") else "# report", encoding="utf-8")
    for name in [
        "live_account_snapshot_20260517_100000.json",
        "reconcile_live_account_20260517_100000.json",
        "risk_alert_20260517_100000.json",
        "ops_dashboard_20260517_100000.json",
        "sell_plan_20260517_100000.json",
        "daily_ops_summary_20260517_100000.json",
    ]:
        (out / name).write_text(json.dumps({"status": "OK"}), encoding="utf-8")
    (out / ".kis_token_cache.json").write_text(
        json.dumps({"access_token": "tok", "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat()}),
        encoding="utf-8",
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


def test_report_health_cli_smoke_creates_reports(tmp_path: Path, capsys) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_minimal(out, db)

    rc = main_mod.main(["report-health-check", "--output-dir", str(out), "--db-path", str(db)])

    assert rc == 0
    assert (out / "REPORT_HEALTH.md").is_file()
    reports = sorted(out.glob("report_health_*.json"))
    assert reports
    data = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert data["status"] == "HEALTH_OK"
    assert data["network_called"] is False
    console = capsys.readouterr().out
    assert "DeepSignal report health check" in console
    assert "JSON:" in console
    assert "Markdown:" in console


def test_report_health_cli_no_network_calls(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_minimal(out, db)
    post = Mock()
    get = Mock()
    with patch("requests.post", post), patch("requests.get", get):
        rc = main_mod.main(["report-health-check", "--output-dir", str(out), "--db-path", str(db)])
    assert rc == 0
    post.assert_not_called()
    get.assert_not_called()


def test_report_health_cli_does_not_delete_files(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_minimal(out, db)
    marker = out / "._marker"
    marker.write_text("metadata", encoding="utf-8")

    rc = main_mod.main(["report-health-check", "--output-dir", str(out), "--db-path", str(db)])

    assert rc == 0
    assert marker.is_file()


def test_report_health_cli_missing_data_still_writes_report(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "missing.db"

    rc = main_mod.main(["report-health-check", "--output-dir", str(out), "--db-path", str(db)])

    assert rc == 0
    assert (out / "REPORT_HEALTH.md").is_file()
    data = json.loads(sorted(out.glob("report_health_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["status"] == "HEALTH_NO_DATA"
