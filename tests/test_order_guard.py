"""order_guard.check_duplicate_order_risk 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

from deepsignal.live_trading.order_guard import check_duplicate_order_risk
from deepsignal.live_trading.reconcile import ReconcileIssue, ReconcileResult


def _now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0)


def test_recent_duplicate_buy_blocks() -> None:
    recent = [
        {
            "symbol": "005930",
            "side": "BUY",
            "quantity": 1,
            "limit_price": 70000.0,
            "status": "KIS_ORDER_SUBMITTED",
            "created_at": (_now() - timedelta(minutes=5)).isoformat(timespec="seconds"),
            "raw": {},
        }
    ]
    r = check_duplicate_order_risk(
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        broker="kis",
        recent_orders=recent,
        reconcile_result=ReconcileResult(success=True, matched=["005930"]),
        latest_snapshot_time=_now().isoformat(timespec="seconds"),
        now=_now(),
    )
    assert r.blocked
    assert any(i.issue_type == "recent_duplicate_buy" for i in r.issues)


def test_stale_snapshot_blocks() -> None:
    old = (_now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    r = check_duplicate_order_risk(
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        broker="kis",
        recent_orders=[],
        reconcile_result=ReconcileResult(matched=[], success=True),
        latest_snapshot_time=old,
        now=_now(),
        stale_snapshot_minutes=10,
    )
    assert r.blocked
    assert any(i.issue_type == "stale_snapshot" for i in r.issues)


def test_reconcile_mismatch_blocks() -> None:
    rec = ReconcileResult(
        matched=[],
        success=False,
        quantity_mismatch=[
            ReconcileIssue(
                symbol="005930",
                issue_type="quantity_mismatch",
                broker_quantity=3,
                db_quantity=1,
                message="mismatch",
            )
        ],
    )
    r = check_duplicate_order_risk(
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        broker="kis",
        recent_orders=[],
        reconcile_result=rec,
        latest_snapshot_time=_now().isoformat(timespec="seconds"),
        now=_now(),
    )
    assert r.blocked
    assert any(i.issue_type == "reconcile_mismatch" for i in r.issues)


def test_partial_fill_blocks() -> None:
    recent = [
        {
            "symbol": "005930",
            "side": "BUY",
            "quantity": 10,
            "limit_price": 70000.0,
            "status": "PARTIAL",
            "created_at": (_now() - timedelta(hours=2)).isoformat(timespec="seconds"),
            "raw": {"filled_quantity": 3, "remaining_quantity": 7},
        }
    ]
    r = check_duplicate_order_risk(
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        broker="kis",
        recent_orders=recent,
        reconcile_result=ReconcileResult(matched=[], success=True),
        latest_snapshot_time=_now().isoformat(timespec="seconds"),
        now=_now(),
    )
    assert r.blocked
    assert any(i.issue_type == "partial_fill_pending" for i in r.issues)


def test_no_issue_safe() -> None:
    r = check_duplicate_order_risk(
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70000.0,
        broker="kis",
        recent_orders=[],
        reconcile_result=ReconcileResult(success=True, matched=["005930"]),
        latest_snapshot_time=_now().isoformat(timespec="seconds"),
        now=_now(),
    )
    assert not r.blocked
    assert not r.issues
