"""kis_order_status: 감사 로그 추출·리포트."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.kis_order_status import (
    extract_order_ids_from_audit,
    load_audit_log,
    summarize_order_results,
    write_order_status_report,
)


def test_extract_odno_from_output() -> None:
    audit = {
        "results": [
            {
                "raw": {
                    "response_body": {"rt_cd": "0", "output": {"ODNO": "1234567890"}},
                }
            }
        ]
    }
    ids = extract_order_ids_from_audit(audit)
    assert "1234567890" in ids


def test_extract_graceful_when_no_ids() -> None:
    audit = {"results": [{"raw": {"response_body": {"msg1": "fail"}}}]}
    assert extract_order_ids_from_audit(audit) == []


def test_write_order_status_report_creates_json_and_md(tmp_path: Path) -> None:
    audit = {
        "status": "KIS_LIVE_ORDER_FAILED",
        "results": [{"broker_order_id": None, "symbol": "005930", "status": "X", "message": "m"}],
    }
    p = tmp_path / "a.json"
    p.write_text(json.dumps(audit), encoding="utf-8")
    loaded = load_audit_log(p)
    ids = extract_order_ids_from_audit(loaded)
    jp, mp = write_order_status_report(
        audit_path=p,
        audit=loaded,
        extracted_order_ids=ids,
        kis_statuses=None,
        output_dir=tmp_path,
    )
    assert jp.exists() and mp.exists()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data.get("audit_status") == "KIS_LIVE_ORDER_FAILED"
    assert "audit_results_summary" in data
    assert summarize_order_results(loaded)


def test_summarize_broker_order_id_from_nested_output() -> None:
    audit = {
        "results": [
            {
                "symbol": "005930",
                "status": "KIS_ORDER_SUBMITTED",
                "message": "ok",
                "raw": {"response_body": {"output": {"ODNO": "999"}}},
            }
        ]
    }
    s = summarize_order_results(audit)
    assert s[0].get("broker_order_id") == "999"


def test_extract_from_output1_list_in_response() -> None:
    audit = {
        "results": [
            {
                "raw": {
                    "response_body": {
                        "rt_cd": "0",
                        "output1": [{"odno": "777", "pdno": "005930"}],
                    }
                }
            }
        ]
    }
    assert "777" in extract_order_ids_from_audit(audit)
