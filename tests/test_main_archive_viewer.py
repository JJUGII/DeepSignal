"""main.py archive-viewer CLI tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import main as main_mod


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, ensure_ascii=False, indent=2))


def test_archive_viewer_cli_smoke(tmp_path: Path, capsys) -> None:
    _write_json(tmp_path / "safety_audit_20260517_100000.json", {"status": "SAFETY_AUDIT_OK"})

    rc = main_mod.main(["archive-viewer", "--output-dir", str(tmp_path), "--archive-dir", str(tmp_path / "archive")])

    assert rc == 0
    assert (tmp_path / "ARCHIVE_VIEWER.html").is_file()
    assert (tmp_path / "ARCHIVE_VIEWER.csv").is_file()
    assert (tmp_path / "ARCHIVE_VIEWER_SUMMARY.md").is_file()
    assert (tmp_path / "ARCHIVE_VIEWER_PRESETS.json").is_file()
    assert sorted(tmp_path.glob("archive_viewer_*.json"))
    out = capsys.readouterr().out
    assert "DeepSignal archive viewer created" in out
    assert "Archive Viewer freshness summary:" in out


def test_archive_viewer_cli_json_export_contains_ux_sections(tmp_path: Path) -> None:
    _write_json(tmp_path / "safety_audit_20260517_100000.json", {"status": "SAFETY_AUDIT_BLOCKED", "blocked_count": 1})

    rc = main_mod.main(["archive-viewer", "--output-dir", str(tmp_path), "--archive-dir", str(tmp_path / "archive")])

    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("archive_viewer_*.json"))[-1].read_text(encoding="utf-8"))
    assert "summary" in data
    assert "filters_available" in data
    assert "needs_attention" in data
    assert "latest_by_type" in data
    assert data["export_files"]["csv"] == "ARCHIVE_VIEWER.csv"
    assert data["export_files"]["summary_md"] == "ARCHIVE_VIEWER_SUMMARY.md"
    assert data["export_files"]["presets"] == "ARCHIVE_VIEWER_PRESETS.json"
    assert data["preset_file"] == "ARCHIVE_VIEWER_PRESETS.json"
    assert any(preset["id"] == "needs_attention" for preset in data["presets"])
    assert data["needs_attention"][0]["relative_path"] == "safety_audit_20260517_100000.json"
    assert "freshness_source" in data["entries"][0]
    assert "freshness_source_summary" in data["summary"]


def test_archive_viewer_cli_no_csv(tmp_path: Path) -> None:
    _write_json(tmp_path / "safety_audit_20260517_100000.json", {"status": "SAFETY_AUDIT_OK"})

    rc = main_mod.main(["archive-viewer", "--output-dir", str(tmp_path), "--archive-dir", str(tmp_path / "archive"), "--no-csv"])

    assert rc == 0
    assert (tmp_path / "ARCHIVE_VIEWER.html").is_file()
    assert (tmp_path / "ARCHIVE_VIEWER_PRESETS.json").is_file()
    assert not (tmp_path / "ARCHIVE_VIEWER.csv").exists()
    data = json.loads(sorted(tmp_path.glob("archive_viewer_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["export_files"]["csv"] is None


def test_archive_viewer_cli_no_summary_md(tmp_path: Path) -> None:
    _write_json(tmp_path / "safety_audit_20260517_100000.json", {"status": "SAFETY_AUDIT_OK"})

    rc = main_mod.main(["archive-viewer", "--output-dir", str(tmp_path), "--archive-dir", str(tmp_path / "archive"), "--no-summary-md"])

    assert rc == 0
    assert (tmp_path / "ARCHIVE_VIEWER.html").is_file()
    assert (tmp_path / "ARCHIVE_VIEWER_PRESETS.json").is_file()
    assert not (tmp_path / "ARCHIVE_VIEWER_SUMMARY.md").exists()
    data = json.loads(sorted(tmp_path.glob("archive_viewer_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["export_files"]["summary_md"] is None


def test_archive_viewer_cli_limit(tmp_path: Path) -> None:
    for i in range(3):
        _write_json(tmp_path / f"risk_alert_20260517_10000{i}.json", {"status": "OK"})

    rc = main_mod.main(["archive-viewer", "--output-dir", str(tmp_path), "--archive-dir", str(tmp_path / "archive"), "--limit", "2"])

    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("archive_viewer_*.json"))[-1].read_text(encoding="utf-8"))
    assert len(data["entries"]) == 2


def test_archive_viewer_cli_trend_days(tmp_path: Path) -> None:
    _write_json(tmp_path / "risk_alert_20260517_100000.json", {"status": "WARNING"})

    rc = main_mod.main(["archive-viewer", "--output-dir", str(tmp_path), "--archive-dir", str(tmp_path / "archive"), "--trend-days", "14"])

    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("archive_viewer_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["trend_analytics"]["trend_days"] == 14
    assert len(data["trend_analytics"]["warning_trend_7d"]) == 14
    assert len(data["trend_analytics"]["blocked_trend_7d"]) == 14


def test_open_dashboard_lists_archive_viewer_paths(tmp_path: Path, capsys) -> None:
    _write(tmp_path / "ARCHIVE_VIEWER.html", "<html></html>")
    _write(tmp_path / "ARCHIVE_VIEWER.csv", "report_type\n")
    _write(tmp_path / "ARCHIVE_VIEWER_SUMMARY.md", "# summary\n")
    _write_json(tmp_path / "ARCHIVE_VIEWER_PRESETS.json", [{"id": "needs_attention"}])
    _write_json(tmp_path / "archive_viewer_20260517_100000.json", {"summary": {"total_reports": 1}})

    rc = main_mod.main(["open-dashboard", "--output-dir", str(tmp_path)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[OK] Archive Viewer:" in out
    assert "ARCHIVE_VIEWER.html" in out
    assert "[OK] Archive Viewer CSV:" in out
    assert "ARCHIVE_VIEWER.csv" in out
    assert "[OK] Archive Viewer Summary:" in out
    assert "ARCHIVE_VIEWER_SUMMARY.md" in out
    assert "[OK] Archive Viewer Presets:" in out
    assert "ARCHIVE_VIEWER_PRESETS.json" in out
    assert "[OK] Archive Viewer JSON:" in out
    assert "archive_viewer_20260517_100000.json" in out


def test_open_dashboard_open_archive_opens_only_archive(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path / "OPS_DASHBOARD.html", "<html></html>")
    _write(tmp_path / "ARCHIVE_VIEWER.html", "<html></html>")
    opener = Mock()
    monkeypatch.setattr("webbrowser.open", opener)

    rc = main_mod.main(["open-dashboard", "--output-dir", str(tmp_path), "--open-archive"])

    assert rc == 0
    opener.assert_called_once()
    opened_arg = opener.call_args.args[0]
    assert "ARCHIVE_VIEWER.html" in opened_arg


def test_archive_viewer_cli_no_network_or_cleanup_side_effects(tmp_path: Path) -> None:
    _write_json(tmp_path / "report_health_20260517_100000.json", {"status": "HEALTH_OK"})
    marker = tmp_path / "keep.txt"
    _write(marker, "keep")
    post = Mock()
    get = Mock()

    with patch("requests.post", post), patch("requests.get", get):
        rc = main_mod.main(["archive-viewer", "--output-dir", str(tmp_path), "--archive-dir", str(tmp_path / "archive")])

    assert rc == 0
    assert marker.is_file()
    assert not (tmp_path / "archive" / marker.name).exists()
    post.assert_not_called()
    get.assert_not_called()
