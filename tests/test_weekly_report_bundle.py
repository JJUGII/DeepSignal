"""weekly_report_bundle.py — 주간 리포트 번들."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from deepsignal.live_trading.weekly_report_bundle import (
    WEEKLY_BUNDLE_OK,
    WEEKLY_BUNDLE_WARNING,
    create_weekly_report_bundle,
)


STATIC_FILES = [
    "WEEKLY_MAINTENANCE.md",
    "REPORT_HEALTH.md",
    "REPORT_INDEX.html",
    "REPORT_INDEX.md",
    "OPS_DASHBOARD.html",
    "DAILY_OPS_SUMMARY.md",
    "RISK_ALERT.md",
    "SELL_PLAN.md",
    "OPS_DRY_RUN.md",
]

JSON_FILES = [
    "weekly_maintenance_20260517_100000.json",
    "report_health_20260517_100000.json",
    "report_index_20260517_100000.json",
    "notification_audit_20260517_100000.json",
    "report_cleanup_audit_20260517_100000.json",
]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_reports(out: Path) -> None:
    for name in STATIC_FILES:
        _write(out / name, "<html></html>" if name.endswith(".html") else f"# {name}")
    for name in JSON_FILES:
        _write(out / name, json.dumps({"status": "OK", "final_status": "WEEKLY_MAINTENANCE_OK"}))


def test_weekly_report_bundle_creates_bundle_and_copies_core_files(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    _seed_reports(out)

    result = create_weekly_report_bundle(output_dir=out, bundle_dir=out / "weekly_bundles", run_weekly=False)

    assert result.status == WEEKLY_BUNDLE_OK
    bundle_dir = Path(result.bundle_dir)
    assert bundle_dir.is_dir()
    assert (bundle_dir / "WEEKLY_MAINTENANCE.md").is_file()
    assert (bundle_dir / "REPORT_HEALTH.md").is_file()
    assert (bundle_dir / "REPORT_INDEX.html").is_file()
    assert (bundle_dir / "BUNDLE_INDEX.md").is_file()
    assert (bundle_dir / "BUNDLE_INDEX.html").is_file()
    assert any(item.category == "notification" and item.copied for item in result.items)


def test_weekly_report_bundle_missing_files_warning(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    _write(out / "WEEKLY_MAINTENANCE.md", "# weekly")

    result = create_weekly_report_bundle(output_dir=out, bundle_dir=out / "weekly_bundles", run_weekly=False)

    assert result.status == WEEKLY_BUNDLE_WARNING
    assert any("Missing bundle target" in warning for warning in result.warnings)
    assert Path(result.index_md or "").is_file()


def test_weekly_report_bundle_zip_option(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    _seed_reports(out)

    result = create_weekly_report_bundle(output_dir=out, bundle_dir=out / "weekly_bundles", create_zip=True, run_weekly=False)

    assert result.zip_path is not None
    zip_path = Path(result.zip_path)
    assert zip_path.is_file()
    assert zip_path.parent == Path(result.bundle_dir).parent
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "BUNDLE_INDEX.md" in names
    assert "WEEKLY_MAINTENANCE.md" in names


def test_weekly_report_bundle_excludes_sensitive_files(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    _seed_reports(out)
    _write(out / ".env", "SECRET=1")
    _write(out / ".kis_token_cache.json", json.dumps({"access_token": "secret"}))
    _write(out / "deepsignal.db", "sqlite")
    _write(out / "script.py", "print('no')")

    result = create_weekly_report_bundle(output_dir=out, bundle_dir=out / "weekly_bundles", run_weekly=False)
    bundle_files = {p.name for p in Path(result.bundle_dir).iterdir() if p.is_file()}

    assert ".env" not in bundle_files
    assert ".kis_token_cache.json" not in bundle_files
    assert "deepsignal.db" not in bundle_files
    assert "script.py" not in bundle_files
    assert all(".kis_token_cache" not in item.source_path for item in result.items)


def test_weekly_report_bundle_rejects_bundle_dir_outside_output(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    _seed_reports(out)

    with pytest.raises(ValueError):
        create_weekly_report_bundle(output_dir=out, bundle_dir=tmp_path / "outside", run_weekly=False)
