"""crypto_trades — entry/exit feedback DB."""

from __future__ import annotations

import json
import sqlite3

from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
from deepsignal.crypto_trading.crypto_trades import (
    compute_actual_return_net,
    features_snapshot_json,
    init_crypto_trades_db,
    load_closed_trades,
    record_crypto_trade_entry,
    record_crypto_trade_exit,
)
from deepsignal.market_data.feature_engine.spec import FEATURE_COUNT


def test_features_snapshot_json_list() -> None:
    vec = [0.1] * FEATURE_COUNT
    raw = features_snapshot_json(vec)
    assert raw is not None
    assert len(json.loads(raw)) == FEATURE_COUNT


def test_trade_round_trip(tmp_path) -> None:
    db = init_crypto_trades_db(tmp_path)
    plan_buy = CryptoOrderPlan(
        market="KRW-BTC",
        side="buy",
        limit_price=100.0,
        score_breakdown={
            "features_snapshot": [0.0] * FEATURE_COUNT,
            "ml_ensemble": {"lgbm_p": 0.62, "seq_p": 0.58, "blended_p": 0.60},
        },
        quality_gates={"gate_mode": "ml_primary"},
        created_at="2026-05-26T10:00:00+09:00",
    )
    tid = record_crypto_trade_entry(
        plan_buy,
        fill_price=100.0,
        fill_volume=0.01,
        trades_db=tmp_path,
    )
    assert tid is not None

    plan_sell = CryptoOrderPlan(
        market="KRW-BTC",
        side="sell",
        limit_price=102.0,
        sell_trigger="take_profit",
        avg_buy_price=100.0,
    )
    out = record_crypto_trade_exit(
        plan_sell,
        fill_price=102.0,
        fill_volume=0.01,
        fee=0.05,
        trades_db=tmp_path,
        exit_time="2026-05-26T10:05:00+09:00",
        fill_complete=True,
    )
    assert out["updated"] is True

    closed = load_closed_trades(tmp_path, lookback_days=30)
    assert len(closed) == 1
    assert closed[0].exit_reason == "tp"
    assert closed[0].actual_return is not None
    assert closed[0].lgbm_prob == 0.62


def test_compute_actual_return_net() -> None:
    r = compute_actual_return_net(
        entry_price=100.0,
        exit_price=102.0,
        entry_fee=0.0,
        exit_fee=0.1,
        position_size=1.0,
    )
    assert r is not None
    assert 0.01 < r < 0.03


def test_schema_columns(tmp_path) -> None:
    db = init_crypto_trades_db(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(crypto_trades)")}
    assert "features_snapshot" in cols
    assert "gate_mode" in cols
    assert "paper" in cols
