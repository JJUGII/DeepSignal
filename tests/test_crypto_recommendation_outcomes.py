"""crypto_recommendation_outcomes — 추천·체결·실현 손익 추적."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
from deepsignal.crypto_trading.crypto_recommendation_outcomes import (
    apply_crypto_fill_update,
    apply_crypto_trade_pipeline,
    crypto_outcomes_db_path,
    generate_crypto_performance_report,
    init_crypto_outcomes_db,
    record_crypto_recommendation,
)
from deepsignal.crypto_trading.upbit_broker import UpbitOrderResult
from deepsignal.live_trading.weekly_maintenance import run_weekly_maintenance


def _plan(*, side: str = "buy", market: str = "KRW-BTC") -> CryptoOrderPlan:
    return CryptoOrderPlan(
        market=market,
        side=side,
        limit_price=50_000_000.0,
        avg_buy_price=48_000_000.0 if side == "sell" else 0.0,
        pnl_pct=2.5 if side == "sell" else 0.0,
        display_name="비트코인",
        reason="test_signal",
        created_at="2026-05-24T10:00:00+09:00",
    )


def _row(db: Path, row_id: int) -> sqlite3.Row:
    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM crypto_recommendation_outcomes WHERE id = ?",
            (row_id,),
        ).fetchone()


def test_record_buy_recommendation(tmp_path: Path) -> None:
    db = crypto_outcomes_db_path(tmp_path)
    plan = _plan(side="buy")
    oid = record_crypto_recommendation(plan, outcomes_db=db)
    row = _row(db, oid)
    assert row["market"] == "KRW-BTC"
    assert row["side"] == "buy"
    assert row["display_name"] == "비트코인"
    assert int(row["executed"]) == 0
    assert row["order_uuid"] is None
    assert float(row["current_price"]) == 50_000_000.0


def test_apply_fill_updates_executed_on_buy(tmp_path: Path) -> None:
    db = crypto_outcomes_db_path(tmp_path)
    plan = _plan(side="buy")
    oid = record_crypto_recommendation(plan, outcomes_db=db)
    result = UpbitOrderResult(
        market="KRW-BTC",
        side="bid",
        order_type="limit",
        price=49_900_000.0,
        volume=0.001,
        krw_amount=49_900.0,
        status="done",
        uuid="buy-uuid-1",
        dry_run=True,
    )
    status = {"uuid": "buy-uuid-1", "executed_volume": 0.001, "price": 49_900_000.0, "paid_fee": 100.0, "state": "done"}
    stats = apply_crypto_fill_update(plan, result, status, "done", outcomes_db=db, outcome_id=oid)
    row = _row(db, oid)
    assert stats["updated"] is True
    assert int(row["executed"]) == 1
    assert row["order_uuid"] == "buy-uuid-1"
    assert float(row["fill_price"]) == 49_900_000.0
    assert float(row["fill_volume"]) == 0.001
    assert float(row["fee"]) == 100.0
    assert stats.get("crypto_trade_id") is not None


def test_sell_fill_computes_realized_pnl_pct(tmp_path: Path) -> None:
    db = crypto_outcomes_db_path(tmp_path)
    plan = _plan(side="sell")
    oid = record_crypto_recommendation(plan, outcomes_db=db)
    result = UpbitOrderResult(
        market="KRW-BTC",
        side="ask",
        order_type="limit",
        price=50_000_000.0,
        volume=0.001,
        krw_amount=50_000.0,
        status="done",
        uuid="sell-uuid-1",
        dry_run=True,
    )
    status = {"uuid": "sell-uuid-1", "executed_volume": 0.001, "price": 50_000_000.0, "paid_fee": 50.0, "state": "done"}
    apply_crypto_fill_update(plan, result, status, "done", outcomes_db=db, outcome_id=oid)
    row = _row(db, oid)
    expected = (50_000_000.0 - 48_000_000.0) / 48_000_000.0 * 100.0
    assert float(row["realized_pnl_pct"]) == pytest.approx(expected, rel=1e-6)
    assert row["closed_at"] is not None
    assert row["exit_reason"] == "test_signal"


def test_apply_crypto_trade_pipeline_attaches_uuid(tmp_path: Path) -> None:
    db = crypto_outcomes_db_path(tmp_path)
    plan = _plan(side="buy")
    record_crypto_recommendation(plan, outcomes_db=db)
    result = UpbitOrderResult(
        market="KRW-BTC",
        side="bid",
        order_type="limit",
        price=50_000_000.0,
        volume=0.001,
        krw_amount=50_000.0,
        status="wait",
        uuid="pending-uuid",
        dry_run=True,
    )
    out = apply_crypto_trade_pipeline(plan, result, outcomes_db=db)
    assert out["order_uuid"] == "pending-uuid"
    with sqlite3.connect(str(db)) as conn:
        uuid = conn.execute(
            "SELECT order_uuid FROM crypto_recommendation_outcomes ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    assert uuid == "pending-uuid"


def test_weekly_maintenance_crypto_performance_step(tmp_path: Path) -> None:
    out = tmp_path / "outputs"
    out.mkdir(parents=True)
    db_path = tmp_path / "data" / "deepsignal.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_text("", encoding="utf-8")

    odb = init_crypto_outcomes_db(out / "crypto_recommendation_outcomes.db")
    record_crypto_recommendation(_plan(side="buy"), outcomes_db=odb)

    from tests.test_weekly_maintenance import _seed_db, _seed_outputs

    _seed_outputs(out)
    _seed_db(db_path)

    result = run_weekly_maintenance(
        output_dir=out,
        archive_dir=out / "archive",
        db_path=db_path,
        keep_days=365,
        keep_latest=100,
    )
    crypto_step = next(s for s in result.steps if s.name == "crypto_recommendation_performance")
    assert crypto_step.success is True
    assert (out / "CRYPTO_RECOMMENDATION_PERFORMANCE.md").is_file()
    jp, mp, summary = generate_crypto_performance_report(odb, output_dir=out, days=7)
    assert jp.is_file() and mp.is_file()
    assert summary.total_rows >= 1
