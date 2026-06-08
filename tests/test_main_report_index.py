"""main.py report-index CLI smoke."""

from __future__ import annotations

import json
from pathlib import Path

import main as main_mod


def _write_json(path: Path, body: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_report_index_cli_smoke(tmp_path: Path) -> None:
    _write_json(tmp_path / "daily_ops_summary_20260516_120000.json", {"status": "WARNING"})
    rc = main_mod.main(["report-index", "--output-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "REPORT_INDEX.html").is_file()
    assert (tmp_path / "REPORT_INDEX.md").is_file()
    reports = sorted(tmp_path.glob("report_index_*.json"))
    assert reports
    data = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert data["items"][0]["category"] == "daily_summary"


def test_report_index_cli_archive_option(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    _write_json(archive / "risk_alert_20260515_120000.json", {"status": "STOP_LOSS_ALERT"})
    rc = main_mod.main(["report-index", "--output-dir", str(tmp_path), "--archive-dir", str(archive)])
    assert rc == 0
    html = (tmp_path / "REPORT_INDEX.html").read_text(encoding="utf-8")
    assert "archive/risk_alert_20260515_120000.json" in html
    data = json.loads(sorted(tmp_path.glob("report_index_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["archive_dir"] == archive.resolve().as_posix()


def test_report_index_cli_max_items(tmp_path: Path) -> None:
    for i in range(4):
        _write_json(tmp_path / f"ops_dashboard_20260516_12000{i}.json", {"status": "OK"})
    rc = main_mod.main(["report-index", "--output-dir", str(tmp_path), "--max-items", "2"])
    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("report_index_*.json"))[-1].read_text(encoding="utf-8"))
    assert len(data["items"]) == 2
