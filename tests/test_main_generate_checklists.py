"""main.py generate-checklists CLI smoke tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import main as main_mod


EXPECTED_FILES = {
    "DAILY_CHECKLIST.md",
    "PRE_MARKET_CHECKLIST.md",
    "POST_TRADE_CHECKLIST.md",
    "WEEKLY_MAINTENANCE_CHECKLIST.md",
    "SAFETY_RULES.md",
}


def test_generate_checklists_cli_smoke(tmp_path: Path, capsys) -> None:
    out = tmp_path / "outputs" / "checklists"

    rc = main_mod.main(["generate-checklists", "--output-dir", str(out)])

    assert rc == 0
    assert {p.name for p in out.glob("*.md")} == EXPECTED_FILES
    console = capsys.readouterr().out
    assert "DeepSignal checklists generated" in console
    assert str(out / "DAILY_CHECKLIST.md") in console


def test_generate_checklists_cli_no_network_calls(tmp_path: Path) -> None:
    out = tmp_path / "outputs" / "checklists"
    post = Mock()
    get = Mock()

    with patch("requests.post", post), patch("requests.get", get):
        rc = main_mod.main(["generate-checklists", "--output-dir", str(out)])

    assert rc == 0
    post.assert_not_called()
    get.assert_not_called()


def test_generate_checklists_cli_creates_no_scheduler_files(tmp_path: Path) -> None:
    out = tmp_path / "outputs" / "checklists"

    rc = main_mod.main(["generate-checklists", "--output-dir", str(out)])

    assert rc == 0
    created_files = {p.name for p in out.rglob("*") if p.is_file()}
    assert created_files == EXPECTED_FILES
    assert not any(name.endswith(".plist") for name in created_files)
    assert not any("cron" in name.lower() for name in created_files)
    assert not any(name in {"crontab", "cron.txt", "launchd.plist"} for name in created_files)
