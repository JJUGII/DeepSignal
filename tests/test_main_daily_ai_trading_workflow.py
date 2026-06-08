from __future__ import annotations

from pathlib import Path

import main as main_mod
from deepsignal.live_trading.daily_ai_trading_workflow import (
    DailyAIStatusResult,
    DailyAITradePlanResult,
    DailyAITradeReportResult,
    WorkflowStep,
)


def test_main_daily_ai_trade_plan_smoke(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import daily_ai_trading_workflow as wf

    called: dict[str, object] = {}

    def fake_plan(db_path: str, **kwargs):
        called.update(kwargs)
        md = tmp_path / "AI_DAILY_TRADE_PLAN.md"
        latest = tmp_path / "live_order_plan_ai_latest.json"
        rec = tmp_path / "ai_live_trade_recommendation_test.json"
        plan = tmp_path / "live_order_plan_ai_test.json"
        for p in (md, latest, rec, plan):
            p.write_text("{}", encoding="utf-8")
        return DailyAITradePlanResult(
            generated_at="2026-05-19T02:00:00",
            status="AI_DAILY_TRADE_PLAN_READY",
            steps=[WorkflowStep("ai-live-recommend", "OK", "ok")],
            recommendation_status="AI_RECOMMENDATION_READY",
            recommendation_count=1,
            order_count=1,
            total_order_value=50_000.0,
            recommendation_json=rec.as_posix(),
            order_plan_json=plan.as_posix(),
            latest_order_plan_json=latest.as_posix(),
            markdown_path=md.as_posix(),
        )

    monkeypatch.setattr(wf, "run_daily_ai_trade_plan", fake_plan)

    rc = main_mod.main(["daily-ai-trade-plan", "--broker", "kis", "--output-dir", str(tmp_path)])

    assert rc == 0
    assert called["broker"] == "kis"
    assert called["network"] is False


def test_main_daily_ai_trade_report_smoke(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import daily_ai_trading_workflow as wf

    def fake_report(**kwargs):
        md = tmp_path / "AI_DAILY_TRADE_REPORT.md"
        js = tmp_path / "ai_daily_trade_report_test.json"
        md.write_text("# report\n", encoding="utf-8")
        js.write_text("{}", encoding="utf-8")
        return DailyAITradeReportResult(
            generated_at="2026-05-19T02:00:00",
            status="AI_DAILY_TRADE_REPORT_READY",
            summary={},
            source_files={},
            markdown_path=md.as_posix(),
            json_path=js.as_posix(),
        )

    monkeypatch.setattr(wf, "build_daily_ai_trade_report", fake_report)

    rc = main_mod.main(["daily-ai-trade-report", "--broker", "kis", "--output-dir", str(tmp_path)])

    assert rc == 0


def test_main_daily_ai_status_smoke(tmp_path: Path, monkeypatch) -> None:
    from deepsignal.live_trading import daily_ai_trading_workflow as wf

    def fake_status(**kwargs):
        md = tmp_path / "AI_DAILY_STATUS.md"
        js = tmp_path / "ai_daily_status_test.json"
        md.write_text("# status\n", encoding="utf-8")
        js.write_text("{}", encoding="utf-8")
        return DailyAIStatusResult(
            generated_at="2026-05-19T02:00:00",
            status="AI_DAILY_STATUS_READY",
            checks={"plan_created": False},
            latest_files={},
            next_command="python main.py daily-ai-trade-plan --broker kis --network --output-dir outputs",
            markdown_path=md.as_posix(),
            json_path=js.as_posix(),
        )

    monkeypatch.setattr(wf, "build_daily_ai_status", fake_status)

    rc = main_mod.main(["daily-ai-status", "--output-dir", str(tmp_path)])

    assert rc == 0


def test_execute_last_approved_without_approval_blocks(tmp_path: Path) -> None:
    rc = main_mod.main(["execute-last-approved", "--output-dir", str(tmp_path)])

    assert rc == 1
