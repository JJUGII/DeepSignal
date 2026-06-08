"""main.py kis-check 및 live-approve --broker kis."""

from __future__ import annotations

import json
from pathlib import Path

import main as main_mod
from deepsignal.live_trading.live_order_plan import LiveOrderItem, LiveOrderPlan, plan_to_json_dict


def _set_kis_env(monkeypatch, *, live: bool = False) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "dummy-app-key")
    monkeypatch.setenv("KIS_APP_SECRET", "dummy-app-secret")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "live" if live else "paper")


def test_kis_check_no_network_smoke(monkeypatch, tmp_path) -> None:
    _set_kis_env(monkeypatch)
    rc = main_mod.main(["kis-check"])
    assert rc == 0


def test_kis_check_missing_env_fails(monkeypatch) -> None:
    for k in (
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "KIS_ACCOUNT_NO",
        "KIS_ACCOUNT_PRODUCT_CODE",
    ):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("KIS_ENV", "paper")
    rc = main_mod.main(["kis-check"])
    assert rc == 1


def test_live_approve_broker_kis_safe_blocked(monkeypatch, tmp_path: Path) -> None:
    _set_kis_env(monkeypatch)
    plan = LiveOrderPlan(
        date="2026-05-15",
        capital=300_000.0,
        investable_cash=270_000.0,
        cash_buffer=30_000.0,
        currency="KRW",
        orders=[
            LiveOrderItem(
                symbol="005930",
                side="BUY",
                target_weight=0.2,
                target_value=700_000.0,
                estimated_price=70_000.0,
                estimated_qty=10,
                estimated_order_value=700_000.0,
                reason="test",
            )
        ],
        warnings=[],
        status="PENDING_APPROVAL",
        approval_required=True,
        dry_run=True,
    )
    p = tmp_path / "live_order_plan_20260515.json"
    p.write_text(json.dumps(plan_to_json_dict(plan), ensure_ascii=False), encoding="utf-8")
    rc = main_mod.main(
        [
            "live-approve",
            "--plan",
            str(p),
            "--approved",
            "--broker",
            "kis",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    audits = sorted(tmp_path.glob("live_approval_audit_*.json"))
    assert audits
    data = json.loads(audits[-1].read_text(encoding="utf-8"))
    assert data.get("broker") == "KISBroker"
    assert data.get("status") == "KIS_SAFE_MODE_COMPLETED"
    res = (data.get("results") or [{}])[0]
    assert res.get("status") == "KIS_SAFE_MODE_BLOCKED"
    assert data.get("실제_주문_없음") is True
