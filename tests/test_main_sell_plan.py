"""main.py sell-plan CLI smoke."""

from __future__ import annotations

import json
from pathlib import Path

import main as main_mod
from deepsignal.storage.database import init_database, save_real_account_snapshot, save_real_positions


def test_sell_plan_cli_smoke(monkeypatch, tmp_path: Path) -> None:
    db = str(tmp_path / "sell_cli.db")
    init_database(db)
    monkeypatch.setenv("DB_PATH", db)
    ts = "2026-05-16T10:00:00"
    save_real_positions(
        db,
        ts,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": 1,
                "avg_price": 280000.0,
                "current_price": 270500.0,
                "market_value": 270500.0,
            }
        ],
    )
    save_real_account_snapshot(
        db,
        ts,
        "kis",
        cash=500000.0,
        withdrawable_cash=500000.0,
        total_market_value=270500.0,
        total_equity=770500.0,
        raw_payload={"timestamp": ts},
    )

    rc = main_mod.main(["sell-plan", "--output-dir", str(tmp_path)])

    assert rc == 0
    reports = list(tmp_path.glob("sell_plan_*.json"))
    assert len(reports) == 1
    body = json.loads(reports[0].read_text(encoding="utf-8"))
    assert body["status"] == "REVIEW"
    assert body["items"][0]["symbol"] == "005930"
    md = tmp_path / "SELL_PLAN.md"
    assert md.is_file()
    assert "This plan does NOT place SELL orders" in md.read_text(encoding="utf-8")
