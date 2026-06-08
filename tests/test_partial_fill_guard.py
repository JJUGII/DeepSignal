"""order_guard partial fill ([실전-8])."""

from __future__ import annotations

from datetime import datetime, timedelta

from deepsignal.live_trading.fill_tracker import PartialFillStatus
from deepsignal.live_trading.order_guard import check_duplicate_order_risk
from deepsignal.live_trading.reconcile import ReconcileResult
from deepsignal.storage.database import init_database, save_real_fill


def _now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0)


def test_partial_fill_open_blocks() -> None:
    partials = [
        PartialFillStatus(
            order_id="99",
            symbol="005930",
            ordered_quantity=10,
            filled_quantity=3,
            remaining_quantity=7,
            avg_fill_price=70000.0,
            fully_filled=False,
            partially_filled=True,
            unfilled=False,
        )
    ]
    r = check_duplicate_order_risk(
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        broker="kis",
        recent_orders=[],
        reconcile_result=ReconcileResult(matched=["005930"], success=True),
        latest_snapshot_time=_now().isoformat(timespec="seconds"),
        now=_now(),
        open_partial_fills=partials,
    )
    assert r.blocked
    assert any(i.issue_type == "partial_fill_open" for i in r.issues)
    assert "partially-filled" in r.issues[0].message.lower() or "partial" in r.issues[0].message.lower()


def test_fully_filled_partial_status_does_not_block_via_open_list() -> None:
    partials = [
        PartialFillStatus(
            order_id="99",
            symbol="005930",
            ordered_quantity=10,
            filled_quantity=10,
            remaining_quantity=0,
            avg_fill_price=70000.0,
            fully_filled=True,
            partially_filled=False,
            unfilled=False,
        )
    ]
    r = check_duplicate_order_risk(
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        broker="kis",
        recent_orders=[],
        reconcile_result=ReconcileResult(matched=["005930"], success=True),
        latest_snapshot_time=_now().isoformat(timespec="seconds"),
        now=_now(),
        open_partial_fills=partials,
    )
    assert not any(i.issue_type == "partial_fill_open" for i in r.issues)


def test_partial_fill_from_db_blocks_guard_check(tmp_path) -> None:
    db = str(tmp_path / "pg.db")
    init_database(db)
    from deepsignal.storage.database import save_real_order_history

    save_real_order_history(
        db,
        broker="kis",
        symbol="005930",
        side="BUY",
        quantity=10,
        order_id="88",
        status="KIS_ORDER_SUBMITTED",
    )
    save_real_fill(
        db,
        broker="kis",
        symbol="005930",
        fill_quantity=2,
        order_id="88",
        fill_id="fill_a",
        fill_price=70000.0,
    )
    from deepsignal.live_trading.fill_tracker import load_open_partial_fill_statuses

    partials = load_open_partial_fill_statuses(db, broker="kis", symbol="005930")
    assert len(partials) >= 1
    r = check_duplicate_order_risk(
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        broker="kis",
        recent_orders=[],
        reconcile_result=ReconcileResult(matched=[], success=True),
        latest_snapshot_time=_now().isoformat(timespec="seconds"),
        now=_now(),
        open_partial_fills=partials,
    )
    assert r.blocked


def test_no_fills_safe() -> None:
    r = check_duplicate_order_risk(
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        broker="kis",
        recent_orders=[],
        reconcile_result=ReconcileResult(matched=["005930"], success=True),
        latest_snapshot_time=_now().isoformat(timespec="seconds"),
        now=_now(),
        open_partial_fills=[],
    )
    assert not r.blocked
