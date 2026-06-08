"""live_order_plan: 자본·비중·파일 생성 (브로커 없음)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepsignal.live_trading.live_order_plan import (
    LiveOrderItem,
    LiveOrderPlan,
    LiveOrderPlanConfig,
    build_live_order_plan,
    compute_investable_cash,
    live_order_plan_from_dict,
    plan_to_json_dict,
    run_live_plan_cli,
    write_live_order_plan_files,
)
from deepsignal.portfolio.portfolio_models import PortfolioSnapshot


def test_compute_investable_cash() -> None:
    inv, buf = compute_investable_cash(300_000.0, 0.1)
    assert buf == pytest.approx(30_000.0)
    assert inv == pytest.approx(270_000.0)


def _fake_snapshot(allocs: list[dict]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        analyzed_at="2026-05-15T00:00:00+00:00",
        total_cash=270_000.0,
        market_regime="neutral",
        allocations=[],
        raw={"allocations_for_paper": allocs},
    )


def test_max_position_pct_caps_target_value(monkeypatch, tmp_path) -> None:
    db = str(tmp_path / "x.db")

    def _fake_build(self, signals, total_cash, macro_result=None):
        return _fake_snapshot(
            [
                {
                    "symbol": "BIG",
                    "target_weight": 0.9,
                    "target_amount": 500_000.0,
                    "rationale": "test",
                }
            ]
        )

    monkeypatch.setattr(
        "deepsignal.portfolio.portfolio_engine.PortfolioEngine.build_portfolio",
        _fake_build,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_signals",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_economic_indicators",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "deepsignal.scoring.macro_scorer.MacroScorer.calculate_macro_score",
        lambda self, *_a, **_k: None,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_market_price",
        lambda *_a, **_k: {"symbol": "BIG", "trade_date": "2026-05-15", "close": 100.0},
    )

    cfg = LiveOrderPlanConfig(
        capital=300_000.0,
        max_symbols=3,
        max_position_pct=0.25,
        min_order_value=1.0,
        cash_buffer_pct=0.1,
        currency="USD",
    )
    plan = build_live_order_plan(db, cfg, plan_date="2026-05-15")
    assert len(plan.orders) == 1
    o = plan.orders[0]
    assert o.symbol == "BIG"
    assert o.target_value == pytest.approx(75_000.0)
    assert o.estimated_qty == 750


def test_min_order_value_excludes_small_orders(monkeypatch, tmp_path) -> None:
    db = str(tmp_path / "x.db")

    def _fake_build(self, signals, total_cash, macro_result=None):
        return _fake_snapshot(
            [
                {
                    "symbol": "TINY",
                    "target_weight": 0.1,
                    "target_amount": 5_000.0,
                    "rationale": "test",
                }
            ]
        )

    monkeypatch.setattr(
        "deepsignal.portfolio.portfolio_engine.PortfolioEngine.build_portfolio",
        _fake_build,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_signals",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_economic_indicators",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "deepsignal.scoring.macro_scorer.MacroScorer.calculate_macro_score",
        lambda self, *_a, **_k: None,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_market_price",
        lambda *_a, **_k: {"symbol": "TINY", "trade_date": "2026-05-15", "close": 100.0},
    )

    cfg = LiveOrderPlanConfig(
        capital=300_000.0,
        max_symbols=3,
        max_position_pct=1.0,
        min_order_value=10_000.0,
        cash_buffer_pct=0.0,
        currency="USD",
    )
    plan = build_live_order_plan(db, cfg, plan_date="2026-05-15")
    assert plan.orders == []


def test_estimated_qty_uses_floor(monkeypatch, tmp_path) -> None:
    db = str(tmp_path / "x.db")

    def _fake_build(self, signals, total_cash, macro_result=None):
        return _fake_snapshot(
            [
                {
                    "symbol": "FLOOR",
                    "target_weight": 0.2,
                    "target_amount": 250.0,
                    "rationale": "test",
                }
            ]
        )

    monkeypatch.setattr(
        "deepsignal.portfolio.portfolio_engine.PortfolioEngine.build_portfolio",
        _fake_build,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_signals",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_economic_indicators",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "deepsignal.scoring.macro_scorer.MacroScorer.calculate_macro_score",
        lambda self, *_a, **_k: None,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_market_price",
        lambda *_a, **_k: {"symbol": "FLOOR", "trade_date": "2026-05-15", "close": 100.0},
    )

    cfg = LiveOrderPlanConfig(
        capital=1_000.0,
        max_symbols=3,
        max_position_pct=1.0,
        min_order_value=1.0,
        cash_buffer_pct=0.0,
        currency="USD",
    )
    plan = build_live_order_plan(db, cfg, plan_date="2026-05-15")
    assert len(plan.orders) == 1
    assert plan.orders[0].estimated_qty == 2
    assert plan.orders[0].estimated_order_value == pytest.approx(200.0)


def test_json_and_md_written(monkeypatch, tmp_path) -> None:
    db = str(tmp_path / "x.db")

    def _fake_build(self, signals, total_cash, macro_result=None):
        return _fake_snapshot(
            [
                {
                    "symbol": "OK",
                    "target_weight": 0.3,
                    "target_amount": 30_000.0,
                    "rationale": "ok",
                }
            ]
        )

    monkeypatch.setattr(
        "deepsignal.portfolio.portfolio_engine.PortfolioEngine.build_portfolio",
        _fake_build,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_signals",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_economic_indicators",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "deepsignal.scoring.macro_scorer.MacroScorer.calculate_macro_score",
        lambda self, *_a, **_k: None,
    )
    monkeypatch.setattr(
        "deepsignal.storage.database.fetch_latest_market_price",
        lambda *_a, **_k: {"symbol": "OK", "trade_date": "2026-05-20", "close": 150.0},
    )

    cfg = LiveOrderPlanConfig(
        capital=100_000.0,
        max_symbols=3,
        max_position_pct=0.5,
        min_order_value=100.0,
        cash_buffer_pct=0.0,
        currency="USD",
    )
    plan = build_live_order_plan(db, cfg, plan_date="2026-05-20")
    out = tmp_path / "out"
    jp, mp = write_live_order_plan_files(plan, output_dir=out)

    assert jp.exists() and mp.exists()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["status"] == "PENDING_APPROVAL"
    assert data["approval_required"] is True
    assert data["dry_run"] is True
    assert len(data["orders"]) >= 1

    md = mp.read_text(encoding="utf-8")
    assert "PENDING_APPROVAL" in md
    assert "OK" in md


def test_live_order_plan_from_dict_roundtrip() -> None:
    plan = LiveOrderPlan(
        date="2026-05-15",
        capital=100_000.0,
        investable_cash=90_000.0,
        cash_buffer=10_000.0,
        currency="USD",
        orders=[
            LiveOrderItem(
                symbol="X",
                side="BUY",
                target_weight=0.1,
                target_value=10_000.0,
                estimated_price=50.0,
                estimated_qty=10,
                estimated_order_value=500.0,
                reason="r",
                warnings=["w"],
            )
        ],
        warnings=["pw"],
        status="PENDING_APPROVAL",
        approval_required=True,
        dry_run=True,
    )
    p2 = live_order_plan_from_dict(plan_to_json_dict(plan))
    assert p2.date == plan.date
    assert len(p2.orders) == 1
    assert p2.orders[0].symbol == "X"
    assert p2.orders[0].warnings == ["w"]
    assert p2.warnings == ["pw"]
