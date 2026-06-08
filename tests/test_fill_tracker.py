"""fill_tracker: 체결 추출·집계·dedupe."""

from __future__ import annotations

from deepsignal.live_trading.fill_tracker import (
    FillRecord,
    aggregate_order_fills,
    build_partial_fill_status,
    extract_fills_from_kis_order_status,
    persist_fill_records_to_db,
    synthetic_fill_id,
)
from deepsignal.storage.database import (
    init_database,
    load_real_fills_by_order,
    save_real_fill,
)


def test_single_fill_from_output2() -> None:
    raw = {
        "response_body": {
            "rt_cd": "0",
            "output1": [{"odno": "1", "pdno": "005930", "ord_qty": "10", "tot_ccld_qty": "3"}],
            "output2": [
                {"odno": "1", "pdno": "005930", "ccld_qty": "2", "ccld_unpr": "70000", "ord_dt": "20260515", "ord_tmd": "100000"},
                {"odno": "1", "pdno": "005930", "ccld_qty": "1", "ccld_unpr": "70500", "ord_dt": "20260515", "ord_tmd": "100100"},
            ],
        }
    }
    fills = extract_fills_from_kis_order_status(raw, default_order_id="1")
    assert len(fills) == 2
    assert sum(f.fill_quantity for f in fills) == 3


def test_multi_fill_avg_price() -> None:
    fills = [
        FillRecord("kis", "005930", "BUY", "9", "f1", 2, 70000.0, 140000.0, None, {}),
        FillRecord("kis", "005930", "BUY", "9", "f2", 1, 71000.0, 71000.0, None, {}),
    ]
    agg = aggregate_order_fills(fills, order_quantity=10, order_id="9", symbol="005930")
    assert agg["filled_quantity"] == 3
    assert agg["remaining_quantity"] == 7
    assert agg["fill_count"] == 2
    assert abs(float(agg["avg_fill_price"]) - 70333.33333333333) < 0.01
    assert agg["fully_filled"] is False
    assert agg["partially_filled"] is True


def test_partial_fill_status() -> None:
    agg = {
        "order_quantity": 10,
        "filled_quantity": 3,
        "remaining_quantity": 7,
        "avg_fill_price": 70250.0,
        "fill_count": 2,
        "fully_filled": False,
        "partially_filled": True,
    }
    pfs = build_partial_fill_status(agg, order_id="12345", symbol="005930")
    assert pfs.partially_filled
    assert not pfs.fully_filled
    assert pfs.remaining_quantity == 7


def test_dedupe_save_real_fill(tmp_path) -> None:
    db = str(tmp_path / "f.db")
    init_database(db)
    fid = synthetic_fill_id(order_id="1", fill_timestamp="2026-05-15T10:00:00", fill_quantity=1, fill_price=70000.0)
    n1 = save_real_fill(
        db,
        broker="kis",
        symbol="005930",
        fill_quantity=1,
        order_id="1",
        fill_id=fid,
        fill_price=70000.0,
        fill_timestamp="2026-05-15T10:00:00",
    )
    n2 = save_real_fill(
        db,
        broker="kis",
        symbol="005930",
        fill_quantity=1,
        order_id="1",
        fill_id=fid,
        fill_price=70000.0,
        fill_timestamp="2026-05-15T10:00:00",
    )
    assert n1 > 0
    assert n2 == 0
    rows = load_real_fills_by_order(db, broker="kis", order_id="1")
    assert len(rows) == 1


def test_persist_fill_records_dedupe(tmp_path) -> None:
    db = str(tmp_path / "f2.db")
    init_database(db)
    rec = FillRecord(
        broker="kis",
        symbol="005930",
        side="BUY",
        order_id="77",
        fill_id="x1",
        fill_quantity=5,
        fill_price=100.0,
        fill_value=500.0,
        fill_timestamp="2026-05-15T11:00:00",
        raw={},
    )
    ins, sk = persist_fill_records_to_db(db, [rec, rec])
    assert ins == 1
    assert sk == 1
