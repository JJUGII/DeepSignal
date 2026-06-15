from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepsignal.crypto_trading.broker.bithumb.broker import BithumbBroker
from deepsignal.crypto_trading.broker.bithumb.config import BithumbConfig
from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
from deepsignal.crypto_trading.crypto_recommendation_outcomes import (
    init_crypto_outcomes_db,
    record_crypto_recommendation,
)
from deepsignal.crypto_trading.crypto_trade_history import (
    done_order_to_trade_item,
    fetch_done_orders_from_broker,
    normalize_order_side_to_trade,
    order_id_from_row,
    trades_from_local_audits,
)


def test_normalize_order_side_to_trade() -> None:
    assert normalize_order_side_to_trade("bid") == "buy"
    assert normalize_order_side_to_trade("ask") == "sell"


def test_done_order_to_trade_item_bithumb_order_id() -> None:
    row = {
        "order_id": "C20240001",
        "market": "KRW-BTC",
        "side": "bid",
        "created_at": "2026-06-14T10:00:00+09:00",
        "avg_price": "90000000",
        "executed_volume": "0.001",
        "paid_fee": "45",
        "trades_price": "90000",
    }
    item = done_order_to_trade_item(row, broker_id="bithumb", source="bithumb_api")
    assert item is not None
    assert item["order_id"] == "C20240001"
    assert item["broker"] == "bithumb"
    assert item["side"] == "buy"
    assert item["symbol"] == "BTC"


def test_fetch_done_orders_from_bithumb_demo() -> None:
    br = BithumbBroker(BithumbConfig(api_key="demo-key", secret_key="demo-secret", dry_run=True))

    def fake_request(method, path, **kwargs):
        assert method == "GET"
        assert path.endswith("/orders")
        return [
            {
                "uuid": "demo-1",
                "market": "KRW-BTC",
                "side": "bid",
                "created_at": "2026-06-14T10:00:00+09:00",
                "price": "90000000",
                "executed_volume": "0.001",
                "paid_fee": "45",
            }
        ]

    br._request = fake_request  # type: ignore[method-assign]
    rows = fetch_done_orders_from_broker(br)
    assert len(rows) == 1
    assert rows[0]["broker"] == "bithumb"


def test_trades_from_local_audits_includes_broker(tmp_path: Path) -> None:
    audit = tmp_path / "crypto_telegram_approval_audit_1.json"
    audit.write_text(
        json.dumps(
            {
                "executed": True,
                "status": "APPROVED",
                "plan": {
                    "market": "KRW-ETH",
                    "side": "buy",
                    "limit_price": 3000000,
                    "krw_amount": 10000,
                    "created_at": "2026-06-14T12:00:00+09:00",
                    "broker": "bithumb",
                },
                "result": {"uuid": "oid-99"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    items = trades_from_local_audits(
        [str(audit)],
        slip_map={},
        start_dt="2026-06-01",
        end_dt="2026-06-30",
        broker_id="bithumb",
    )
    assert len(items) == 1
    assert items[0]["broker"] == "bithumb"
    assert items[0]["order_id"] == "oid-99"


def test_record_crypto_recommendation_stores_broker(tmp_path: Path) -> None:
    db = init_crypto_outcomes_db(tmp_path / "crypto_recommendation_outcomes.db")
    plan = CryptoOrderPlan(
        broker="bithumb",
        market="KRW-BTC",
        display_name="비트코인",
        side="buy",
        krw_amount=10_000,
        limit_price=90_000_000,
        reason="test",
        created_at="2026-06-14T12:00:00+09:00",
    )
    record_crypto_recommendation(plan, outcomes_db=db)
    import sqlite3

    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT broker FROM crypto_recommendation_outcomes ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0] == "bithumb"
