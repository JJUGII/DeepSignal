"""weekly_maintenance.py — 주간 운영 점검 dry-run."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from deepsignal.live_trading.weekly_maintenance import (
    WEEKLY_MAINTENANCE_CRITICAL,
    WEEKLY_MAINTENANCE_OK,
    WEEKLY_MAINTENANCE_WARNING,
    run_weekly_maintenance,
    write_weekly_maintenance_report,
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

TODAY = datetime.now().strftime("%Y%m%d")

JSON_REPORTS = [
    f"live_account_snapshot_{TODAY}_100000.json",
    f"reconcile_live_account_{TODAY}_100000.json",
    f"risk_alert_{TODAY}_100000.json",
    f"ops_dashboard_{TODAY}_100000.json",
    f"sell_plan_{TODAY}_100000.json",
    f"daily_ops_summary_{TODAY}_100000.json",
    f"notification_audit_{TODAY}_100000.json",
    f"live_fill_summary_{TODAY}_100000.json",
]


def _write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _seed_outputs(out: Path) -> None:
    for name in STATIC_REPORTS:
        _write(out / name, "<html></html>" if name.endswith(".html") else "# report")
    base = {
        "status": "OK",
        "success": True,
        "positions": [],
        "items": [],
        "warnings": [],
        "messages": [],
        "results": [],
        "summaries": [],
    }
    for name in JSON_REPORTS:
        _write(out / name, json.dumps(base))
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
    from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
    from deepsignal.crypto_trading.crypto_recommendation_outcomes import (
        crypto_outcomes_db_path,
        init_crypto_outcomes_db,
        record_crypto_recommendation,
    )

    crypto_db = crypto_outcomes_db_path(out)
    init_crypto_outcomes_db(crypto_db)
    record_crypto_recommendation(
        CryptoOrderPlan(
            market="KRW-BTC",
            side="buy",
            limit_price=50_000_000.0,
            display_name="BTC",
            reason="seed",
            created_at=datetime.now().isoformat(timespec="seconds"),
        ),
        outcomes_db=crypto_db,
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


def test_weekly_maintenance_happy_path_ok(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_outputs(out)
    _seed_db(db)

    result = run_weekly_maintenance(output_dir=out, archive_dir=out / "archive", db_path=db, keep_days=365, keep_latest=100)

    assert result.final_status in (WEEKLY_MAINTENANCE_OK, WEEKLY_MAINTENANCE_WARNING)
    assert result.dry_run is True
    assert result.success is True
    crypto_step = next(s for s in result.steps if s.name == "crypto_recommendation_performance")
    assert crypto_step.status == "OK"
    assert [s.name for s in result.steps] == [
        "report_health_check",
        "cleanup_reports_dry_run",
        "daily_ops_summary",
        "html_dashboard",
        "report_index",
        "recommendation_performance",
        "crypto_recommendation_performance",
    ]


def test_weekly_maintenance_health_warning(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_outputs(out)
    _seed_db(db)
    (out / "REPORT_INDEX.html").unlink()

    result = run_weekly_maintenance(output_dir=out, archive_dir=out / "archive", db_path=db, keep_days=365, keep_latest=100)

    assert result.final_status == WEEKLY_MAINTENANCE_WARNING
    assert any(s.name == "report_health_check" and s.status == "HEALTH_WARNING" for s in result.steps)


def test_weekly_maintenance_health_critical(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_outputs(out)
    _write(db, "not a sqlite database")

    result = run_weekly_maintenance(output_dir=out, archive_dir=out / "archive", db_path=db, keep_days=365, keep_latest=100)

    assert result.final_status == WEEKLY_MAINTENANCE_CRITICAL
    assert result.success is False
    assert any(s.status == "HEALTH_CRITICAL" for s in result.steps)


def test_weekly_maintenance_cleanup_candidates_warning(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_outputs(out)
    _seed_db(db)
    old = out / "risk_alert_20200101_000000.json"
    _write(old, json.dumps({"status": "OK"}))
    old_ts = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(old, (old_ts, old_ts))

    result = run_weekly_maintenance(output_dir=out, archive_dir=out / "archive", db_path=db, keep_days=1, keep_latest=0)

    assert result.final_status == WEEKLY_MAINTENANCE_WARNING
    cleanup = next(s for s in result.steps if s.name == "cleanup_reports_dry_run")
    assert cleanup.status == "WARNING"
    assert "candidates=" in cleanup.message
    assert old.is_file()


def test_weekly_maintenance_writes_outputs(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_outputs(out)
    _seed_db(db)

    result = run_weekly_maintenance(output_dir=out, archive_dir=out / "archive", db_path=db, keep_days=365, keep_latest=100)
    jp, mp = write_weekly_maintenance_report(result, output_dir=out)

    assert jp.is_file()
    assert mp.is_file()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["dry_run"] is True
    assert data["cleanup_apply_used"] is False
    assert data["archive_move_used"] is False
    assert "DeepSignal Weekly Maintenance" in mp.read_text(encoding="utf-8")


def test_weekly_maintenance_cleanup_dry_run_only(monkeypatch, tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_outputs(out)
    _seed_db(db)
    audit = out / "report_cleanup_audit_fake.json"
    _write(audit, json.dumps({"candidates": []}))
    calls: list[dict[str, object]] = []

    def fake_cleanup_reports(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(dry_run=True, warnings=[], audit_path=audit.as_posix())

    monkeypatch.setattr("deepsignal.live_trading.report_cleanup.cleanup_reports", fake_cleanup_reports)

    result = run_weekly_maintenance(output_dir=out, archive_dir=out / "archive", db_path=db, keep_days=365, keep_latest=100)

    assert result.final_status == WEEKLY_MAINTENANCE_OK
    assert calls
    assert calls[0]["dry_run"] is True
    assert calls[0]["archive"] is False
    assert calls[0]["archive_dir"] is None
