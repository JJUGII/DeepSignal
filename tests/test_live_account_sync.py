"""live_account_sync: 스냅샷 파일."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from deepsignal.live_trading.kis_broker import KISBroker
from deepsignal.live_trading.kis_config import KISConfig
from deepsignal.live_trading.live_account_sync import (
    build_account_snapshot_payload,
    summarize_kis_balance_raw,
    write_live_account_snapshot_paths,
)


def _cfg() -> KISConfig:
    return KISConfig(
        app_key="a",
        app_secret="b",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="paper",
    )


def test_build_snapshot_from_mock_balance_response() -> None:
    balance_body = {
        "rt_cd": "0",
        "output1": [
            {
                "pdno": "005930",
                "hldg_qty": "10",
                "pchs_avg_pric": "70000",
                "prpr": "71000",
                "evlu_amt": "710000",
            }
        ],
        "output2": [{"dnca_tot_amt": "1000000", "ord_psbl_cash": "900000"}],
    }

    class Resp:
        status_code = 200

        def json(self) -> dict:
            return balance_body

    session = MagicMock()

    class TokResp:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "tok", "expires_in": 600}

    def post_side_effect(url: str, **kwargs: object) -> TokResp | Resp:
        if "oauth2/tokenP" in url:
            return TokResp()
        raise AssertionError(url)

    session.post.side_effect = post_side_effect
    session.get.return_value = Resp()

    b = KISBroker(_cfg(), safe_mode=True, session=session)
    payload = build_account_snapshot_payload(b)
    assert payload["cash"]["cash"] == 1_000_000.0
    assert len(payload["positions"]) == 1
    assert payload["positions"][0]["symbol"] == "005930"
    assert payload["positions"][0]["quantity"] == 10


def test_write_snapshot_paths(tmp_path: Path) -> None:
    payload = {
        "timestamp": "t",
        "kis_env": "paper",
        "cash": {"cash": 1.0, "withdrawable_cash": 1.0, "raw": {}},
        "positions": [{"symbol": "005930", "quantity": 1, "avg_price": 1.0, "current_price": 2.0, "market_value": 2.0, "raw": {}}],
    }
    jp, mp = write_live_account_snapshot_paths(payload, output_dir=tmp_path)
    assert json.loads(jp.read_text(encoding="utf-8"))["kis_env"] == "paper"
    assert "005930" in mp.read_text(encoding="utf-8")


def test_summarize_kis_balance_raw_keys_only() -> None:
    raw = {
        "rt_cd": "0",
        "output1": [{"pdno": "005930", "hldg_qty": "1", "sensitive_value": "secret"}],
        "output2": [{"dnca_tot_amt": "100"}],
    }
    summary = summarize_kis_balance_raw(raw)
    assert summary["available"] is True
    assert summary["output1"]["row_count"] == 1
    assert "pdno" in summary["output1"]["keys"]
    assert "secret" not in json.dumps(summary)
