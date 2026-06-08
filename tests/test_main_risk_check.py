"""main.py risk-check CLI ([실전-12])."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import main as main_mod
from deepsignal.storage.database import init_database, save_real_positions


def test_risk_check_cli_stop_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "risk.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    now = datetime.now().isoformat(timespec="seconds")
    save_real_positions(
        db,
        now,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": 1,
                "avg_price": 70_000.0,
                "current_price": 64_000.0,
                "market_value": 64_000.0,
                "raw": {},
            }
        ],
    )
    with patch("deepsignal.live_trading.kis_broker.KISBroker.place_order") as mock_place:
        rc = main_mod.main(
            [
                "risk-check",
                "--broker",
                "kis",
                "--output-dir",
                str(tmp_path),
            ]
        )
    mock_place.assert_not_called()
    assert rc == 1
    reps = list(tmp_path.glob("risk_alert_*.json"))
    assert reps
    data = json.loads(sorted(reps)[-1].read_text(encoding="utf-8"))
    assert data["status"] == "STOP_LOSS_ALERT"
    assert (tmp_path / "RISK_ALERT.md").is_file()
    md = (tmp_path / "RISK_ALERT.md").read_text(encoding="utf-8")
    assert "STOP_LOSS_ALERT" in md
    assert "does not place SELL" in md


def test_risk_check_cli_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "ok.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    now = datetime.now().isoformat(timespec="seconds")
    save_real_positions(
        db,
        now,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": 1,
                "avg_price": 70_000.0,
                "current_price": 71_000.0,
                "market_value": 71_000.0,
                "raw": {},
            }
        ],
    )
    rc = main_mod.main(
        [
            "risk-check",
            "--broker",
            "kis",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("risk_alert_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["status"] == "OK"


def test_risk_check_no_positions_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "empty.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    rc = main_mod.main(
        [
            "risk-check",
            "--broker",
            "kis",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("risk_alert_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["status"] == "OK"
    assert any("no open" in w.lower() for w in data.get("warnings") or [])


def test_risk_check_cli_custom_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "custom.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    now = datetime.now().isoformat(timespec="seconds")
    save_real_positions(
        db,
        now,
        "kis",
        [
            {
                "symbol": "005930",
                "quantity": 1,
                "avg_price": 70_000.0,
                "current_price": 66_500.0,
                "market_value": 66_500.0,
                "raw": {},
            }
        ],
    )
    rc = main_mod.main(
        [
            "risk-check",
            "--broker",
            "kis",
            "--output-dir",
            str(tmp_path),
            "--stop-loss-pct",
            "-0.05",
            "--take-profit-pct",
            "0.12",
            "--warn-loss-pct",
            "-0.02",
            "--warn-profit-pct",
            "0.08",
        ]
    )
    assert rc == 1
    data = json.loads(sorted(tmp_path.glob("risk_alert_*.json"))[-1].read_text(encoding="utf-8"))
    assert data["status"] == "STOP_LOSS_ALERT"
    assert data["policy"] == {
        "stop_loss_pct": -0.05,
        "take_profit_pct": 0.12,
        "warn_loss_pct": -0.02,
        "warn_profit_pct": 0.08,
    }
