"""ops_dashboard: 실전 운영 상태 요약."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.ops_dashboard import (
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_RECONCILE_MISMATCH,
    STATUS_RISK_ALERT,
    STATUS_WARNING,
    build_ops_dashboard,
    write_ops_dashboard_report,
)
from deepsignal.storage.database import (
    init_database,
    save_real_account_snapshot,
    save_real_order_history,
    save_real_positions,
)


def _write_json(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_account(db: str) -> None:
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
                "raw": {"pdno": "005930"},
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


def _write_ok_reconcile(out: Path) -> None:
    _write_json(
        out / "reconcile_live_account_20260516_100000.json",
        {"timestamp": "t", "success": True, "matched": ["005930"], "missing_in_db": [], "missing_in_broker": [], "quantity_mismatch": []},
    )


def _write_risk(out: Path, status: str) -> None:
    _write_json(
        out / "risk_alert_20260516_100000.json",
        {
            "timestamp": "t",
            "status": status,
            "alerts": ["005930: loss warning"] if status != "OK" else [],
            "warnings": ["005930: review position"] if status == "WARNING" else [],
            "positions": [
                {
                    "symbol": "005930",
                    "quantity": 1,
                    "avg_price": 280000.0,
                    "current_price": 270500.0,
                    "market_value": 270500.0,
                    "unrealized_pnl": -9500.0,
                    "unrealized_pnl_pct": -0.0339285714,
                    "risk_level": status if status != "OK" else "OK",
                    "alerts": [],
                }
            ],
        },
    )


def test_no_data_status(tmp_path: Path) -> None:
    db = str(tmp_path / "ops.db")
    init_database(db)
    result = build_ops_dashboard(db, output_dir=tmp_path)
    assert result.status == STATUS_NO_DATA
    assert result.warnings


def test_reconcile_mismatch_status(tmp_path: Path) -> None:
    db = str(tmp_path / "ops.db")
    init_database(db)
    _seed_account(db)
    _write_json(
        tmp_path / "reconcile_live_account_20260516_100000.json",
        {"success": False, "matched": [], "missing_in_db": [{"symbol": "005930"}], "missing_in_broker": [], "quantity_mismatch": []},
    )
    _write_risk(tmp_path, "OK")
    result = build_ops_dashboard(db, output_dir=tmp_path)
    assert result.status == STATUS_RECONCILE_MISMATCH


def test_risk_warning_status(tmp_path: Path) -> None:
    db = str(tmp_path / "ops.db")
    init_database(db)
    _seed_account(db)
    _write_ok_reconcile(tmp_path)
    _write_risk(tmp_path, "WARNING")
    result = build_ops_dashboard(db, output_dir=tmp_path)
    assert result.status == STATUS_WARNING


def test_stop_loss_status_is_risk_alert(tmp_path: Path) -> None:
    db = str(tmp_path / "ops.db")
    init_database(db)
    _seed_account(db)
    _write_ok_reconcile(tmp_path)
    _write_risk(tmp_path, "STOP_LOSS_ALERT")
    result = build_ops_dashboard(db, output_dir=tmp_path)
    assert result.status == STATUS_RISK_ALERT


def test_normal_status_ok(tmp_path: Path) -> None:
    db = str(tmp_path / "ops.db")
    init_database(db)
    _seed_account(db)
    _write_ok_reconcile(tmp_path)
    _write_risk(tmp_path, "OK")
    result = build_ops_dashboard(db, output_dir=tmp_path)
    assert result.status == STATUS_OK


def test_markdown_and_json_generation(tmp_path: Path) -> None:
    db = str(tmp_path / "ops.db")
    init_database(db)
    _seed_account(db)
    _write_ok_reconcile(tmp_path)
    _write_risk(tmp_path, "WARNING")
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
    result = build_ops_dashboard(db, output_dir=tmp_path, recent_orders=5)
    jp, mp = write_ops_dashboard_report(result, output_dir=tmp_path)
    assert jp.is_file()
    assert mp.is_file()
    body = json.loads(jp.read_text(encoding="utf-8"))
    assert body["status"] == STATUS_WARNING
    text = mp.read_text(encoding="utf-8")
    assert "# DeepSignal Ops Dashboard" in text
    assert "005930" in text
    assert "O1" in text
    assert "does not place SELL" in text
