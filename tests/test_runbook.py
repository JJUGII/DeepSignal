"""runbook.py pre/post trade orchestration ([실전-10])."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from deepsignal.live_trading.broker_interface import BrokerCashBalance, BrokerPosition
from deepsignal.live_trading.reconcile import ReconcileResult, write_latest_reconcile_state
from deepsignal.live_trading.risk_guard import RiskGuardPolicy
from deepsignal.live_trading.runbook import (
    PostTradeRunbookParams,
    PreTradeRunbookParams,
    resolve_post_trade_final_status,
    run_post_trade_runbook,
    run_pre_trade_runbook,
    validate_plan_for_runbook,
    write_runbook_report,
)
from deepsignal.live_trading.trading_session import TradingSessionPolicy, TradingSessionResult
from deepsignal.storage.database import (
    init_database,
    save_real_account_snapshot,
    save_real_order_history,
)


def _kr_plan(tmp_path: Path) -> Path:
    d = {
        "date": "2026-05-15",
        "status": "PENDING_APPROVAL",
        "approval_required": True,
        "dry_run": True,
        "capital": 1_000_000.0,
        "investable_cash": 1_000_000.0,
        "cash_buffer": 0.0,
        "currency": "KRW",
        "orders": [
            {
                "symbol": "005930",
                "side": "BUY",
                "target_weight": 1.0,
                "target_value": 70_000.0,
                "estimated_price": 70_000.0,
                "estimated_qty": 1,
                "estimated_order_value": 70_000.0,
                "reason": "t",
                "warnings": [],
            }
        ],
        "warnings": [],
    }
    p = tmp_path / "live_order_plan.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def _open_session() -> TradingSessionResult:
    now = datetime(2026, 5, 15, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    return TradingSessionResult(
        is_open=True,
        reason="regular trading session",
        market="KR",
        now=now,
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )


def _mock_broker(*, positions: list[BrokerPosition] | None = None) -> MagicMock:
    br = MagicMock()
    br.config.env = "paper"
    br.get_positions.return_value = list(positions or [])
    br.get_cash_balance.return_value = BrokerCashBalance(
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
    )
    br.get_order_status.return_value = []
    return br


def _seed_ok(db: str, out: Path) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    save_real_account_snapshot(
        db,
        now,
        "kis",
        cash=1_000_000.0,
        withdrawable_cash=900_000.0,
        total_market_value=0.0,
        total_equity=1_000_000.0,
        raw_payload={"timestamp": now, "positions": []},
    )
    report = out / "reconcile_seed.json"
    report.write_text(json.dumps({"success": True, "matched": []}), encoding="utf-8")
    write_latest_reconcile_state(
        report,
        ReconcileResult(success=True, matched=[]),
        output_dir=out,
    )


def test_session_closed_stops_pre_trade(tmp_path: Path) -> None:
    closed = TradingSessionResult(
        is_open=False,
        reason="outside regular trading hours",
        market="KR",
        now="2026-05-15T08:00:00+09:00",
        timezone="Asia/Seoul",
        session_open="09:00",
        session_close="15:30",
    )
    params = PreTradeRunbookParams(
        plan_path=str(_kr_plan(tmp_path)),
        symbol="005930",
        output_dir=str(tmp_path),
        db_path=str(tmp_path / "x.db"),
        network=True,
    )
    with patch("deepsignal.live_trading.runbook.is_trading_session_open", return_value=closed):
        result = run_pre_trade_runbook(params, broker=_mock_broker())
    assert result.final_status == "PRE_TRADE_BLOCKED"
    assert len(result.steps) == 1
    assert result.steps[0].step_name == "trading_session"


def test_reconcile_mismatch_stops_pre_trade(tmp_path: Path) -> None:
    db = str(tmp_path / "r.db")
    init_database(db)
    from deepsignal.storage.database import save_real_positions

    _seed_ok(db, tmp_path)
    save_real_positions(
        db,
        datetime.now().isoformat(timespec="seconds"),
        "kis",
        [{"symbol": "005930", "quantity": 1, "avg_price": 1.0, "current_price": 1.0, "market_value": 1.0, "raw": {}}],
    )
    br = _mock_broker(
        positions=[
            BrokerPosition(symbol="005930", quantity=5, avg_price=1.0, current_price=1.0, market_value=5.0)
        ]
    )
    params = PreTradeRunbookParams(
        plan_path=str(_kr_plan(tmp_path)),
        symbol="005930",
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        save_db=False,
        session_now=datetime(2026, 5, 15, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    with patch("deepsignal.live_trading.runbook.is_trading_session_open", return_value=_open_session()):
        result = run_pre_trade_runbook(params, broker=br)
    assert result.final_status == "PRE_TRADE_BLOCKED"
    names = [s.step_name for s in result.steps]
    assert "reconcile" in names
    assert result.steps[-1].step_name == "reconcile" or not result.success


def test_duplicate_guard_stops_pre_trade(tmp_path: Path) -> None:
    db = str(tmp_path / "dup.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    save_real_order_history(
        db,
        broker="kis",
        symbol="005930",
        side="BUY",
        quantity=1,
        limit_price=70_000.0,
        status="KIS_ORDER_SUBMITTED",
        raw_payload={},
    )
    br = _mock_broker()
    params = PreTradeRunbookParams(
        plan_path=str(_kr_plan(tmp_path)),
        symbol="005930",
        quantity=1,
        limit_price=70_000.0,
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        session_now=datetime(2026, 5, 15, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    with patch("deepsignal.live_trading.runbook.is_trading_session_open", return_value=_open_session()):
        result = run_pre_trade_runbook(params, broker=br)
    assert result.final_status == "PRE_TRADE_BLOCKED"
    assert any(s.step_name == "duplicate_guard" and not s.success for s in result.steps)


def test_partial_fill_stops_pre_trade(tmp_path: Path) -> None:
    db = str(tmp_path / "pf.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    from deepsignal.storage.database import save_real_fill

    save_real_order_history(
        db,
        broker="kis",
        symbol="005930",
        side="BUY",
        quantity=10,
        limit_price=70_000.0,
        status="PARTIAL",
        raw_payload={"filled_quantity": 3, "remaining_quantity": 7},
        order_id="ORD1",
    )
    save_real_fill(
        db,
        broker="kis",
        order_id="ORD1",
        symbol="005930",
        fill_quantity=3,
        fill_price=70_000.0,
        fill_id="f1",
        raw_payload={},
        side="BUY",
    )
    br = _mock_broker()
    params = PreTradeRunbookParams(
        plan_path=str(_kr_plan(tmp_path)),
        symbol="005930",
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        session_now=datetime(2026, 5, 15, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    with patch("deepsignal.live_trading.runbook.is_trading_session_open", return_value=_open_session()):
        result = run_pre_trade_runbook(params, broker=br)
    assert result.final_status == "PRE_TRADE_BLOCKED"
    assert any(s.step_name == "duplicate_guard" and not s.success for s in result.steps)


def test_pre_trade_happy_path(tmp_path: Path) -> None:
    db = str(tmp_path / "ok.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    br = _mock_broker()
    params = PreTradeRunbookParams(
        plan_path=str(_kr_plan(tmp_path)),
        symbol="005930",
        quantity=1,
        limit_price=70_000.0,
        allow_symbols=["005930"],
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        session_now=datetime(2026, 5, 15, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    with patch("deepsignal.live_trading.runbook.is_trading_session_open", return_value=_open_session()):
        result = run_pre_trade_runbook(params, broker=br)
    assert result.final_status == "PRE_TRADE_READY"
    assert result.success is True
    names = [s.step_name for s in result.steps]
    assert names == [
        "trading_session",
        "account_sync",
        "reconcile",
        "duplicate_guard",
        "plan_validation",
        "summary",
    ]


def test_plan_validation_sell_blocked(tmp_path: Path) -> None:
    from deepsignal.live_trading.live_order_plan import LiveOrderItem, LiveOrderPlan

    plan = LiveOrderPlan(
        date="2026-05-15",
        capital=1_000_000.0,
        investable_cash=1_000_000.0,
        cash_buffer=0.0,
        currency="KRW",
        orders=[
            LiveOrderItem(
                symbol="005930",
                side="SELL",
                target_weight=1.0,
                target_value=70_000.0,
                estimated_price=70_000.0,
                estimated_qty=1,
                estimated_order_value=70_000.0,
                reason="x",
            )
        ],
        warnings=[],
        status="PENDING_APPROVAL",
        approval_required=True,
    )
    ok, errs = validate_plan_for_runbook(
        plan,
        target_symbol="005930",
        allow_symbols=["005930"],
        max_single_order_value=100_000.0,
        max_total_order_value=200_000.0,
    )
    assert ok is False
    assert any("SELL" in e for e in errs)


def test_step_ordering_and_warning_propagation(tmp_path: Path) -> None:
    db = str(tmp_path / "warn.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    br = _mock_broker()
    params = PreTradeRunbookParams(
        plan_path=str(_kr_plan(tmp_path)),
        symbol="005930",
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        session_now=datetime(2026, 5, 15, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    with patch("deepsignal.live_trading.runbook.is_trading_session_open", return_value=_open_session()):
        result = run_pre_trade_runbook(params, broker=br)
    jp, mp = write_runbook_report(result, output_dir=tmp_path)
    assert jp.is_file()
    assert mp.is_file()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert len(data["steps"]) == len(result.steps)


def _position_row(*, avg: float = 70_000.0, cur: float = 71_000.0) -> dict:
    return {
        "symbol": "005930",
        "quantity": 1,
        "avg_price": avg,
        "current_price": cur,
        "market_value": cur,
        "raw": {},
    }


def _broker_position(*, avg: float = 70_000.0, cur: float = 71_000.0) -> BrokerPosition:
    return BrokerPosition(
        symbol="005930",
        quantity=1,
        avg_price=avg,
        current_price=cur,
        market_value=cur,
    )


def _save_position(
    db: str,
    *,
    avg: float = 70_000.0,
    cur: float = 71_000.0,
) -> None:
    from deepsignal.storage.database import save_real_positions

    now = datetime.now().isoformat(timespec="seconds")
    save_real_positions(db, now, "kis", [_position_row(avg=avg, cur=cur)])


def _broker_for_position(*, avg: float = 70_000.0, cur: float = 71_000.0) -> MagicMock:
    return _mock_broker(positions=[_broker_position(avg=avg, cur=cur)])


def test_post_trade_success(tmp_path: Path) -> None:
    db = str(tmp_path / "post.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    _save_position(db)
    audit = tmp_path / "audit.json"
    audit.write_text(
        json.dumps({"results": [{"raw": {"output": {"ODNO": "99"}}}]}),
        encoding="utf-8",
    )
    br = _broker_for_position()
    params = PostTradeRunbookParams(
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        audit_path=str(audit),
    )
    result = run_post_trade_runbook(params, broker=br)
    assert result.final_status == "POST_TRADE_OK"
    names = [s.step_name for s in result.steps]
    assert names.index("risk_check") < names.index("summary")
    assert len(result.steps) >= 6
    assert result.steps[0].step_name == "order_status"
    assert list(tmp_path.glob("risk_alert_*.json"))
    assert "ops_dashboard" not in names
    assert result.summary["generated_reports"]["risk_report"]


def test_post_trade_risk_ok(tmp_path: Path) -> None:
    db = str(tmp_path / "rok.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    _save_position(db, avg=70_000.0, cur=71_000.0)
    br = _broker_for_position(avg=70_000.0, cur=71_000.0)
    params = PostTradeRunbookParams(
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        order_id="1",
    )
    result = run_post_trade_runbook(params, broker=br)
    assert result.final_status == "POST_TRADE_OK"
    assert result.summary["risk_status"] == "OK"


def test_post_trade_with_summary_generates_report_chain(tmp_path: Path) -> None:
    db = str(tmp_path / "chain.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    _save_position(db, avg=70_000.0, cur=71_000.0)
    br = _broker_for_position(avg=70_000.0, cur=71_000.0)
    params = PostTradeRunbookParams(
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        order_id="1",
        with_summary=True,
        generate_html_dashboard=True,
    )
    result = run_post_trade_runbook(params, broker=br)
    names = [s.step_name for s in result.steps]
    assert names[-5:] == ["ops_dashboard", "sell_plan", "daily_ops_summary", "html_dashboard", "summary"]
    reports = result.summary["generated_reports"]
    assert reports["risk_report"].endswith(".json")
    assert reports["ops_dashboard_json"].endswith(".json")
    assert reports["sell_plan_json"].endswith(".json")
    assert reports["daily_ops_summary_json"].endswith(".json")
    assert reports["html_dashboard"].endswith("OPS_DASHBOARD.html")
    assert (tmp_path / "OPS_DASHBOARD.html").is_file()


def test_post_trade_report_chain_failure_is_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = str(tmp_path / "chain_fail.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    _save_position(db)
    br = _broker_for_position()

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("ops broken")

    monkeypatch.setattr("deepsignal.live_trading.ops_dashboard.run_ops_dashboard", boom)
    params = PostTradeRunbookParams(
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        order_id="1",
        with_summary=True,
        generate_html_dashboard=True,
    )
    result = run_post_trade_runbook(params, broker=br)
    assert result.final_status == "POST_TRADE_WARNING"
    assert any(s.step_name == "ops_dashboard" and s.status == "WARNING" and s.success for s in result.steps)
    assert any("ops_dashboard report generation failed" in w for w in result.warnings)
    assert result.summary["generated_reports"]["html_dashboard"].endswith("OPS_DASHBOARD.html")


def test_post_trade_risk_warning(tmp_path: Path) -> None:
    db = str(tmp_path / "rw.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    cur = 70_000.0 * 0.96
    _save_position(db, avg=70_000.0, cur=cur)
    br = _broker_for_position(avg=70_000.0, cur=cur)
    params = PostTradeRunbookParams(
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        order_id="1",
    )
    result = run_post_trade_runbook(params, broker=br)
    assert result.final_status == "POST_TRADE_WARNING"
    assert result.summary["risk_status"] == "WARNING"
    assert any(s.step_name == "risk_check" and s.status == "WARNING" for s in result.steps)


def test_post_trade_risk_stop_loss_alert(tmp_path: Path) -> None:
    db = str(tmp_path / "rsl.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    _save_position(db, avg=70_000.0, cur=64_000.0)
    br = _broker_for_position(avg=70_000.0, cur=64_000.0)
    params = PostTradeRunbookParams(
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        order_id="1",
    )
    result = run_post_trade_runbook(params, broker=br)
    assert result.final_status == "POST_TRADE_RISK_ALERT"
    assert result.summary["risk_status"] == "STOP_LOSS_ALERT"
    assert result.summary["stop_loss_alert_count"] >= 1
    assert result.summary["risk_report_path"]
    jp, mp = write_runbook_report(result, output_dir=tmp_path)
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["summary"]["risk_status"] == "STOP_LOSS_ALERT"
    md = mp.read_text(encoding="utf-8")
    assert "## Risk Summary" in md
    assert "stop-loss" in md.lower() or "STOP_LOSS" in md


def test_post_trade_custom_risk_policy_changes_status_and_reports(tmp_path: Path) -> None:
    db = str(tmp_path / "custom_policy.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    _save_position(db, avg=70_000.0, cur=66_500.0)
    br = _broker_for_position(avg=70_000.0, cur=66_500.0)
    params = PostTradeRunbookParams(
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        order_id="1",
    )
    result = run_post_trade_runbook(
        params,
        broker=br,
        risk_policy=RiskGuardPolicy(
            stop_loss_pct=-0.05,
            take_profit_pct=0.12,
            warn_loss_pct=-0.02,
            warn_profit_pct=0.08,
        ),
    )
    assert result.final_status == "POST_TRADE_RISK_ALERT"
    assert result.summary["risk_status"] == "STOP_LOSS_ALERT"
    assert result.summary["risk_policy"] == {
        "stop_loss_pct": -0.05,
        "take_profit_pct": 0.12,
        "warn_loss_pct": -0.02,
        "warn_profit_pct": 0.08,
    }
    risk_report = Path(result.summary["risk_report_path"])
    risk_data = json.loads(risk_report.read_text(encoding="utf-8"))
    assert risk_data["policy"]["stop_loss_pct"] == -0.05
    jp, mp = write_runbook_report(result, output_dir=tmp_path)
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["summary"]["risk_policy"]["take_profit_pct"] == 0.12
    md = mp.read_text(encoding="utf-8")
    assert "## Risk Policy" in md
    assert "Stop loss: -5%" in md
    assert "Take profit: 12%" in md


def test_post_trade_risk_take_profit_alert(tmp_path: Path) -> None:
    db = str(tmp_path / "rtp.db")
    init_database(db)
    _seed_ok(db, tmp_path)
    _save_position(db, avg=70_000.0, cur=84_000.0)
    br = _broker_for_position(avg=70_000.0, cur=84_000.0)
    params = PostTradeRunbookParams(
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        order_id="1",
    )
    result = run_post_trade_runbook(params, broker=br)
    assert result.final_status == "POST_TRADE_RISK_ALERT"
    assert result.summary["risk_status"] == "TAKE_PROFIT_ALERT"
    assert result.summary["take_profit_alert_count"] >= 1


def test_resolve_post_trade_final_status_priority() -> None:
    from deepsignal.live_trading.runbook import RunbookStepResult

    ok_step = RunbookStepResult(
        step_name="reconcile",
        success=True,
        status="OK",
        message="ok",
        started_at="t",
        finished_at="t",
        duration_ms=0,
    )
    final, success = resolve_post_trade_final_status([ok_step], [], "STOP_LOSS_ALERT")
    assert final == "POST_TRADE_RISK_ALERT"
    assert success is True
    final2, _ = resolve_post_trade_final_status([ok_step], [], "WARNING")
    assert final2 == "POST_TRADE_WARNING"


def test_post_trade_warning_on_reconcile_mismatch(tmp_path: Path) -> None:
    db = str(tmp_path / "pw.db")
    init_database(db)
    from deepsignal.storage.database import save_real_positions

    _seed_ok(db, tmp_path)
    save_real_positions(
        db,
        datetime.now().isoformat(timespec="seconds"),
        "kis",
        [{"symbol": "005930", "quantity": 1, "avg_price": 1.0, "current_price": 1.0, "market_value": 1.0, "raw": {}}],
    )
    br = _mock_broker(
        positions=[
            BrokerPosition(symbol="005930", quantity=2, avg_price=1.0, current_price=1.0, market_value=2.0)
        ]
    )
    params = PostTradeRunbookParams(
        output_dir=str(tmp_path),
        db_path=db,
        network=True,
        save_db=False,
        order_id="1",
    )
    result = run_post_trade_runbook(params, broker=br)
    assert result.final_status == "POST_TRADE_BLOCKED"
    assert any(s.step_name == "reconcile" and not s.success for s in result.steps)
