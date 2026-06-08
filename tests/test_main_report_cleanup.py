"""main.py cleanup-reports CLI smoke."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import main as main_mod


def _write(path: Path, *, days_old: int = 30) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    ts = (datetime.now() - timedelta(days=days_old)).timestamp()
    os.utime(path, (ts, ts))
    return path


def _latest_audit(root: Path) -> dict:
    audits = sorted(root.glob("report_cleanup_audit_*.json"))
    assert audits
    return json.loads(audits[-1].read_text(encoding="utf-8"))


def test_cleanup_reports_cli_dry_run(tmp_path: Path) -> None:
    old = _write(tmp_path / "risk_alert_20250101_010101.json")
    rc = main_mod.main(["cleanup-reports", "--output-dir", str(tmp_path), "--keep-days", "1", "--keep-latest", "0"])
    assert rc == 0
    assert old.exists()
    audit = _latest_audit(tmp_path)
    assert audit["dry_run"] is True
    assert len(audit["candidates"]) == 1


def test_cleanup_reports_cli_apply(tmp_path: Path) -> None:
    old = _write(tmp_path / "ops_dashboard_20250101_010101.json")
    rc = main_mod.main(
        [
            "cleanup-reports",
            "--output-dir",
            str(tmp_path),
            "--apply",
            "--keep-days",
            "1",
            "--keep-latest",
            "0",
        ]
    )
    assert rc == 0
    assert not old.exists()
    audit = _latest_audit(tmp_path)
    assert audit["deleted"]


def test_cleanup_reports_cli_archive(tmp_path: Path) -> None:
    old = _write(tmp_path / "sell_plan_20250101_010101.json")
    rc = main_mod.main(
        [
            "cleanup-reports",
            "--output-dir",
            str(tmp_path),
            "--apply",
            "--archive",
            "--archive-dir",
            str(tmp_path / "archive"),
            "--keep-days",
            "1",
            "--keep-latest",
            "0",
        ]
    )
    assert rc == 0
    assert not old.exists()
    assert (tmp_path / "archive" / old.name).exists()
    audit = _latest_audit(tmp_path)
    assert audit["archived"]


def test_cleanup_reports_cli_does_not_touch_outside_files(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    outside = _write(tmp_path / "risk_alert_20240101_010101.json")
    inside = _write(out / "risk_alert_20250101_010101.json")
    rc = main_mod.main(
        [
            "cleanup-reports",
            "--output-dir",
            str(out),
            "--apply",
            "--keep-days",
            "1",
            "--keep-latest",
            "0",
        ]
    )
    assert rc == 0
    assert outside.exists()
    assert not inside.exists()
