from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from deepsignal.live_trading.daily_ai_freshness import (
    SOURCE_GENERATED_AT,
    SOURCE_MTIME_FALLBACK,
    DailyAIFreshnessPolicy,
    check_file_freshness,
    build_daily_ai_freshness,
    validate_execution_freshness,
)
from deepsignal.live_trading.daily_ai_status_reader import read_daily_ai_workflow_status


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_same_local_date_fresh(tmp_path: Path) -> None:
    ref = datetime(2026, 5, 19, 1, 0, tzinfo=UTC)
    path = tmp_path / "ai_daily_trade_plan_20260519.json"
    _write_json(path, {"generated_at": "2026-05-19T08:00:00+09:00", "status": "READY"})

    result = check_file_freshness(
        path,
        target_name="plan",
        reference_local_date=ref.date(),
        now_utc=ref,
    )

    assert result.status == "FRESH"
    assert result.same_local_date is True
    assert result.generated_at is not None


def test_previous_day_stale(tmp_path: Path) -> None:
    ref = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    path = tmp_path / "live_order_plan_ai_latest.json"
    _write_json(path, {"generated_at": "2026-05-18T23:00:00+09:00"})

    result = check_file_freshness(
        path,
        target_name="latest_order_plan",
        reference_local_date=ref.date(),
        now_utc=ref,
    )

    assert result.status == "STALE"
    assert result.same_local_date is False
    assert result.severity == "blocked"


def test_max_age_hours_stale(tmp_path: Path) -> None:
    now = datetime(2026, 5, 19, 15, 0, tzinfo=UTC)
    path = tmp_path / "ai_daily_trade_plan.json"
    _write_json(path, {"generated_at": "2026-05-19T01:00:00+09:00"})
    policy = DailyAIFreshnessPolicy(max_plan_age_hours=12)

    result = check_file_freshness(
        path,
        target_name="plan",
        policy=policy,
        reference_local_date=now.date(),
        max_age_hours=12,
        now_utc=now,
    )

    assert result.status == "STALE"


def test_missing_file_result(tmp_path: Path) -> None:
    result = check_file_freshness(
        tmp_path / "missing.json",
        target_name="plan",
        reference_local_date=datetime(2026, 5, 19, tzinfo=UTC).date(),
    )

    assert result.status == "MISSING"


def test_json_generated_at_preferred_over_mtime(tmp_path: Path, monkeypatch) -> None:
    now = datetime(2026, 5, 19, 10, 0, tzinfo=UTC)
    path = tmp_path / "plan.json"
    _write_json(path, {"generated_at": "2026-05-19T09:00:00+09:00"})
    old = now - timedelta(days=2)
    path.touch()
    import os

    os.utime(path, (old.timestamp(), old.timestamp()))

    result = check_file_freshness(
        path,
        target_name="plan",
        reference_local_date=now.date(),
        now_utc=now,
    )

    assert result.status == "FRESH"
    assert "2026-05-19" in (result.generated_at or "")


def test_generated_date_same_date_assist(tmp_path: Path) -> None:
    now = datetime(2026, 5, 19, 1, 0, tzinfo=UTC)
    path = tmp_path / "plan.json"
    _write_json(
        path,
        {
            "generated_at": "2026-05-19T08:00:00+09:00",
            "generated_date": "2026-05-19",
            "timezone": "Asia/Seoul",
        },
    )

    result = check_file_freshness(
        path,
        target_name="plan",
        reference_local_date=now.date(),
        now_utc=now,
    )

    assert result.status == "FRESH"
    assert result.freshness_source == SOURCE_GENERATED_AT
    assert result.same_local_date is True


def test_mtime_fallback_warning(tmp_path: Path) -> None:
    now = datetime(2026, 5, 19, 10, 0, tzinfo=UTC)
    path = tmp_path / "legacy.json"
    path.write_text("{}", encoding="utf-8")
    import os

    ts = datetime(2026, 5, 19, 8, 0, tzinfo=UTC).timestamp()
    os.utime(path, (ts, ts))

    result = check_file_freshness(
        path,
        target_name="plan",
        reference_local_date=now.date(),
        now_utc=now,
    )

    assert result.freshness_source == SOURCE_MTIME_FALLBACK
    assert result.warning and "mtime fallback" in result.warning


def test_fallback_modified_time_when_no_generated_at(tmp_path: Path) -> None:
    now = datetime(2026, 5, 19, 10, 0, tzinfo=UTC)
    path = tmp_path / "AI_DAILY_TRADE_PLAN.md"
    path.write_text("# plan", encoding="utf-8")
    import os

    ts = datetime(2026, 5, 19, 8, 0, tzinfo=UTC).timestamp()
    os.utime(path, (ts, ts))

    result = check_file_freshness(
        path,
        target_name="plan",
        reference_local_date=now.date(),
        now_utc=now,
    )

    assert result.status == "FRESH"
    assert result.modified_at is not None


def test_build_daily_ai_freshness_bundle(tmp_path: Path) -> None:
    ref_day = "2026-05-19"
    now = datetime(2026, 5, 19, 10, 0, tzinfo=UTC)
    (tmp_path / "AI_DAILY_TRADE_PLAN.md").write_text("# plan", encoding="utf-8")
    _write_json(
        tmp_path / "ai_daily_trade_plan_20260519.json",
        {"generated_at": "2026-05-19T08:00:00+09:00"},
    )
    _write_json(
        tmp_path / "live_order_plan_ai_latest.json",
        {"generated_at": "2026-05-19T08:00:00+09:00"},
    )

    results = build_daily_ai_freshness(tmp_path, freshness_date=ref_day, now_utc=now)

    assert results["plan"].status == "FRESH"
    assert results["latest_order_plan"].status == "FRESH"


def test_daily_status_next_action_stale_plan(tmp_path: Path) -> None:
    ref_day = "2026-05-19"
    (tmp_path / "AI_DAILY_TRADE_PLAN.md").write_text("# plan", encoding="utf-8")
    _write_json(
        tmp_path / "ai_daily_trade_plan_20260519.json",
        {"generated_at": "2026-05-18T08:00:00+09:00", "status": "AI_DAILY_TRADE_PLAN_READY"},
    )
    _write_json(
        tmp_path / "live_order_plan_ai_latest.json",
        {"generated_at": "2026-05-18T08:00:00+09:00"},
    )

    status = read_daily_ai_workflow_status(tmp_path, freshness_date=ref_day)

    assert "daily-ai-trade-plan" in status.next_action
    assert any("오래" in w for w in status.warnings)
