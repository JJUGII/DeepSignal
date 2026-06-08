"""safety_audit.py — 로컬 읽기 전용 안전 감사."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from deepsignal.live_trading.checklist_generator import generate_checklists
from deepsignal.live_trading.safety_audit import (
    SAFETY_AUDIT_BLOCKED,
    SAFETY_AUDIT_OK,
    SAFETY_AUDIT_WARNING,
    run_safety_audit,
    write_safety_audit,
)
from deepsignal.storage.database import init_database, save_real_fill, save_real_order_history


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
    _write(
        out / "AI_LIVE_TRADE_RECOMMENDATION.md",
        "# AI recommendation\n"
        "python main.py live-approve --execute --allow-live-env "
        "--final-confirm I_UNDERSTAND_REAL_ORDER\n"
        "- final-confirm 자동 주입 없음\n"
        "- crypto-auto-runner automation example\n",
    )
    _write(
        out / "SAFETY_AUDIT.md",
        "# Safety Audit\n"
        "- Remove any automation around --final-confirm and live-approve --execute.\n"
        "- launchd plist cron alias #!/bin/bash\n",
    )
    _write_json(
        out / "safety_audit_20260517_100000.json",
        {
            "status": "SAFETY_AUDIT_OK",
            "checks": {"automation_scan": {"suspicious_final_confirm": []}},
        },
    )
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


def test_safety_audit_ok_fixture(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)

    result = run_safety_audit(output_dir=out, db_path=db)

    assert result.status == SAFETY_AUDIT_OK
    assert result.issues == []


def test_safety_audit_missing_checklist_is_warning(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    (out / "checklists" / "DAILY_CHECKLIST.md").unlink()

    result = run_safety_audit(output_dir=out, db_path=db)

    assert result.status == SAFETY_AUDIT_WARNING
    assert any(issue.category == "checklists" for issue in result.issues)


def test_safety_audit_missing_safety_rule_phrase_is_blocked(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    safety = out / "checklists" / "SAFETY_RULES.md"
    safety.write_text(safety.read_text(encoding="utf-8").replace("KIS POST 직접 호출 금지", ""), encoding="utf-8")

    result = run_safety_audit(output_dir=out, db_path=db)

    assert result.status == SAFETY_AUDIT_BLOCKED
    assert any(issue.category == "safety_rules" for issue in result.issues)


def test_safety_audit_reconcile_mismatch_is_blocked(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    _write_json(out / "LATEST_RECONCILE_STATE.json", {"success": False, "quantity_mismatch": [{"symbol": "005930"}]})

    result = run_safety_audit(output_dir=out, db_path=db)

    assert result.status == SAFETY_AUDIT_BLOCKED
    assert any(issue.category == "reconcile" for issue in result.issues)


def test_safety_audit_stale_snapshot_is_blocked(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat(timespec="seconds")
    _write_json(out / "live_account_snapshot_20260517_100000.json", {"snapshot_time": old, "status": "OK"})

    result = run_safety_audit(output_dir=out, db_path=db, max_snapshot_age_hours=24.0)

    assert result.status == SAFETY_AUDIT_BLOCKED
    assert any(issue.category == "account_snapshot" for issue in result.issues)


def test_safety_audit_partial_fill_db_is_blocked(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    now = datetime.now().isoformat(timespec="seconds")
    save_real_order_history(str(db), broker="kis", symbol="005930", side="BUY", quantity=10, status="PARTIAL", order_id="OID1", created_at=now)
    save_real_fill(str(db), broker="kis", symbol="005930", side="BUY", order_id="OID1", fill_id="FILL1", fill_quantity=4, fill_price=70000.0, fill_timestamp=now, created_at=now)

    result = run_safety_audit(output_dir=out, db_path=db)

    assert result.status == SAFETY_AUDIT_BLOCKED
    assert any(issue.category == "partial_fill" for issue in result.issues)


def test_write_safety_audit_reports_include_safety_flags(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    result = run_safety_audit(output_dir=out, db_path=db)

    jp, mp = write_safety_audit(result, output_dir=out)

    assert jp.is_file()
    assert mp.is_file()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["network_called"] is False
    assert data["kis_post_called"] is False
    assert data["live_approve_called"] is False
    assert data["cleanup_apply_called"] is False
    assert data["files_deleted"] is False
    assert "daily_ai_workflow" in data
    assert data["daily_ai_workflow"]["plan_status"] == "AI_DAILY_TRADE_PLAN_READY"
    assert "daily_ai_freshness" in data
    md = mp.read_text(encoding="utf-8")
    assert "AI 일일 매매 운영 상태" in md
    assert "AI 일일 매매 운영 Freshness" in md


def test_safety_audit_daily_ai_stale_plan_blocked(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    (out / "AI_DAILY_TRADE_PLAN.md").write_text("# plan", encoding="utf-8")
    import json

    stale_ts = "2026-05-18T08:00:00+09:00"
    (out / "ai_daily_trade_plan_20260519.json").write_text(
        json.dumps({"status": "AI_DAILY_TRADE_PLAN_READY", "generated_at": stale_ts}, ensure_ascii=False),
        encoding="utf-8",
    )
    (out / "live_order_plan_ai_latest.json").write_text(
        json.dumps({"generated_at": stale_ts}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_safety_audit(output_dir=out, db_path=db, freshness_date="2026-05-19")

    assert any(issue.category == "daily_ai_freshness" for issue in result.issues)
    assert "daily_ai_freshness" in result.checks


def test_safety_audit_report_docs_do_not_trigger_final_confirm(tmp_path: Path) -> None:
    """SAFETY_AUDIT.md / AI_LIVE_TRADE_RECOMMENDATION.md are documentation, not automation."""
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)

    result = run_safety_audit(output_dir=out, db_path=db)

    assert not any(issue.category == "final_confirm" for issue in result.issues)
    assert result.status in {SAFETY_AUDIT_OK, SAFETY_AUDIT_WARNING}


def test_safety_audit_project_shell_with_automated_live_approve_is_blocked(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    (tmp_path / "main.py").write_text("# cli\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "auto_live.sh").write_text(
        "#!/bin/bash\n"
        "python main.py live-approve --broker kis --approved --execute "
        "--allow-live-env --final-confirm I_UNDERSTAND_REAL_ORDER\n",
        encoding="utf-8",
    )

    result = run_safety_audit(output_dir=out, db_path=db)

    assert result.status == SAFETY_AUDIT_BLOCKED
    assert any(issue.category == "final_confirm" for issue in result.issues)


def test_safety_audit_daily_ai_missing_steps_warning(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = tmp_path / "data" / "deepsignal.db"
    _seed_ok(out, db)
    (out / "execute_approved_audit_20260517_100000.json").unlink()

    result = run_safety_audit(output_dir=out, db_path=db)

    assert result.status == SAFETY_AUDIT_WARNING
    assert any(issue.category == "daily_ai_workflow" for issue in result.issues)
    assert "execute-last-approved" in result.checks["daily_ai_workflow"]["next_action"]
