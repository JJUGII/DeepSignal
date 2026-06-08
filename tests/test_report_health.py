"""report_health.py — 운영 산출물/DB health check."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from deepsignal.live_trading.report_health import (
    HEALTH_NO_DATA,
    HEALTH_OK,
    HEALTH_WARNING,
    run_report_health_check,
    write_report_health,
)
from deepsignal.storage.database import init_database, save_real_account_snapshot, save_real_positions


STATIC_REPORTS = [
    "OPS_DASHBOARD.html",
    "REPORT_INDEX.html",
    "DAILY_OPS_SUMMARY.md",
    "RISK_ALERT.md",
    "SELL_PLAN.md",
    "OPS_DRY_RUN.md",
]

JSON_REPORTS = [
    "live_account_snapshot_20260517_100000.json",
    "reconcile_live_account_20260517_100000.json",
    "risk_alert_20260517_100000.json",
    "ops_dashboard_20260517_100000.json",
    "sell_plan_20260517_100000.json",
    "daily_ops_summary_20260517_100000.json",
]


def _write(path: Path, text: str = "{}") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _seed_fresh_outputs(out: Path) -> None:
    for name in STATIC_REPORTS:
        _write(out / name, "<html></html>" if name.endswith(".html") else "# report")
    for name in JSON_REPORTS:
        _write(out / name, json.dumps({"status": "OK"}))
    _write(
        out / ".kis_token_cache.json",
        json.dumps(
            {
                "access_token": "tok",
                "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
                "env": "paper",
                "app_key_hash": "hash",
            }
        ),
    )


def _seed_db(db: Path) -> None:
    init_database(str(db))
    ts = datetime.now().isoformat(timespec="seconds")
    save_real_account_snapshot(
        str(db),
        ts,
        "kis",
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
        total_market_value=70_000.0,
        total_equity=1_070_000.0,
        raw_payload={"timestamp": ts},
    )
    save_real_positions(
        str(db),
        ts,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": 1,
                "avg_price": 70_000.0,
                "current_price": 70_000.0,
                "market_value": 70_000.0,
                "raw": {},
            }
        ],
    )


def test_no_data_health_status(tmp_path: Path) -> None:
    result = run_report_health_check(output_dir=tmp_path / "missing_outputs", db_path=tmp_path / "missing.db")

    assert result.status == HEALTH_NO_DATA
    assert result.checks["no_operational_data"] is True
    assert any(i.category == "outputs" for i in result.issues)


def test_all_fresh_health_ok(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_fresh_outputs(out)
    _seed_db(db)

    result = run_report_health_check(output_dir=out, db_path=db)

    assert result.status == HEALTH_OK
    assert not [i for i in result.issues if i.severity in {"WARNING", "CRITICAL"}]
    assert result.checks["db"]["position_count"] == 1


def test_stale_risk_report_warning(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_fresh_outputs(out)
    _seed_db(db)
    stale = out / "risk_alert_20260517_100000.json"
    old = (datetime.now(UTC) - timedelta(hours=48)).timestamp()
    os.utime(stale, (old, old))

    result = run_report_health_check(output_dir=out, db_path=db, max_age_hours=24)

    assert result.status == HEALTH_WARNING
    assert any("risk_alert" in i.message and "older" in i.message for i in result.issues)


def test_missing_db_warning_when_outputs_exist(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    _seed_fresh_outputs(out)

    result = run_report_health_check(output_dir=out, db_path=tmp_path / "missing.db")

    assert result.status == HEALTH_WARNING
    assert any(i.category == "db" and "not found" in i.message for i in result.issues)


def test_appledouble_detection(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_fresh_outputs(out)
    _seed_db(db)
    _write(out / "._OPS_DASHBOARD.html", "metadata")

    result = run_report_health_check(output_dir=out, db_path=db)

    assert result.status == HEALTH_WARNING
    assert result.checks["appledouble"]["count"] == 1
    assert any("AppleDouble" in i.message for i in result.issues)


def test_token_cache_expired_detection(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_fresh_outputs(out)
    _seed_db(db)
    _write(
        out / ".kis_token_cache.json",
        json.dumps({"access_token": "tok", "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}),
    )

    result = run_report_health_check(output_dir=out, db_path=db)

    assert result.status == HEALTH_WARNING
    assert any(i.category == "token" and "expired" in i.message for i in result.issues)


def test_dashboard_stale_detection(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_fresh_outputs(out)
    _seed_db(db)
    html = out / "OPS_DASHBOARD.html"
    ops = out / "ops_dashboard_20260517_100000.json"
    old = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    new = datetime.now(UTC).timestamp()
    os.utime(html, (old, old))
    os.utime(ops, (new, new))

    result = run_report_health_check(output_dir=out, db_path=db)

    assert result.status == HEALTH_WARNING
    assert any(i.category == "dashboard" for i in result.issues)


def test_output_file_count_exceeded(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_fresh_outputs(out)
    _seed_db(db)

    result = run_report_health_check(output_dir=out, db_path=db, max_output_files=1)

    assert result.status == HEALTH_WARNING
    assert any("above limit" in i.message for i in result.issues)


def test_write_report_health_outputs(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    result = run_report_health_check(output_dir=out, db_path=tmp_path / "missing.db")
    jp, mp = write_report_health(result, output_dir=out)

    assert jp.is_file()
    assert mp.is_file()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["network_called"] is False
    assert data["no_cleanup_performed"] is True
    assert "DeepSignal Report Health Check" in mp.read_text(encoding="utf-8")
