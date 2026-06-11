"""fetch_latest_signals 테스트."""

from __future__ import annotations

import sqlite3

from deepsignal.storage.database import fetch_latest_signals, init_database


def test_fetch_latest_signals_one_per_symbol(tmp_path) -> None:
    db = tmp_path / "sig.db"
    init_database(str(db))
    sql = """
    INSERT INTO signals (
        symbol, signal_date, strategy_name, technical_score, news_score, macro_score,
        final_score, action, confidence, reason, raw_json
    ) VALUES (?, ?, 'technical_v1', 50, NULL, NULL, ?, 'BUY_CANDIDATE', 0.5, '', '{}')
    """
    with sqlite3.connect(str(db)) as conn:
        conn.execute(sql, ("AA", "2026-01-01", 60.0))
        conn.execute(sql, ("AA", "2026-01-10", 70.0))
        conn.execute(sql, ("BB", "2026-01-05", 55.0))
        conn.commit()
    rows = fetch_latest_signals(str(db), limit=100)
    by = {r["symbol"]: r for r in rows}
    assert set(by) == {"AA", "BB"}
    assert float(by["AA"]["final_score"]) == 70.0


def test_fetch_latest_signals_can_include_k_gsqs_strategy(tmp_path) -> None:
    db = tmp_path / "sig.db"
    init_database(str(db))
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            """
            INSERT INTO signals (
                symbol, signal_date, strategy_name, technical_score, news_score, macro_score,
                final_score, action, confidence, reason, raw_json
            ) VALUES ('005930', '2026-06-10', 'k_gsqs_v1', 80, NULL, NULL, 80,
                      'BUY_CANDIDATE', 0.8, '', '{}')
            """
        )
        conn.commit()

    default_rows = fetch_latest_signals(str(db), limit=100)
    live_rows = fetch_latest_signals(
        str(db),
        limit=100,
        strategy_names=("technical_v1", "k_gsqs_v1"),
    )

    assert default_rows == []
    assert live_rows[0]["symbol"] == "005930"
    assert live_rows[0]["strategy_name"] == "k_gsqs_v1"
