"""main.py open-dashboard CLI smoke tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import main as main_mod


def test_open_dashboard_cli_smoke_lists_reports(tmp_path: Path, capsys) -> None:
    (tmp_path / "OPS_DASHBOARD.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "REPORT_INDEX.html").write_text("<html></html>", encoding="utf-8")

    rc = main_mod.main(["open-dashboard", "--output-dir", str(tmp_path)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "DeepSignal local viewer" in out
    assert "[OK] OPS Dashboard" in out
    assert "[OK] Report Index" in out
    assert "Opened:\n- (none)" in out


def test_open_dashboard_without_open_does_not_call_browser(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "OPS_DASHBOARD.html").write_text("<html></html>", encoding="utf-8")
    opener = Mock()
    monkeypatch.setattr("webbrowser.open", opener)

    rc = main_mod.main(["open-dashboard", "--output-dir", str(tmp_path)])

    assert rc == 0
    opener.assert_not_called()


def test_open_dashboard_with_open_calls_browser(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "OPS_DASHBOARD.html").write_text("<html></html>", encoding="utf-8")
    opener = Mock()
    monkeypatch.setattr("webbrowser.open", opener)

    rc = main_mod.main(["open-dashboard", "--output-dir", str(tmp_path), "--open"])

    assert rc == 0
    opener.assert_called_once()
    out = capsys.readouterr().out
    assert f"- {(tmp_path / 'OPS_DASHBOARD.html').as_posix()}" in out


def test_open_dashboard_with_open_index_calls_browser(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "REPORT_INDEX.html").write_text("<html></html>", encoding="utf-8")
    opener = Mock()
    monkeypatch.setattr("webbrowser.open", opener)

    rc = main_mod.main(["open-dashboard", "--output-dir", str(tmp_path), "--open-index"])

    assert rc == 0
    opener.assert_called_once()


def test_open_dashboard_missing_file_does_not_fail(tmp_path: Path, monkeypatch, capsys) -> None:
    opener = Mock()
    monkeypatch.setattr("webbrowser.open", opener)

    rc = main_mod.main(["open-dashboard", "--output-dir", str(tmp_path), "--open"])

    assert rc == 0
    opener.assert_not_called()
    out = capsys.readouterr().out
    assert "[MISSING] OPS Dashboard" in out
    assert "Missing report:" in out
