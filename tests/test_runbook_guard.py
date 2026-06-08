"""runbook_guard.py — pre-trade runbook 검증 ([실전-11])."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from deepsignal.live_trading.runbook_guard import (
    find_latest_pre_trade_runbook,
    validate_pre_trade_runbook,
)


def _write_report(
    tmp_path: Path,
    *,
    final_status: str = "PRE_TRADE_READY",
    finished_at: str | None = None,
    summary: dict | None = None,
    name: str = "pre_trade_runbook_20260515_120000.json",
) -> Path:
    p = tmp_path / name
    body = {
        "mode": "pre_trade",
        "success": final_status == "PRE_TRADE_READY",
        "final_status": final_status,
        "started_at": finished_at or datetime.now().isoformat(timespec="seconds"),
        "finished_at": finished_at or datetime.now().isoformat(timespec="seconds"),
        "summary": summary or {},
        "steps": [],
    }
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def _ok_summary(tmp_path: Path) -> dict:
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    return {
        "plan_path": plan.resolve().as_posix(),
        "symbol": "005930",
        "quantity": 1,
        "limit_price": 70000.0,
    }


def test_find_latest_pre_trade_runbook(tmp_path: Path) -> None:
    old = _write_report(tmp_path, name="pre_trade_runbook_20260515_100000.json")
    new = _write_report(tmp_path, name="pre_trade_runbook_20260515_120000.json")
    import os
    import time

    os.utime(old, (time.time() - 100, time.time() - 100))
    os.utime(new, (time.time(), time.time()))
    found = find_latest_pre_trade_runbook(tmp_path)
    assert found is not None
    assert found.name == new.name


def test_pre_trade_ready_passes(tmp_path: Path) -> None:
    summ = _ok_summary(tmp_path)
    p = _write_report(tmp_path, summary=summ)
    r = validate_pre_trade_runbook(
        report_path=p,
        expected_plan_path=summ["plan_path"],
        expected_symbol="005930",
        expected_quantity=1,
        expected_limit_price=70000.0,
    )
    assert r.passed is True
    assert r.status == "RUNBOOK_OK"


def test_not_found_fails(tmp_path: Path) -> None:
    r = validate_pre_trade_runbook(output_dir=tmp_path)
    assert r.passed is False
    assert r.status == "RUNBOOK_NOT_FOUND"


def test_not_ready_fails(tmp_path: Path) -> None:
    p = _write_report(tmp_path, final_status="PRE_TRADE_BLOCKED", summary=_ok_summary(tmp_path))
    r = validate_pre_trade_runbook(report_path=p)
    assert r.passed is False
    assert r.status == "RUNBOOK_NOT_READY"


def test_expired_fails(tmp_path: Path) -> None:
    old = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    p = _write_report(tmp_path, finished_at=old, summary=_ok_summary(tmp_path))
    r = validate_pre_trade_runbook(report_path=p, max_age_minutes=10)
    assert r.passed is False
    assert r.status == "RUNBOOK_EXPIRED"


def test_symbol_mismatch(tmp_path: Path) -> None:
    summ = _ok_summary(tmp_path)
    summ["symbol"] = "000660"
    p = _write_report(tmp_path, summary=summ)
    r = validate_pre_trade_runbook(report_path=p, expected_symbol="005930")
    assert r.passed is False
    assert r.status == "RUNBOOK_MISMATCH"


def test_quantity_mismatch(tmp_path: Path) -> None:
    summ = _ok_summary(tmp_path)
    summ["quantity"] = 2
    p = _write_report(tmp_path, summary=summ)
    r = validate_pre_trade_runbook(report_path=p, expected_quantity=1)
    assert r.passed is False
    assert r.status == "RUNBOOK_MISMATCH"


def test_limit_price_mismatch(tmp_path: Path) -> None:
    summ = _ok_summary(tmp_path)
    summ["limit_price"] = 71000.0
    p = _write_report(tmp_path, summary=summ)
    r = validate_pre_trade_runbook(report_path=p, expected_limit_price=70000.0)
    assert r.passed is False
    assert r.status == "RUNBOOK_MISMATCH"


def test_plan_path_mismatch(tmp_path: Path) -> None:
    summ = _ok_summary(tmp_path)
    p = _write_report(tmp_path, summary=summ)
    other = tmp_path / "other_plan.json"
    other.write_text("{}", encoding="utf-8")
    r = validate_pre_trade_runbook(report_path=p, expected_plan_path=str(other))
    assert r.passed is False
    assert r.status == "RUNBOOK_MISMATCH"
