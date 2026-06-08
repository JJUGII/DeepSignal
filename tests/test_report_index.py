"""report_index: outputs/archive 정적 인덱스."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.report_index import (
    build_report_index,
    run_report_index,
)


def _write_json(path: Path, body: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_empty_outputs_creates_index(tmp_path: Path) -> None:
    result, html_path, md_path, json_path = run_report_index(output_dir=tmp_path)
    assert result.items == []
    assert html_path.is_file()
    assert md_path.is_file()
    assert json_path.is_file()
    assert "DeepSignal Report Index" in html_path.read_text(encoding="utf-8")


def test_category_detection_and_status_extraction(tmp_path: Path) -> None:
    _write_json(tmp_path / "risk_alert_20260516_120000.json", {"status": "STOP_LOSS_ALERT", "alerts": ["x"]})
    _write_json(tmp_path / "reconcile_live_account_20260516_120100.json", {"success": False})
    result = build_report_index(output_dir=tmp_path)
    by_name = {item.name: item for item in result.items}
    assert by_name["risk_alert_20260516_120000.json"].category == "risk"
    assert by_name["risk_alert_20260516_120000.json"].status == "STOP_LOSS_ALERT"
    assert by_name["reconcile_live_account_20260516_120100.json"].category == "reconcile"
    assert by_name["reconcile_live_account_20260516_120100.json"].status == "success=False"


def test_date_grouping(tmp_path: Path) -> None:
    _write_json(tmp_path / "daily_ops_summary_20260516_120000.json", {"status": "OK"})
    _write_json(tmp_path / "ops_dashboard_20260516_120100.json", {"status": "WARNING"})
    result = build_report_index(output_dir=tmp_path)
    assert "2026-05-16" in result.by_date
    assert result.by_date["2026-05-16"]["count"] == 2
    assert result.by_date["2026-05-16"]["highest_severity"] == "WARNING"


def test_archive_scan(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    _write_json(archive / "sell_plan_20260515_120000.json", {"status": "REVIEW"})
    result = build_report_index(output_dir=tmp_path, archive_dir=archive)
    assert len(result.items) == 1
    assert result.items[0].path == "archive/sell_plan_20260515_120000.json"
    assert result.items[0].category == "sell_plan"


def test_html_md_json_generation(tmp_path: Path) -> None:
    _write_json(tmp_path / "post_trade_runbook_20260516_120000.json", {"final_status": "POST_TRADE_WARNING"})
    (tmp_path / "AI_DAILY_TRADE_PLAN.md").write_text("# plan", encoding="utf-8")
    ts = "2026-05-19T08:00:00+09:00"
    _write_json(
        tmp_path / "ai_daily_trade_plan_20260516_120100.json",
        {"status": "AI_DAILY_TRADE_PLAN_READY", "generated_at": ts},
    )
    _write_json(tmp_path / "live_order_plan_ai_latest.json", {"status": "PENDING_APPROVAL", "orders": [], "generated_at": ts})
    result, html_path, md_path, json_path = run_report_index(output_dir=tmp_path)
    html = html_path.read_text(encoding="utf-8")
    md = md_path.read_text(encoding="utf-8")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert "By Date" in html
    assert "AI 일일 매매 운영" in html
    assert "오늘 계획" in html
    assert "Recent Reports" in md
    assert "AI 일일 매매 운영" in md
    assert data["items"]
    assert "daily_ai_workflow" in data
    assert data["daily_ai_workflow"]["plan_status"] == "AI_DAILY_TRADE_PLAN_READY"
    assert data["network_called"] is False
    assert data["실제_주문_없음"] is True
    assert result.by_category["post_trade_runbook"]["count"] == 1
    assert result.by_category["ai_daily_trade_plan"]["count"] >= 1


def test_broken_json_graceful(tmp_path: Path) -> None:
    p = tmp_path / "ops_dashboard_20260516_120000.json"
    p.write_text("{not-json", encoding="utf-8")
    result = build_report_index(output_dir=tmp_path)
    assert len(result.items) == 1
    assert result.items[0].status is None
    assert result.items[0].summary["parse_error"] is True
    assert result.warnings


def test_max_items_limits_recent_reports(tmp_path: Path) -> None:
    for i in range(5):
        _write_json(tmp_path / f"notification_audit_20260516_12000{i}.json", {"status": "OK"})
    result = build_report_index(output_dir=tmp_path, max_items=3)
    assert len(result.items) == 3
