"""reconcile_real_account 단위 테스트."""

from __future__ import annotations

from deepsignal.live_trading.reconcile import reconcile_real_account


def test_reconcile_matched() -> None:
    b = [{"symbol": "005930", "quantity": 1}]
    d = [{"symbol": "005930", "quantity": 1}]
    r = reconcile_real_account(b, d)
    assert r.success
    assert r.matched == ["005930"]
    assert not r.missing_in_db
    assert not r.missing_in_broker
    assert not r.quantity_mismatch


def test_missing_in_db() -> None:
    b = [{"symbol": "005930", "quantity": 2}]
    d: list[dict] = []
    r = reconcile_real_account(b, d)
    assert not r.success
    assert r.missing_in_db
    assert r.missing_in_db[0].symbol == "005930"
    assert r.missing_in_db[0].broker_quantity == 2
    assert r.warnings


def test_missing_in_broker() -> None:
    b: list[dict] = []
    d = [{"symbol": "000660", "quantity": 1}]
    r = reconcile_real_account(b, d)
    assert not r.success
    assert r.missing_in_broker
    assert r.missing_in_broker[0].symbol == "000660"
    assert r.missing_in_broker[0].db_quantity == 1


def test_both_empty_success() -> None:
    r = reconcile_real_account([], [])
    assert r.success
    assert r.matched == []
    assert not r.missing_in_db
    assert not r.missing_in_broker
    assert not r.quantity_mismatch


def test_quantity_mismatch() -> None:
    b = [{"symbol": "035420", "quantity": 3}]
    d = [{"symbol": "035420", "quantity": 1}]
    r = reconcile_real_account(b, d)
    assert not r.success
    assert len(r.quantity_mismatch) == 1
    assert any("WARNING" in w for w in r.warnings)
