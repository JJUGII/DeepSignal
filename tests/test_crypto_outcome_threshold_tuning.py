"""crypto_outcome_threshold_tuning — outcome 기반 임계값 튜닝."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import (
    ACTIVE_THRESHOLDS_JSON,
    load_active_crypto_thresholds,
    run_tune_crypto_thresholds_from_outcomes,
    tune_crypto_thresholds_from_outcomes,
)
from deepsignal.crypto_trading.crypto_recommendation_outcomes import init_crypto_outcomes_db


def _seed_outcomes(db: Path) -> None:
    init_crypto_outcomes_db(db)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            INSERT INTO crypto_recommendation_outcomes (
                run_id, created_at, market, display_name, side, reason,
                current_price, avg_buy_price, pnl_pct, order_uuid, executed,
                fill_price, fill_volume, fee, realized_pnl_pct, exit_reason, closed_at,
                max_profit_pct, max_loss_pct
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "r1",
                "2026-05-24T10:00:00+09:00",
                "KRW-XRP",
                "리플",
                "sell",
                "tp",
                2050.0,
                2000.0,
                2.5,
                "u1",
                1,
                2050.0,
                10.0,
                1.0,
                2.8,
                "take_profit",
                "2026-05-24T11:00:00+09:00",
                None,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO crypto_recommendation_outcomes (
                run_id, created_at, market, display_name, side, reason,
                current_price, avg_buy_price, pnl_pct, order_uuid, executed,
                fill_price, fill_volume, fee, realized_pnl_pct, exit_reason, closed_at,
                max_profit_pct, max_loss_pct
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "r2",
                "2026-05-23T10:00:00+09:00",
                "KRW-BTC",
                "BTC",
                "sell",
                "sl",
                90_000_000.0,
                92_000_000.0,
                -2.0,
                "u2",
                1,
                90_000_000.0,
                0.001,
                100.0,
                -2.2,
                "stop_loss",
                "2026-05-23T12:00:00+09:00",
                None,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO crypto_recommendation_outcomes (
                run_id, created_at, market, display_name, side, reason,
                current_price, avg_buy_price, pnl_pct, order_uuid, executed,
                fill_price, fill_volume, fee, realized_pnl_pct, exit_reason, closed_at,
                max_profit_pct, max_loss_pct
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "r3",
                "2026-05-22T10:00:00+09:00",
                "KRW-ETH",
                "ETH",
                "buy",
                "momentum",
                3_500_000.0,
                0.0,
                -1.0,
                "u3",
                1,
                3_500_000.0,
                0.01,
                50.0,
                None,
                None,
                None,
                -0.5,
                -1.5,
            ),
        )
        conn.commit()


def test_tune_from_sell_outcomes(tmp_path: Path) -> None:
    db = tmp_path / "crypto_recommendation_outcomes.db"
    _seed_outcomes(db)
    tuned = tune_crypto_thresholds_from_outcomes(db, lookback_days=30, min_sell_samples=2, min_buy_samples=1)
    assert tuned.sample_sell_closed >= 2
    assert 1.0 <= tuned.take_profit_pct <= 4.0
    assert -3.0 <= tuned.stop_loss_pct <= -0.8


def test_write_active_thresholds(tmp_path: Path) -> None:
    db = tmp_path / "crypto_recommendation_outcomes.db"
    _seed_outcomes(db)
    run_tune_crypto_thresholds_from_outcomes(db, output_dir=tmp_path)
    active = tmp_path / ACTIVE_THRESHOLDS_JSON
    assert active.is_file()
    loaded = load_active_crypto_thresholds(tmp_path)
    assert loaded is not None
    data = json.loads(active.read_text(encoding="utf-8"))
    assert "min_volume_ratio" in data
