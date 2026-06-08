"""retrain_history.jsonl append and table formatting."""

from __future__ import annotations

from deepsignal.ml.crypto_retrain_history import (
    RetrainHistoryEntry,
    append_retrain_history,
    format_retrain_history_table,
    load_retrain_history,
)


def test_append_and_load(tmp_path) -> None:
    append_retrain_history(
        tmp_path,
        RetrainHistoryEntry(
            date="2026-05-26T03:10:00+09:00",
            val_auc=0.61,
            val_sharpe=0.8,
            train_sharpe=1.2,
            deployed=True,
            n_trades_used=47,
            model_version="v12",
        ),
    )
    rows = load_retrain_history(tmp_path, last_days=30)
    assert len(rows) == 1
    assert rows[0]["model_version"] == "v12"
    table = format_retrain_history_table(rows)
    assert "v12" in table
    assert "0.610" in table
