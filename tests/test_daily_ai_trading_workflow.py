from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from deepsignal.live_trading.daily_ai_trading_workflow import (
    build_daily_ai_status,
    build_daily_ai_trade_report,
    run_daily_ai_trade_plan,
)


def _fake_recommendation_runner(db_path: str, *, config, network: bool):
    root = Path(config.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    rec_json = root / "ai_live_trade_recommendation_20260519_020000.json"
    plan_json = root / "live_order_plan_ai_20260519_020000.json"
    md = root / "AI_LIVE_TRADE_RECOMMENDATION.md"
    order_plan = {
        "status": "PENDING_APPROVAL",
        "approval_required": True,
        "dry_run": True,
        "orders": [
            {
                "symbol": "005930",
                "side": "BUY",
                "estimated_order_value": 50_000.0,
                "estimated_qty": 1,
                "estimated_price": 50_000.0,
            }
        ],
    }
    rec_json.write_text(json.dumps({"status": "AI_RECOMMENDATION_READY"}, ensure_ascii=False), encoding="utf-8")
    plan_json.write_text(json.dumps(order_plan, ensure_ascii=False), encoding="utf-8")
    md.write_text("# recommendation\n", encoding="utf-8")
    result = SimpleNamespace(
        status="AI_RECOMMENDATION_READY",
        recommendations=[{"symbol": "005930"}],
        order_plan=order_plan,
    )
    return result, rec_json, plan_json, md


def test_daily_ai_trade_plan_creates_reports_and_latest_pointer(tmp_path: Path) -> None:
    result = run_daily_ai_trade_plan(
        "data/test.db",
        broker="kis",
        network=False,
        output_dir=tmp_path,
        recommendation_runner=_fake_recommendation_runner,
    )

    assert result.status == "AI_DAILY_TRADE_PLAN_READY"
    assert result.order_count == 1
    assert result.total_order_value == 50_000.0
    assert (tmp_path / "AI_DAILY_TRADE_PLAN.md").exists()
    latest = tmp_path / "live_order_plan_ai_latest.json"
    assert latest.exists()
    assert json.loads(latest.read_text(encoding="utf-8"))["orders"][0]["symbol"] == "005930"
    md = (tmp_path / "AI_DAILY_TRADE_PLAN.md").read_text(encoding="utf-8")
    assert "생성 시각" in md
    assert "기준 날짜" in md
    assert "Asia/Seoul" in md
    assert "live-approve" not in md.split("## Safety", 1)[0]
    assert "KIS order-cash POST" in md
    plan_jsons = sorted(tmp_path.glob("ai_daily_trade_plan_*.json"))
    assert plan_jsons
    plan_data = json.loads(plan_jsons[-1].read_text(encoding="utf-8"))
    latest_data = json.loads(latest.read_text(encoding="utf-8"))
    for data in (plan_data, latest_data):
        assert "+09:00" in data["generated_at"]
        assert data["generated_date"]
        assert data["timezone"] == "Asia/Seoul"


def test_daily_ai_trade_report_summarizes_latest_files(tmp_path: Path) -> None:
    (tmp_path / "ai_live_trade_recommendation_20260519_020000.json").write_text(
        json.dumps({"status": "AI_RECOMMENDATION_READY"}),
        encoding="utf-8",
    )
    (tmp_path / "telegram_approval_audit_20260519_020100.json").write_text(
        json.dumps({"status": "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED"}),
        encoding="utf-8",
    )
    (tmp_path / "execute_approved_audit_20260519_020200.json").write_text(
        json.dumps({"status": "EXECUTE_APPROVED_COMPLETED", "execution_result": {"actual_order_attempted": True}}),
        encoding="utf-8",
    )

    report = build_daily_ai_trade_report(output_dir=tmp_path, broker="kis", network=False)

    assert report.status == "AI_DAILY_TRADE_REPORT_READY"
    assert report.summary["ai_recommendation_status"] == "AI_RECOMMENDATION_READY"
    assert report.summary["order_submitted"] is True
    assert (tmp_path / "AI_DAILY_TRADE_REPORT.md").exists()
    assert Path(report.json_path).exists()


def test_daily_ai_status_next_command_progression(tmp_path: Path) -> None:
    ts = "2026-05-19T08:00:00+09:00"
    status = build_daily_ai_status(output_dir=tmp_path, freshness_date="2026-05-19")
    assert "daily-ai-trade-plan" in status.next_command

    (tmp_path / "AI_DAILY_TRADE_PLAN.md").write_text("# plan", encoding="utf-8")
    (tmp_path / "ai_daily_trade_plan_20260519_020000.json").write_text(
        json.dumps({"generated_at": ts}),
        encoding="utf-8",
    )
    (tmp_path / "live_order_plan_ai_latest.json").write_text(
        json.dumps({"generated_at": ts}),
        encoding="utf-8",
    )
    status = build_daily_ai_status(output_dir=tmp_path, freshness_date="2026-05-19")
    assert "telegram-approval-request" in status.next_command

    (tmp_path / "telegram_approval_request_20260519_020050.json").write_text(
        json.dumps({"status": "PENDING", "generated_at": ts}),
        encoding="utf-8",
    )
    (tmp_path / "telegram_approval_audit_20260519_020100.json").write_text(
        json.dumps({"status": "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED", "generated_at": ts}),
        encoding="utf-8",
    )
    status = build_daily_ai_status(output_dir=tmp_path, freshness_date="2026-05-19")
    assert "execute-last-approved" in status.next_command

    (tmp_path / "execute_approved_audit_20260519_020200.json").write_text(
        json.dumps({"status": "EXECUTE_APPROVED_COMPLETED", "generated_at": ts}),
        encoding="utf-8",
    )
    status = build_daily_ai_status(output_dir=tmp_path, freshness_date="2026-05-19")
    assert "daily-ai-trade-report" in status.next_command
    status_json = sorted(tmp_path.glob("ai_daily_status_*.json"))[-1]
    status_data = json.loads(status_json.read_text(encoding="utf-8"))
    assert status_data["timezone"] == "Asia/Seoul"
    assert "+09:00" in status_data["generated_at"]
    status_md = (tmp_path / "AI_DAILY_STATUS.md").read_text(encoding="utf-8")
    assert "생성 시각" in status_md
