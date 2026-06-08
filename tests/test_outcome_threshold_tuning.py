"""Tests for outcome-based threshold tuning ([학습루프-02])."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import main as main_mod

from deepsignal.live_trading.ai_recommendation.outcome_threshold_tuning import (
    OUTCOME_TUNING_MD,
    blend_threshold_summaries,
    compute_outcome_threshold_tuning,
    load_outcome_samples,
    run_tune_threshold_from_outcomes,
)
from deepsignal.live_trading.ai_recommendation.recommendation_outcomes import init_outcomes_db
from deepsignal.live_trading.ai_recommendation.validation_threshold_tuning import (
    THRESHOLD_SUMMARY_FILENAME,
    ValidationThresholdSummary,
    load_threshold_summary,
    write_threshold_summary,
)


def _insert_outcome(
    db: Path,
    *,
    symbol: str = "005930",
    score: float = 72.0,
    ret: float = 2.5,
    closed: bool = True,
    days_ago: int = 1,
) -> None:
    created = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    closed_at = created if closed else None
    realized = ret if closed else None
    executed = 1
    max_profit = ret if not closed else max(ret, 1.0)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO recommendation_outcomes ("
            "run_id, created_at, symbol, action, final_score, allowed_for_plan, executed, "
            "entry_price, closed_at, realized_pnl_pct, max_profit_pct, max_loss_pct"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "run1",
                created,
                symbol,
                "BUY",
                score,
                1,
                executed,
                70000.0,
                closed_at,
                realized,
                max_profit,
                -1.0,
            ),
        )
        conn.commit()


def _seed_many_wins(db: Path, n: int = 12) -> None:
    for i in range(n):
        _insert_outcome(
            db,
            symbol=f"S{i % 3:03d}",
            score=72.0 if i % 2 == 0 else 52.0,
            ret=3.0 if i % 2 == 0 else -2.0,
            days_ago=1 + (i % 5),
        )


def test_pick_threshold_from_outcomes(tmp_path: Path) -> None:
    db = init_outcomes_db(tmp_path / "recommendation_outcomes.db")
    _seed_many_wins(db, 14)
    samples = load_outcome_samples(db, lookback_days=60)
    assert len(samples) >= 10
    result = compute_outcome_threshold_tuning(db, min_samples=10, lookback_days=60)
    assert 50.0 <= result.global_block["recommended_min_final_score"] <= 75.0


def test_insufficient_samples_keeps_default_60(tmp_path: Path) -> None:
    db = init_outcomes_db(tmp_path / "recommendation_outcomes.db")
    _insert_outcome(db, score=80.0, ret=5.0)
    result = compute_outcome_threshold_tuning(db, min_samples=10, lookback_days=60)
    assert result.global_block["recommended_min_final_score"] == 60.0


def test_blend_with_validation(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    out.mkdir()
    validation = ValidationThresholdSummary(global_threshold=60.0, default_min_final_score=60.0)
    write_threshold_summary(validation, out)
    outcome = ValidationThresholdSummary(global_threshold=70.0, default_min_final_score=60.0)
    merged = blend_threshold_summaries(outcome, validation, weight_outcome=0.5, generated_at="2026-01-01T00:00:00")
    assert merged.global_threshold == 65.0


def test_writes_validation_summary_json(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    db = init_outcomes_db(out / "recommendation_outcomes.db")
    _seed_many_wins(db, 12)
    write_threshold_summary(ValidationThresholdSummary(global_threshold=58.0), out)
    result, jp, mp, sp = run_tune_threshold_from_outcomes(
        outcomes_db=db,
        output_dir=out,
        min_samples=8,
        blend_with_validation=0.5,
    )
    assert sp.name == THRESHOLD_SUMMARY_FILENAME
    loaded = load_threshold_summary(out)
    assert loaded is not None
    assert loaded.global_threshold == result.merged_summary.global_threshold
    assert (out / OUTCOME_TUNING_MD).is_file()
    payload = json.loads(jp.read_text(encoding="utf-8"))
    assert payload["source"] == "outcomes"
    assert "global" in payload


def test_cli_tune_threshold(tmp_path: Path, capsys) -> None:
    out = tmp_path / "outputs"
    db = init_outcomes_db(out / "recommendation_outcomes.db")
    _seed_many_wins(db, 12)
    rc = main_mod.main(
        [
            "tune-threshold-from-outcomes",
            "--outcomes-db",
            str(db),
            "--output-dir",
            str(out),
            "--min-samples",
            "8",
        ]
    )
    assert rc == 0
    assert "outcome threshold tuning" in capsys.readouterr().out.lower()


def test_weekly_maintenance_tune_option(tmp_path: Path) -> None:
    from deepsignal.storage.database import init_database, save_real_account_snapshot

    out = tmp_path / "outputs"
    out.mkdir()
    for name in ["OPS_DASHBOARD.html", "REPORT_INDEX.html", "DAILY_OPS_SUMMARY.md", "RISK_ALERT.md", "SELL_PLAN.md", "OPS_DRY_RUN.md"]:
        (out / name).write_text("# x", encoding="utf-8")
    for name in [
        "live_account_snapshot_20260517_100000.json",
        "reconcile_live_account_20260517_100000.json",
        "risk_alert_20260517_100000.json",
        "ops_dashboard_20260517_100000.json",
        "sell_plan_20260517_100000.json",
        "daily_ops_summary_20260517_100000.json",
        "notification_audit_20260517_100000.json",
        "live_fill_summary_20260517_100000.json",
    ]:
        (out / name).write_text(json.dumps({"status": "OK", "warnings": [], "items": []}), encoding="utf-8")
    db_main = init_database(str(tmp_path / "main.db"))
    save_real_account_snapshot(
        db_main,
        datetime.now().isoformat(timespec="seconds"),
        "kis",
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
        total_market_value=0.0,
        total_equity=1_000_000.0,
        raw_payload={},
    )
    odb = init_outcomes_db(out / "recommendation_outcomes.db")
    _seed_many_wins(odb, 12)
    rc = main_mod.main(
        [
            "weekly-maintenance",
            "--output-dir",
            str(out),
            "--db-path",
            str(db_main),
            "--keep-days",
            "365",
            "--keep-latest",
            "100",
            "--tune-threshold-from-outcomes",
        ]
    )
    assert rc == 0
    md = (out / "WEEKLY_MAINTENANCE.md").read_text(encoding="utf-8")
    assert "tune_threshold_from_outcomes" in md
