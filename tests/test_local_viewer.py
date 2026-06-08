"""local_viewer: safe local report opener."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from deepsignal.live_trading.local_viewer import (
    build_local_viewer_result,
    collect_view_items,
    format_local_viewer_console,
    open_local_report,
)


def test_format_local_viewer_shows_freshness(tmp_path: Path) -> None:
    import json

    ts = "2026-05-19T08:00:00+09:00"
    (tmp_path / "live_order_plan_ai_latest.json").write_text(
        json.dumps({"generated_at": ts}, ensure_ascii=False),
        encoding="utf-8",
    )
    result = build_local_viewer_result(tmp_path)
    text = format_local_viewer_console(result)
    assert "AI Daily Freshness" in text


def test_collect_view_items_lists_default_reports(tmp_path: Path) -> None:
    (tmp_path / "OPS_DASHBOARD.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "REPORT_INDEX.html").write_text("<html></html>", encoding="utf-8")

    items = collect_view_items(tmp_path)

    names = [item.name for item in items]
    assert names == [
        "OPS Dashboard",
        "Report Index",
        "Archive Viewer",
        "Archive Viewer CSV",
        "Archive Viewer Summary",
        "Archive Viewer Presets",
        "Safety Audit",
        "AI Daily Trade Plan",
        "Latest AI Order Plan",
        "AI Daily Trade Report",
        "AI Daily Status",
        "Daily Summary",
        "Ops Dry Run",
        "Risk Alert",
        "Sell Plan",
        "Safety Audit JSON",
        "Archive Viewer JSON",
        "AI Daily Trade Plan JSON",
        "AI Daily Trade Report JSON",
        "AI Daily Status JSON",
    ]
    assert items[0].exists is True
    assert items[1].kind == "html"
    assert items[2].kind == "html"


def test_missing_files_are_graceful(tmp_path: Path) -> None:
    result = build_local_viewer_result(tmp_path)

    assert result.opened == []
    assert result.warnings == []
    assert all(not item.exists for item in result.items)
    console = format_local_viewer_console(result)
    assert "[MISSING] OPS Dashboard" in console


def test_output_dir_outside_path_blocked(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    out.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<html></html>", encoding="utf-8")

    with pytest.raises(ValueError, match="outside output_dir"):
        open_local_report(outside, output_dir=out)


def test_external_url_blocked() -> None:
    with pytest.raises(ValueError, match="external URLs"):
        open_local_report("https://example.com/OPS_DASHBOARD.html")


def test_open_mock_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "OPS_DASHBOARD.html"
    path.write_text("<html></html>", encoding="utf-8")
    opener = Mock()
    monkeypatch.setattr("webbrowser.open", opener)

    opened = open_local_report(path, output_dir=tmp_path)

    assert opened == path.as_posix()
    opener.assert_called_once()
    assert opener.call_args.args[0].startswith("file://")


def test_build_open_dashboard_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "OPS_DASHBOARD.html").write_text("<html></html>", encoding="utf-8")
    opener = Mock()
    monkeypatch.setattr("webbrowser.open", opener)

    result = build_local_viewer_result(tmp_path, open_names=["ops_dashboard"])

    assert result.opened == [(tmp_path / "OPS_DASHBOARD.html").as_posix()]
    assert result.warnings == []
    opener.assert_called_once()


def test_open_all_opens_html_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "OPS_DASHBOARD.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "REPORT_INDEX.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "DAILY_OPS_SUMMARY.md").write_text("# Daily", encoding="utf-8")
    opener = Mock()
    monkeypatch.setattr("webbrowser.open", opener)

    result = build_local_viewer_result(tmp_path, open_all=True)

    assert result.opened == [
        (tmp_path / "OPS_DASHBOARD.html").as_posix(),
        (tmp_path / "REPORT_INDEX.html").as_posix(),
    ]
    assert opener.call_count == 2


def test_open_missing_requested_report_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opener = Mock()
    monkeypatch.setattr("webbrowser.open", opener)

    result = build_local_viewer_result(tmp_path, open_names=["ops_dashboard"])

    assert result.opened == []
    assert result.warnings == [f"Missing report: {(tmp_path / 'OPS_DASHBOARD.html').as_posix()}"]
    opener.assert_not_called()
