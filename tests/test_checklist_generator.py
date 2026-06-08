"""checklist_generator.py — 수동 운영 체크리스트 생성."""

from __future__ import annotations

from pathlib import Path

from deepsignal.live_trading.checklist_generator import generate_checklists


EXPECTED_FILES = {
    "DAILY_CHECKLIST.md",
    "PRE_MARKET_CHECKLIST.md",
    "POST_TRADE_CHECKLIST.md",
    "WEEKLY_MAINTENANCE_CHECKLIST.md",
    "SAFETY_RULES.md",
}


def test_generate_checklists_writes_all_markdown_files(tmp_path: Path) -> None:
    out = tmp_path / "outputs" / "checklists"

    documents = generate_checklists(out)

    assert {Path(document.path).name for document in documents} == EXPECTED_FILES
    for name in EXPECTED_FILES:
        path = out / name
        assert path.is_file()
        assert "Manual checklist only" in path.read_text(encoding="utf-8")


def test_daily_weekly_checklists_include_expected_commands(tmp_path: Path) -> None:
    out = tmp_path / "checklists"

    generate_checklists(out)

    daily = (out / "DAILY_CHECKLIST.md").read_text(encoding="utf-8")
    weekly = (out / "WEEKLY_MAINTENANCE_CHECKLIST.md").read_text(encoding="utf-8")
    assert "source .venv/bin/activate" in daily
    assert "python main.py trading-session-check" in daily
    assert "python main.py open-dashboard --output-dir outputs" in daily
    assert "python main.py weekly-maintenance --output-dir outputs --archive-dir outputs/archive" in weekly
    assert "python main.py cleanup-reports --output-dir outputs --dry-run" in weekly
    assert "cleanup-reports --apply는 수동 검토 후에만" in weekly


def test_safety_rules_include_required_bans(tmp_path: Path) -> None:
    out = tmp_path / "checklists"

    generate_checklists(out)

    safety = (out / "SAFETY_RULES.md").read_text(encoding="utf-8")
    assert "cron/launchd 자동 실주문 금지" in safety
    assert "live-approve --execute 자동화 금지" in safety
    assert "--final-confirm 자동 주입 금지" in safety
    assert ".env 커밋 금지" in safety
    assert "KIS_ENV=live 확인" in safety
    assert "SELL 자동화 금지" in safety
    assert "시장가 금지" in safety
    assert "KIS POST 직접 호출 금지" in safety
