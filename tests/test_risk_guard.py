"""risk_guard.py — 손절/익절 경고 ([실전-12])."""

from __future__ import annotations

from pathlib import Path
from argparse import Namespace

import pytest

from deepsignal.live_trading.risk_guard import (
    RiskGuardPolicy,
    count_risk_levels,
    evaluate_portfolio_risk,
    evaluate_position_risk,
    is_risk_alert_status,
    risk_policy_from_namespace,
    summarize_risk_result,
    write_risk_report,
)


def _pos(
    symbol: str = "005930",
    qty: int = 1,
    avg: float = 70_000.0,
    cur: float = 70_000.0,
) -> dict:
    return {
        "symbol": symbol,
        "quantity": qty,
        "avg_price": avg,
        "current_price": cur,
        "market_value": cur * qty,
    }


def test_ok_position() -> None:
    r = evaluate_position_risk(_pos(cur=72_000.0))
    assert r.risk_level == "OK"
    assert r.unrealized_pnl == 2_000.0
    assert r.unrealized_pnl_pct is not None
    assert abs(r.unrealized_pnl_pct - (2_000.0 / 70_000.0)) < 1e-9


def test_warn_loss() -> None:
    # -4% between warn -3% and stop -7%
    cur = 70_000.0 * 0.96
    r = evaluate_position_risk(_pos(cur=cur))
    assert r.risk_level == "WARNING"
    assert any("loss warning" in a for a in r.alerts)


def test_stop_loss() -> None:
    cur = 70_000.0 * 0.90
    r = evaluate_position_risk(_pos(cur=cur))
    assert r.risk_level == "STOP_LOSS_ALERT"
    assert any("stop-loss" in a for a in r.alerts)


def test_warn_profit() -> None:
    cur = 70_000.0 * 1.12
    r = evaluate_position_risk(_pos(cur=cur))
    assert r.risk_level == "WARNING"
    assert any("profit warning" in a for a in r.alerts)


def test_take_profit() -> None:
    cur = 70_000.0 * 1.20
    r = evaluate_position_risk(_pos(cur=cur))
    assert r.risk_level == "TAKE_PROFIT_ALERT"
    assert any("take-profit" in a for a in r.alerts)


def test_peak_drawdown_warning() -> None:
    p = _pos(cur=78_000.0)
    p["raw"] = {"peak_price": 100_000.0}
    r = evaluate_position_risk(p)
    assert r.risk_level == "WARNING"
    assert any("peak drawdown" in a.lower() for a in r.alerts)


def test_avg_price_missing() -> None:
    p = _pos()
    p["avg_price"] = None
    r = evaluate_position_risk(p)
    assert r.risk_level == "WARNING"
    assert r.unrealized_pnl_pct is None


def test_current_price_missing() -> None:
    p = _pos()
    p["current_price"] = None
    r = evaluate_position_risk(p)
    assert r.risk_level == "WARNING"


def test_portfolio_mixed_alert() -> None:
    pol = RiskGuardPolicy()
    rows = [
        _pos(symbol="005930", cur=70_000.0 * 0.90),
        _pos(symbol="000660", cur=70_000.0 * 1.20),
    ]
    res = evaluate_portfolio_risk(rows, pol)
    assert res.status == "MIXED_ALERT"
    assert len(res.positions) == 2


def test_count_risk_levels_and_summarize() -> None:
    res = evaluate_portfolio_risk(
        [
            _pos(cur=70_000.0 * 0.90),
            _pos(symbol="000660", cur=70_000.0 * 1.20),
        ]
    )
    counts = count_risk_levels(res.positions)
    assert counts["stop_loss_alert_count"] == 1
    assert counts["take_profit_alert_count"] == 1
    summary = summarize_risk_result(res, risk_report_path="/tmp/risk.json")
    assert summary["risk_status"] == "MIXED_ALERT"
    assert summary["risk_report_path"] == "/tmp/risk.json"
    assert is_risk_alert_status("STOP_LOSS_ALERT")
    assert not is_risk_alert_status("WARNING")


def test_write_risk_report(tmp_path: Path) -> None:
    res = evaluate_portfolio_risk([_pos(cur=64_000.0)])
    jp, mp = write_risk_report(res, output_dir=tmp_path)
    assert jp.is_file()
    assert mp.is_file()
    text = mp.read_text(encoding="utf-8")
    assert "does not place SELL" in text
    assert "005930" in text


def test_risk_policy_from_namespace_defaults_and_overrides() -> None:
    default = risk_policy_from_namespace(Namespace())
    assert default == RiskGuardPolicy()
    custom = risk_policy_from_namespace(
        Namespace(
            stop_loss_pct=-0.05,
            take_profit_pct=0.12,
            warn_loss_pct=-0.02,
            warn_profit_pct=0.08,
        )
    )
    assert custom.stop_loss_pct == -0.05
    assert custom.take_profit_pct == 0.12
    assert custom.warn_loss_pct == -0.02
    assert custom.warn_profit_pct == 0.08
