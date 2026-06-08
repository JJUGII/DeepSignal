"""Tests for recommendation outcomes DB and performance report."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    OperationalRiskContext,
    RecommendationConfig,
    RecommendationResult,
    RecommendationRunResult,
)
from deepsignal.live_trading.ai_recommendation.recommendation_outcomes import (
    PERFORMANCE_REPORT_MD,
    init_outcomes_db,
    record_recommendation_run,
    refresh_recommendation_outcomes,
    generate_recommendation_performance_report,
)
from deepsignal.storage.database import init_database, save_real_order_history


def _rec(**kwargs) -> RecommendationResult:
    base = dict(
        symbol="005930",
        action="BUY",
        action_label="매수",
        confidence=0.8,
        priority=80,
        reason="test",
        risk_notes=[],
        current_quantity=0,
        current_value=0.0,
        current_weight=0.0,
        target_weight=0.1,
        suggested_quantity=10,
        suggested_limit_price=70000.0,
        estimated_order_value=700000.0,
        source_signal_score=72.0,
        macro_context={},
        account_context={},
        blocked_reasons=[],
        allowed_for_plan=True,
        score_breakdown={
            "technical_score": 40.0,
            "news_score": 20.0,
            "macro_score": 12.0,
            "final_score": 72.0,
        },
        quality_gates={"liquidity": "ok", "min_final_score": "65.0(validation_tuned)", "score_threshold": "ok"},
    )
    base.update(kwargs)
    return RecommendationResult(**base)


def test_record_and_report(tmp_path: Path) -> None:
    out_db = tmp_path / "recommendation_outcomes.db"
    run = RecommendationRunResult(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        status="OK",
        config=RecommendationConfig(),
        account_context=AccountContext(cash=1_000_000.0),
        macro_context={},
        operational_risk_context=OperationalRiskContext(),
        recommendations=[_rec(), _rec(symbol="000660", allowed_for_plan=False, source_signal_score=50.0)],
        order_plan={"orders": []},
        output_files={},
    )
    n = record_recommendation_run(run, outcomes_db=out_db)
    assert n == 2
    jp, mp, summary = generate_recommendation_performance_report(out_db, output_dir=tmp_path, days=30)
    assert summary.total_rows == 2
    assert mp.name == PERFORMANCE_REPORT_MD
    assert jp.is_file()


def test_refresh_marks_executed(tmp_path: Path) -> None:
    main_db = str(init_database(str(tmp_path / "main.db")))
    out_db = tmp_path / "recommendation_outcomes.db"
    ts = datetime.now().isoformat(timespec="seconds")
    run = RecommendationRunResult(
        generated_at=ts,
        status="OK",
        config=RecommendationConfig(),
        account_context=AccountContext(cash=1_000_000.0),
        macro_context={},
        operational_risk_context=OperationalRiskContext(),
        recommendations=[_rec()],
        order_plan={},
        output_files={},
    )
    record_recommendation_run(run, outcomes_db=out_db)
    save_real_order_history(
        main_db,
        broker="kis",
        symbol="005930",
        side="BUY",
        quantity=10,
        limit_price=71000.0,
        estimated_order_value=710000.0,
        status="filled",
        order_id="OID1",
        audit_path="outputs/test.json",
        raw_payload={},
    )
    stats = refresh_recommendation_outcomes(main_db, out_db)
    assert stats["executed_marked"] >= 1
    import sqlite3

    with sqlite3.connect(str(out_db)) as conn:
        row = conn.execute("SELECT executed, entry_price FROM recommendation_outcomes LIMIT 1").fetchone()
    assert row[0] == 1
    assert float(row[1]) > 0
