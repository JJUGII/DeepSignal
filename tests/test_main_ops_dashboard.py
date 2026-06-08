"""main.py ops-dashboard CLI smoke."""

from __future__ import annotations

import json
from pathlib import Path

import main as main_mod
from deepsignal.storage.database import (
    init_database,
    save_real_account_snapshot,
    save_real_order_history,
    save_real_positions,
)


def test_ops_dashboard_cli_smoke(monkeypatch, tmp_path: Path) -> None:
    db = str(tmp_path / "ops_cli.db")
    init_database(db)
    monkeypatch.setenv("DB_PATH", db)
    ts = "2026-05-16T10:00:00"
    save_real_positions(
        db,
        ts,
        "kis",
        [{"symbol": "005930", "quantity": 1, "avg_price": 280000.0, "current_price": 270500.0, "market_value": 270500.0}],
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
    save_real_order_history(
        db,
        broker="kis",
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=270500.0,
        status="FILLED",
        order_id="O1",
        created_at="2026-05-16T10:01:00",
    )
    (tmp_path / "reconcile_live_account_20260516_100000.json").write_text(
        json.dumps({"success": True, "matched": ["005930"], "missing_in_db": [], "missing_in_broker": [], "quantity_mismatch": []}),
        encoding="utf-8",
    )
    (tmp_path / "risk_alert_20260516_100000.json").write_text(
        json.dumps(
            {
                "status": "WARNING",
                "alerts": ["005930: loss warning"],
                "warnings": [],
                "positions": [{"symbol": "005930", "unrealized_pnl_pct": -0.0339, "risk_level": "WARNING"}],
            }
        ),
        encoding="utf-8",
    )

    rc = main_mod.main(["ops-dashboard", "--output-dir", str(tmp_path), "--recent-orders", "3"])

    assert rc == 0
    reports = list(tmp_path.glob("ops_dashboard_*.json"))
    assert len(reports) == 1
    body = json.loads(reports[0].read_text(encoding="utf-8"))
    assert body["status"] == "WARNING"
    assert body["recent_orders"][0]["order_id"] == "O1"
    md = tmp_path / "OPS_DASHBOARD.md"
    assert md.is_file()
    assert "005930" in md.read_text(encoding="utf-8")
