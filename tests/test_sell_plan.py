"""sell_plan: 운영자 검토용 수동 SELL 계획서."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.risk_guard import RiskGuardPolicy
from deepsignal.live_trading.sell_plan import (
    SELL_PLAN_STATUS_EXIT,
    SELL_PLAN_STATUS_HOLD,
    SELL_PLAN_STATUS_NO_DATA,
    SELL_PLAN_STATUS_REDUCE,
    SELL_PLAN_STATUS_REVIEW,
    build_sell_plan,
    write_sell_plan_report,
)
from deepsignal.storage.database import init_database, save_real_account_snapshot, save_real_positions


def _seed_position(db: str, *, avg: float = 100.0, cur: float = 100.0, qty: int = 10) -> None:
    ts = "2026-05-16T10:00:00"
    save_real_positions(
        db,
        ts,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": qty,
                "avg_price": avg,
                "current_price": cur,
                "market_value": cur * qty,
                "raw": {"pdno": "005930"},
            }
        ],
    )
    save_real_account_snapshot(
        db,
        ts,
        "kis",
        cash=1000.0,
        withdrawable_cash=1000.0,
        total_market_value=cur * qty,
        total_equity=1000.0 + cur * qty,
        raw_payload={"timestamp": ts},
    )


def test_no_positions_no_data(tmp_path: Path) -> None:
    db = str(tmp_path / "sell.db")
    init_database(db)
    result = build_sell_plan(db, output_dir=tmp_path)
    assert result.status == SELL_PLAN_STATUS_NO_DATA
    assert result.items == []
    assert result.warnings


def test_warn_loss_review(tmp_path: Path) -> None:
    db = str(tmp_path / "sell.db")
    init_database(db)
    _seed_position(db, avg=100.0, cur=96.0)
    result = build_sell_plan(db, output_dir=tmp_path)
    assert result.status == SELL_PLAN_STATUS_REVIEW
    assert result.items[0].suggested_action == SELL_PLAN_STATUS_REVIEW
    assert result.items[0].suggested_sell_quantity == 0


def test_stop_loss_exit(tmp_path: Path) -> None:
    db = str(tmp_path / "sell.db")
    init_database(db)
    _seed_position(db, avg=100.0, cur=90.0, qty=3)
    result = build_sell_plan(db, output_dir=tmp_path)
    assert result.status == SELL_PLAN_STATUS_EXIT
    assert result.items[0].suggested_sell_ratio == 1.0
    assert result.items[0].suggested_sell_quantity == 3


def test_take_profit_reduce(tmp_path: Path) -> None:
    db = str(tmp_path / "sell.db")
    init_database(db)
    _seed_position(db, avg=100.0, cur=120.0, qty=10)
    result = build_sell_plan(db, output_dir=tmp_path)
    assert result.status == SELL_PLAN_STATUS_REDUCE
    assert result.items[0].suggested_sell_ratio == 0.5
    assert result.items[0].suggested_sell_quantity == 5


def test_hold(tmp_path: Path) -> None:
    db = str(tmp_path / "sell.db")
    init_database(db)
    _seed_position(db, avg=100.0, cur=101.0)
    result = build_sell_plan(db, output_dir=tmp_path)
    assert result.status == SELL_PLAN_STATUS_HOLD
    assert result.items[0].suggested_sell_quantity == 0


def test_threshold_override(tmp_path: Path) -> None:
    db = str(tmp_path / "sell.db")
    init_database(db)
    _seed_position(db, avg=100.0, cur=94.0)
    result = build_sell_plan(
        db,
        output_dir=tmp_path,
        policy=RiskGuardPolicy(stop_loss_pct=-0.05, take_profit_pct=0.15, warn_loss_pct=-0.03),
    )
    assert result.status == SELL_PLAN_STATUS_EXIT


def test_markdown_and_json_generation(tmp_path: Path) -> None:
    db = str(tmp_path / "sell.db")
    init_database(db)
    _seed_position(db, avg=280000.0, cur=270500.0, qty=1)
    result = build_sell_plan(db, output_dir=tmp_path)
    jp, mp = write_sell_plan_report(result, output_dir=tmp_path)
    assert jp.is_file()
    assert mp.is_file()
    body = json.loads(jp.read_text(encoding="utf-8"))
    assert body["status"] == SELL_PLAN_STATUS_REVIEW
    text = mp.read_text(encoding="utf-8")
    assert "# DeepSignal Sell Plan" in text
    assert "005930" in text
    assert "This plan does NOT place SELL orders" in text
    assert "live-approve SELL execution is not implemented" in text
