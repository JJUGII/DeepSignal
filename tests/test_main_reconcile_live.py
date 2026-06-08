"""main.py reconcile-live-account · DB 연동."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import main as main_mod
from deepsignal.storage.database import init_database, save_real_positions


def test_reconcile_live_account_mock_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "r.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    save_real_positions(
        db,
        "2026-05-15T10:00:00",
        "kis",
        [{"symbol": "005930", "quantity": 1, "avg_price": 1.0, "current_price": 1.0, "market_value": 1.0, "raw": {}}],
    )

    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")

    class Tok:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "t", "expires_in": 600}

    class BalResp:
        status_code = 200

        def json(self) -> dict:
            return {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "3", "pchs_avg_pric": "1", "prpr": "2", "evlu_amt": "6"}],
                "output2": [{"dnca_tot_amt": "100", "ord_psbl_cash": "50"}],
            }

    session = MagicMock()

    def get_side_effect(url: str, **kwargs: object) -> BalResp:
        if "inquire-balance" in url:
            return BalResp()
        raise AssertionError(url)

    def post_side_effect(url: str, **kwargs: object) -> Tok:
        if "oauth2/tokenP" in url:
            return Tok()
        raise AssertionError(url)

    session.get.side_effect = get_side_effect
    session.post.side_effect = post_side_effect

    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=session):
        rc = main_mod.main(
            [
                "reconcile-live-account",
                "--broker",
                "kis",
                "--network",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert rc == 1
    reps = list(tmp_path.glob("reconcile_live_account_*.json"))
    assert reps
    data = json.loads(sorted(reps)[-1].read_text(encoding="utf-8"))
    assert data.get("success") is False
    assert data.get("quantity_mismatch")


def test_reconcile_live_account_missing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "e.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO", "KIS_ACCOUNT_PRODUCT_CODE"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("KIS_ENV", "paper")
    rc = main_mod.main(
        [
            "reconcile-live-account",
            "--broker",
            "kis",
            "--network",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 1


def test_live_sync_saves_db_when_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "s.db")
    monkeypatch.setenv("DB_PATH", db)
    init_database(db)
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")

    class Tok:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "t", "expires_in": 600}

    class BalResp:
        status_code = 200

        def json(self) -> dict:
            return {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "1", "pchs_avg_pric": "1", "prpr": "1", "evlu_amt": "1"}],
                "output2": [{"dnca_tot_amt": "10", "ord_psbl_cash": "9"}],
            }

    session = MagicMock()
    session.get.side_effect = lambda url, **kw: BalResp() if "inquire-balance" in url else MagicMock()
    session.post.side_effect = lambda url, **kw: Tok() if "oauth2/tokenP" in url else MagicMock()

    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=session):
        rc = main_mod.main(
            [
                "live-sync-account",
                "--broker",
                "kis",
                "--network",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert rc == 0
    from deepsignal.storage.database import load_latest_real_positions

    assert len(load_latest_real_positions(db, broker="kis")) == 1
