"""main.py weekly-report-bundle CLI smoke tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import main as main_mod
from deepsignal.storage.database import init_database, save_real_account_snapshot


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_operational_inputs(out: Path, db: Path) -> None:
    # Files consumed by weekly-maintenance/report-health/html/index.
    for name in [
        "OPS_DASHBOARD.html",
        "REPORT_INDEX.html",
        "REPORT_INDEX.md",
        "DAILY_OPS_SUMMARY.md",
        "RISK_ALERT.md",
        "SELL_PLAN.md",
        "OPS_DRY_RUN.md",
    ]:
        _write(out / name, "<html></html>" if name.endswith(".html") else f"# {name}")
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


def test_weekly_report_bundle_cli_smoke(tmp_path: Path, capsys) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_operational_inputs(out, db)

    rc = main_mod.main(["weekly-report-bundle", "--output-dir", str(out), "--bundle-dir", str(out / "weekly_bundles"), "--db-path", str(db)])

    assert rc == 0
    bundles = sorted((out / "weekly_bundles").glob("weekly_bundle_*"))
    assert bundles
    assert (bundles[-1] / "BUNDLE_INDEX.md").is_file()
    assert (bundles[-1] / "BUNDLE_INDEX.html").is_file()
    console = capsys.readouterr().out
    assert "DeepSignal weekly report bundle" in console
    assert "Bundle dir:" in console


def test_weekly_report_bundle_cli_no_network_or_delete_or_move(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_operational_inputs(out, db)
    marker = out / "risk_alert_20200101_000000.json"
    _write(marker, json.dumps({"status": "OK"}))
    post = Mock()
    get = Mock()
    monkeypatch.setattr("deepsignal.live_trading.notification_center.requests.post", post)
    monkeypatch.setattr("requests.get", get)

    rc = main_mod.main(["weekly-report-bundle", "--output-dir", str(out), "--bundle-dir", str(out / "weekly_bundles"), "--db-path", str(db)])

    assert rc == 0
    assert marker.is_file()
    assert not (out / "archive" / marker.name).exists()
    post.assert_not_called()
    get.assert_not_called()


def test_weekly_report_bundle_cli_zip_option(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_operational_inputs(out, db)

    rc = main_mod.main(["weekly-report-bundle", "--output-dir", str(out), "--bundle-dir", str(out / "weekly_bundles"), "--db-path", str(db), "--zip"])

    assert rc == 0
    zips = sorted((out / "weekly_bundles").glob("weekly_bundle_*.zip"))
    assert zips


def test_weekly_report_bundle_cli_open_default_not_called(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_operational_inputs(out, db)
    opened = Mock()
    monkeypatch.setattr("deepsignal.live_trading.weekly_report_bundle.webbrowser.open", opened)

    rc = main_mod.main(["weekly-report-bundle", "--output-dir", str(out), "--bundle-dir", str(out / "weekly_bundles"), "--db-path", str(db)])

    assert rc == 0
    opened.assert_not_called()
