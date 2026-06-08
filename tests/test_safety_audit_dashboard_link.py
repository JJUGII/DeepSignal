"""Safety audit links in report index, HTML dashboard, and local viewer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import main as main_mod
from deepsignal.live_trading.html_dashboard import write_html_dashboard
from deepsignal.live_trading.local_viewer import build_local_viewer_result, format_local_viewer_console
from deepsignal.live_trading.report_index import run_report_index


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, ensure_ascii=False, indent=2))


def _seed_safety(out: Path, status: str = "SAFETY_AUDIT_WARNING") -> Path:
    _write(out / "SAFETY_AUDIT.md", "# DeepSignal Safety Audit\n")
    path = out / "safety_audit_20260517_161340.json"
    _write_json(
        path,
        {
            "status": status,
            "generated_at": "2026-05-17T16:13:40",
            "issues": [
                {"severity": "WARNING", "category": "reports", "message": "missing report"},
                {"severity": "BLOCKED", "category": "reconcile", "message": "blocked"} if status == "SAFETY_AUDIT_BLOCKED" else {"severity": "INFO", "category": "ok"},
            ],
        },
    )
    return path


def test_report_index_links_safety_audit_status_and_files(tmp_path: Path) -> None:
    _seed_safety(tmp_path, "SAFETY_AUDIT_WARNING")

    _result, html_path, md_path, _json_path = run_report_index(output_dir=tmp_path)

    html = html_path.read_text(encoding="utf-8")
    md = md_path.read_text(encoding="utf-8")
    assert "안전 점검" in html
    assert "SAFETY_AUDIT_WARNING" in html
    assert "SAFETY_AUDIT.md" in html
    assert "safety_audit_20260517_161340.json" in html
    assert "Safety audit status: 안전 점검 경고 (SAFETY_AUDIT_WARNING)" in md


def test_report_index_safety_audit_not_available(tmp_path: Path) -> None:
    _result, html_path, md_path, _json_path = run_report_index(output_dir=tmp_path)

    html = html_path.read_text(encoding="utf-8")
    md = md_path.read_text(encoding="utf-8")
    assert "NOT_AVAILABLE" in html
    assert "Safety audit has not been generated yet" in html
    assert "Safety audit status: 없음 (NOT_AVAILABLE)" in md


def test_html_dashboard_includes_safety_audit_section(tmp_path: Path) -> None:
    _seed_safety(tmp_path, "SAFETY_AUDIT_WARNING")

    result = write_html_dashboard(output_dir=tmp_path)

    html = Path(result.html_path).read_text(encoding="utf-8")
    assert "안전 점검" in html
    assert "안전 점검 상태" in html
    assert "안전 점검 경고" in html
    assert "SAFETY_AUDIT_WARNING" in html
    assert "SAFETY_AUDIT.md" in html
    assert "safety_audit_20260517_161340.json" in html


def test_html_dashboard_missing_safety_audit_is_optional(tmp_path: Path) -> None:
    result = write_html_dashboard(output_dir=tmp_path)

    html = Path(result.html_path).read_text(encoding="utf-8")
    assert "안전 점검 리포트가 아직 생성되지 않았습니다." in html
    assert result.html_path


def test_open_dashboard_lists_safety_audit_paths(tmp_path: Path) -> None:
    latest = _seed_safety(tmp_path, "SAFETY_AUDIT_OK")

    result = build_local_viewer_result(tmp_path)
    console = format_local_viewer_console(result)

    assert "[OK] Safety Audit:" in console
    assert (tmp_path / "SAFETY_AUDIT.md").as_posix() in console
    assert "[OK] Safety Audit JSON:" in console
    assert latest.as_posix() in console


def test_blocked_status_displays_in_index_and_dashboard(tmp_path: Path) -> None:
    _seed_safety(tmp_path, "SAFETY_AUDIT_BLOCKED")

    _result, html_path, _md_path, _json_path = run_report_index(output_dir=tmp_path)
    dashboard = write_html_dashboard(output_dir=tmp_path)

    assert "SAFETY_AUDIT_BLOCKED" in html_path.read_text(encoding="utf-8")
    assert "SAFETY_AUDIT_BLOCKED" in Path(dashboard.html_path).read_text(encoding="utf-8")


def test_safety_audit_dashboard_links_do_not_call_network_or_orders(tmp_path: Path) -> None:
    _seed_safety(tmp_path, "SAFETY_AUDIT_WARNING")
    post = Mock()
    get = Mock()

    with patch("requests.post", post), patch("requests.get", get):
        rc_index = main_mod.main(["report-index", "--output-dir", str(tmp_path), "--archive-dir", str(tmp_path / "archive")])
        rc_html = main_mod.main(["html-dashboard", "--output-dir", str(tmp_path)])
        rc_open = main_mod.main(["open-dashboard", "--output-dir", str(tmp_path)])

    assert rc_index == 0
    assert rc_html == 0
    assert rc_open == 0
    post.assert_not_called()
    get.assert_not_called()
