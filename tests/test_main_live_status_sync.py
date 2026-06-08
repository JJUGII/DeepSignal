"""main.py live-order-status / live-sync-account."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import main as main_mod
from deepsignal.storage.database import init_database


def test_live_order_status_no_network_smoke(tmp_path: Path) -> None:
    audit = {
        "status": "KIS_ORDER_SUBMITTED",
        "results": [{"raw": {"response_body": {"output": {"ODNO": "111"}}}}],
    }
    ap = tmp_path / "live_approval_audit_x.json"
    ap.write_text(json.dumps(audit), encoding="utf-8")
    rc = main_mod.main(
        [
            "live-order-status",
            "--audit",
            str(ap),
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    reports = list(tmp_path.glob("live_order_status_*.json"))
    assert len(reports) == 1
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    assert "111" in data.get("extracted_order_ids", [])


def test_live_order_status_network_mock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")
    audit = {"status": "OK", "results": [{"raw": {"response_body": {"output": {"ODNO": "222"}}}}]}
    ap = tmp_path / "a.json"
    ap.write_text(json.dumps(audit), encoding="utf-8")

    class Tok:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "t", "expires_in": 600}

    class OrdResp:
        status_code = 200

        def json(self) -> dict:
            return {
                "rt_cd": "0",
                "output1": [
                    {
                        "odno": "222",
                        "pdno": "005930",
                        "ord_qty": "1",
                        "tot_ccld_qty": "1",
                        "rmnd_qty": "0",
                        "ord_unpr": "70000",
                        "avg_prvs": "70000",
                        "sll_buy_dvsn_cd": "02",
                    }
                ],
            }

    session = MagicMock()

    def get_side_effect(url: str, **kwargs: object) -> OrdResp:
        if "inquire-daily-ccld" in url:
            return OrdResp()
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
                "live-order-status",
                "--audit",
                str(ap),
                "--network",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert rc == 0
    data = json.loads(sorted(tmp_path.glob("live_order_status_*.json"))[-1].read_text(encoding="utf-8"))
    assert data.get("kis_query")


def test_live_sync_account_missing_env_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO", "KIS_ACCOUNT_PRODUCT_CODE"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("KIS_ENV", "paper")
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
    assert rc == 1


def test_live_sync_account_network_mock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = str(tmp_path / "sync.db")
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
                "output1": [{"pdno": "005930", "hldg_qty": "2", "pchs_avg_pric": "1", "prpr": "2", "evlu_amt": "4"}],
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
                "live-sync-account",
                "--broker",
                "kis",
                "--network",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert rc == 0
    snaps = list(tmp_path.glob("live_account_snapshot_*.json"))
    assert len(snaps) == 1
    body = json.loads(snaps[0].read_text(encoding="utf-8"))
    assert body["positions"][0]["symbol"] == "005930"


def test_live_sync_account_debug_raw_writes_shape_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = str(tmp_path / "sync_debug.db")
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
                "output1": [{"pdno": "005930", "hldg_qty": "0", "prdt_name": "Samsung"}],
                "output2": [{"dnca_tot_amt": "100", "account_name": "secret-name"}],
            }

    session = MagicMock()
    session.get.return_value = BalResp()
    session.post.side_effect = lambda url, **kw: Tok() if "oauth2/tokenP" in url else MagicMock()

    with patch("deepsignal.live_trading.kis_broker.requests.Session", return_value=session):
        rc = main_mod.main(
            [
                "live-sync-account",
                "--broker",
                "kis",
                "--network",
                "--debug-raw",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert rc == 0
    debug_files = list(tmp_path.glob("kis_debug_account_*.json"))
    assert len(debug_files) == 1
    text = debug_files[0].read_text(encoding="utf-8")
    assert "secret-name" not in text
    data = json.loads(text)
    assert data["summary"]["output1"]["row_count"] == 1
    assert "hldg_qty" in data["summary"]["output1"]["keys"]


def test_live_sync_account_requires_network(tmp_path: Path) -> None:
    rc = main_mod.main(
        [
            "live-sync-account",
            "--broker",
            "kis",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 1
