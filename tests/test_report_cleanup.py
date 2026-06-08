"""report_cleanup: outputs 보존/정리 매니저."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from deepsignal.live_trading.report_cleanup import cleanup_reports


def _write(path: Path, *, days_old: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    ts = (datetime.now() - timedelta(days=days_old)).timestamp()
    os.utime(path, (ts, ts))
    return path


def _audit(path: str | None) -> dict:
    assert path
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_dry_run_does_not_delete_files(tmp_path: Path) -> None:
    old = _write(tmp_path / "risk_alert_20250101_010101.json", days_old=30)
    result = cleanup_reports(output_dir=tmp_path, keep_days=1, keep_latest=0, dry_run=True)
    assert old.exists()
    data = _audit(result.audit_path)
    assert len(data["candidates"]) == 1
    assert result.deleted == []
    assert data["dry_run"] is True
    assert data["network_called"] is False
    assert data["실제_주문_없음"] is True


def test_keep_latest_preserves_recent_per_category(tmp_path: Path) -> None:
    old1 = _write(tmp_path / "ops_dashboard_20250101_010101.json", days_old=30)
    old2 = _write(tmp_path / "ops_dashboard_20250102_010101.json", days_old=20)
    result = cleanup_reports(output_dir=tmp_path, keep_days=1, keep_latest=1, dry_run=True)
    data = _audit(result.audit_path)
    candidate_paths = {c["path"] for c in data["candidates"]}
    kept_paths = {c.path for c in result.kept}
    assert old1.name in candidate_paths
    assert old2.name in kept_paths


def test_keep_days_preserves_recent_files(tmp_path: Path) -> None:
    recent = _write(tmp_path / "sell_plan_20260516_010101.json", days_old=1)
    old = _write(tmp_path / "sell_plan_20250101_010101.json", days_old=40)
    result = cleanup_reports(output_dir=tmp_path, keep_days=14, keep_latest=0, dry_run=True)
    data = _audit(result.audit_path)
    candidate_paths = {c["path"] for c in data["candidates"]}
    kept_paths = {c.path for c in result.kept}
    assert old.name in candidate_paths
    assert recent.name in kept_paths


def test_archive_moves_candidates(tmp_path: Path) -> None:
    old = _write(tmp_path / "daily_ops_summary_20250101_010101.json", days_old=40)
    result = cleanup_reports(
        output_dir=tmp_path,
        keep_days=1,
        keep_latest=0,
        archive=True,
        archive_dir=tmp_path / "archive",
        dry_run=False,
    )
    assert not old.exists()
    archived = tmp_path / "archive" / old.name
    assert archived.exists()
    assert result.archived
    assert result.deleted == []


def test_appledouble_candidate_and_remove(tmp_path: Path) -> None:
    meta = _write(tmp_path / "._test_runbook.py", days_old=0)
    dry = cleanup_reports(output_dir=tmp_path, keep_days=14, keep_latest=20, dry_run=True)
    data = _audit(dry.audit_path)
    assert any(c["category"] == "appledouble" for c in data["candidates"])
    assert meta.exists()

    applied = cleanup_reports(
        output_dir=tmp_path,
        keep_days=14,
        keep_latest=20,
        remove_appledouble=True,
        dry_run=False,
    )
    assert not meta.exists()
    assert any(c.category == "appledouble" for c in applied.deleted)


def test_protected_files_are_preserved(tmp_path: Path) -> None:
    protected = [
        tmp_path / "OPS_DASHBOARD.html",
        tmp_path / "OPS_DASHBOARD.md",
        tmp_path / ".gitkeep",
        tmp_path / ".kis_token_cache.json",
    ]
    for p in protected:
        _write(p, days_old=100)
    result = cleanup_reports(output_dir=tmp_path, keep_days=0, keep_latest=0, dry_run=False)
    for p in protected:
        assert p.exists()
    data = _audit(result.audit_path)
    candidate_paths = {c["path"] for c in data["candidates"]}
    assert not any(p.name in candidate_paths for p in protected)


def test_audit_created_for_apply(tmp_path: Path) -> None:
    _write(tmp_path / "post_trade_runbook_20250101_010101.json", days_old=30)
    result = cleanup_reports(output_dir=tmp_path, keep_days=1, keep_latest=0, dry_run=False)
    assert result.audit_path
    data = _audit(result.audit_path)
    assert data["deleted"]
    assert data["actual_order_attempted"] is False


def test_archive_dir_must_be_inside_output_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        cleanup_reports(
            output_dir=tmp_path / "outputs",
            archive=True,
            archive_dir=tmp_path / "outside",
            dry_run=False,
        )
