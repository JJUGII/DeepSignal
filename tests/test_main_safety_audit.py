"""main.py safety-audit CLI smoke tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import main as main_mod
from deepsignal.live_trading.checklist_generator import generate_checklists
from deepsignal.storage.database import init_database


STATIC_REPORTS = [
    "REPORT_HEALTH.md",
    "WEEKLY_MAINTENANCE.md",
    "REPORT_INDEX.html",
    "OPS_DASHBOARD.html",
    "RISK_ALERT.md",
    "SELL_PLAN.md",
]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, ensure_ascii=False, indent=2))


def _seed_ok(out: Path, db: Path) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    generate_checklists(out / "checklists")
    for name in STATIC_REPORTS:
        _write(out / name, "<html></html>" if name.endswith(".html") else f"# {name}")
    _write_json(out / "LATEST_RECONCILE_STATE.json", {"timestamp": now, "success": True, "matched": [], "warnings": []})
    _write_json(out / "live_account_snapshot_20260517_100000.json", {"snapshot_time": now, "status": "OK"})
    _write_json(out / "reconcile_live_account_20260517_100000.json", {"success": True, "warnings": []})
    _write_json(out / "risk_alert_20260517_100000.json", {"status": "OK", "alerts": [], "warnings": []})
    _write_json(out / "live_fill_summary_20260517_100000.json", {"status": "OK", "open_partial_fills": []})
    _write_json(out / "live_approval_audit_20260517_100000.json", {"status": "DRY_RUN_COMPLETED", "actual_order_attempted": False})
    _write_json(out / "pre_trade_runbook_20260517_100000.json", {"mode": "pre_trade", "final_status": "PRE_TRADE_READY", "finished_at": now})
    _write(out / "AI_DAILY_TRADE_PLAN.md", "# daily plan")
    _write_json(out / "ai_daily_trade_plan_20260517_100000.json", {"status": "AI_DAILY_TRADE_PLAN_READY"})
    _write_json(out / "live_order_plan_ai_latest.json", {"status": "PENDING_APPROVAL", "orders": []})
    _write_json(out / "telegram_approval_request_20260517_100000.json", {"status": "PENDING"})
    _write_json(out / "telegram_approval_audit_20260517_100000.json", {"status": "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED"})
    _write_json(out / "execute_approved_audit_20260517_100000.json", {"status": "EXECUTE_APPROVED_COMPLETED"})
    _write(out / "AI_DAILY_TRADE_REPORT.md", "# daily report")
    _write_json(out / "ai_daily_trade_report_20260517_100000.json", {"status": "AI_DAILY_TRADE_REPORT_READY", "generated_at": now})
    _write(out / "AI_DAILY_STATUS.md", "# daily status")
    _write_json(out / "ai_daily_status_20260517_100000.json", {"status": "AI_DAILY_STATUS_READY", "generated_at": now})
    for name in (
        "ai_daily_trade_plan_20260517_100000.json",
        "live_order_plan_ai_latest.json",
        "telegram_approval_request_20260517_100000.json",
        "telegram_approval_audit_20260517_100000.json",
        "execute_approved_audit_20260517_100000.json",
    ):
        path = out / name
        data = json.loads(path.read_text(encoding="utf-8"))
        data["generated_at"] = now
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    init_database(str(db))


def test_safety_audit_cli_ok_exit_code_and_files(tmp_path: Path, capsys) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)

    rc = main_mod.main(["safety-audit", "--output-dir", str(out), "--db-path", str(db)])

    assert rc == 0
    assert (out / "SAFETY_AUDIT.md").is_file()
    reports = sorted(out.glob("safety_audit_*.json"))
    assert reports
    assert json.loads(reports[-1].read_text(encoding="utf-8"))["status"] == "SAFETY_AUDIT_OK"
    console = capsys.readouterr().out
    assert "DeepSignal safety audit" in console


def test_safety_audit_cli_warning_exit_code_zero(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    (out / "checklists" / "DAILY_CHECKLIST.md").unlink()

    rc = main_mod.main(["safety-audit", "--output-dir", str(out), "--db-path", str(db)])

    assert rc == 0
    data = json.loads(sorted(out.glob("safety_audit_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["status"] == "SAFETY_AUDIT_WARNING"


def test_safety_audit_cli_blocked_exit_code_one(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    safety = out / "checklists" / "SAFETY_RULES.md"
    safety.write_text(safety.read_text(encoding="utf-8").replace("SELL 자동화 금지", ""), encoding="utf-8")

    rc = main_mod.main(["safety-audit", "--output-dir", str(out), "--db-path", str(db)])

    assert rc == 1
    data = json.loads(sorted(out.glob("safety_audit_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["status"] == "SAFETY_AUDIT_BLOCKED"


def test_safety_audit_cli_strict_promotes_warning_to_blocked(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    (out / "checklists" / "DAILY_CHECKLIST.md").unlink()

    rc = main_mod.main(["safety-audit", "--output-dir", str(out), "--db-path", str(db), "--strict"])

    assert rc == 1
    data = json.loads(sorted(out.glob("safety_audit_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["status"] == "SAFETY_AUDIT_BLOCKED"


def test_safety_audit_cli_no_network_order_cleanup_or_delete(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    marker = out / "keep_me.txt"
    _write(marker, "do not delete")
    post = Mock()
    get = Mock()

    with patch("requests.post", post), patch("requests.get", get):
        rc = main_mod.main(["safety-audit", "--output-dir", str(out), "--db-path", str(db)])

    assert rc == 0
    assert marker.is_file()
    assert not (out / "archive" / marker.name).exists()
    post.assert_not_called()
    get.assert_not_called()
