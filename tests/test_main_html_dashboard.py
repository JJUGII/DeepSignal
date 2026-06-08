"""main.py html-dashboard CLI smoke."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import main as main_mod


def test_html_dashboard_cli_smoke(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "daily_ops_summary_20260516_100000.json").write_text(
        json.dumps({"status": "OK", "generated_at": "t", "next_actions": ["No critical action. Continue monitoring."]}),
        encoding="utf-8",
    )
    post = Mock()
    monkeypatch.setattr("requests.post", post)
    rc = main_mod.main(["html-dashboard", "--output-dir", str(tmp_path)])
    assert rc == 0
    html_path = tmp_path / "OPS_DASHBOARD.html"
    assert html_path.is_file()
    text = html_path.read_text(encoding="utf-8")
    assert "DeepSignal Operations Dashboard" in text
    assert "No critical action" in text
    post.assert_not_called()


def test_html_dashboard_open_uses_webbrowser(monkeypatch, tmp_path: Path) -> None:
    open_mock = Mock()
    monkeypatch.setattr("webbrowser.open", open_mock)
    rc = main_mod.main(["html-dashboard", "--output-dir", str(tmp_path), "--open"])
    assert rc == 0
    open_mock.assert_called_once()
