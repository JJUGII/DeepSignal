from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from deepsignal.collector.market.market_data import MarketData
from deepsignal.live_trading.ai_recommendation.recommendation_engine import build_recommendations
from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    OperationalRiskContext,
    RecommendationConfig,
)
from deepsignal.scoring.signal_scorer import SignalResult
from deepsignal.storage.database import (
    init_database,
    insert_market_prices,
    insert_signal_result,
    save_real_account_snapshot,
    save_real_order_history,
    save_real_positions,
)


def _db(tmp_path: Path) -> str:
    path = tmp_path / "test.db"
    init_database(str(path))
    return str(path)


def _signal(db: str, symbol: str, score: float, action: str, confidence: float = 0.8) -> None:
    insert_signal_result(
        db,
        SignalResult(
            symbol=symbol,
            signal_date="2026-05-17",
            technical_score=score,
            news_score=None,
            macro_score=None,
            final_score=score,
            action=action,
            confidence=confidence,
            reason="test signal",
            raw={},
        ),
    )


def _price(db: str, symbol: str, close: float) -> None:
    insert_market_prices(
        db,
        [
            MarketData(
                symbol=symbol,
                trade_date="2026-05-17",
                open=close,
                high=close,
                low=close,
                close=close,
                adjusted_close=close,
                volume=1000,
                source="yfinance",
                raw={},
            )
        ],
    )


def _account(*, positions: list[dict] | None = None, equity: float = 10_000.0, stale: bool = False) -> AccountContext:
    return AccountContext(
        broker="kis",
        snapshot_time=datetime.now().isoformat(timespec="seconds"),
        cash=equity,
        withdrawable_cash=equity,
        total_market_value=sum(float(p.get("market_value") or 0.0) for p in (positions or [])),
        total_equity=equity,
        positions=positions or [],
        stale_snapshot=stale,
    )


def _macro(regime: str = "neutral", score: float = 0.0) -> dict:
    return {"market_regime": regime, "macro_score": score, "confidence": 0.8, "reason": "test"}


def _cfg(**kwargs) -> RecommendationConfig:
    base = {"output_dir": "unused", "capital_limit": 10_000.0, "max_recommendations": 10}
    base.update(kwargs)
    return RecommendationConfig(**base)


def test_buy_recommendation_created(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "005930", 80.0, "BUY_CANDIDATE")
    _price(db, "005930", 100.0)

    recs = build_recommendations(db_path=db, account=_account(), macro_context=_macro(), risk_context=OperationalRiskContext(), config=_cfg())

    rec = recs[0]
    assert rec.action == "BUY"
    assert rec.allowed_for_plan is True
    assert rec.suggested_quantity > 0
    assert rec.suggested_limit_price == pytest.approx(100.0)


def test_buy_recommendation_uses_k_gsqs_signal_strategy(tmp_path: Path) -> None:
    db = _db(tmp_path)
    insert_signal_result(
        db,
        SignalResult(
            symbol="005930",
            signal_date="2026-06-10",
            technical_score=82.0,
            news_score=None,
            macro_score=None,
            final_score=82.0,
            action="BUY_CANDIDATE",
            confidence=0.82,
            reason="k-gsqs test",
            raw={},
            strategy_name="k_gsqs_v1",
        ),
    )
    _price(db, "005930", 100.0)

    recs = build_recommendations(
        db_path=db,
        account=_account(),
        macro_context=_macro(),
        risk_context=OperationalRiskContext(),
        config=_cfg(),
    )

    rec = recs[0]
    assert rec.symbol == "005930"
    assert rec.action == "BUY"
    assert rec.allowed_for_plan is True
    assert rec.score_breakdown["final_score"] == pytest.approx(82.0)


def test_domestic_price_fallback_uses_yfinance_ks_suffix(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "005930", 80.0, "BUY_CANDIDATE")
    _price(db, "005930.KS", 100.0)

    rec = build_recommendations(
        db_path=db,
        account=_account(),
        macro_context=_macro(),
        risk_context=OperationalRiskContext(),
        config=_cfg(),
    )[0]

    assert rec.symbol == "005930"
    assert rec.action == "BUY"
    assert rec.allowed_for_plan is True
    assert rec.suggested_limit_price == pytest.approx(100.0)
    assert rec.suggested_quantity > 0


def test_increase_recommendation_created_for_low_weight_position(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "005930", 90.0, "BUY_CANDIDATE")
    _price(db, "005930", 100.0)
    acct = _account(positions=[{"symbol": "005930", "quantity": 1, "current_price": 100.0, "market_value": 100.0}])

    rec = build_recommendations(db_path=db, account=acct, macro_context=_macro(), risk_context=OperationalRiskContext(), config=_cfg())[0]

    assert rec.action == "INCREASE"
    assert rec.allowed_for_plan is True


def test_reduce_and_sell_candidates_created(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "000660", -65.0, "SELL_CANDIDATE")
    _signal(db, "005930", -80.0, "SELL_CANDIDATE")
    _price(db, "000660", 100.0)
    _price(db, "005930", 100.0)
    acct = _account(
        positions=[
            {"symbol": "000660", "quantity": 10, "current_price": 100.0, "market_value": 1000.0},
            {"symbol": "005930", "quantity": 10, "current_price": 100.0, "market_value": 1000.0},
        ]
    )

    actions = {r.symbol: r.action for r in build_recommendations(db_path=db, account=acct, macro_context=_macro(), risk_context=OperationalRiskContext(), config=_cfg())}

    assert actions["000660"] == "REDUCE"
    assert actions["005930"] == "SELL"


def test_risk_off_reduces_buy_size(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "005930", 80.0, "BUY_CANDIDATE")
    _price(db, "005930", 100.0)

    normal = build_recommendations(db_path=db, account=_account(), macro_context=_macro(), risk_context=OperationalRiskContext(), config=_cfg())[0]
    risk_off = build_recommendations(db_path=db, account=_account(), macro_context=_macro("risk_off", -60.0), risk_context=OperationalRiskContext(), config=_cfg())[0]

    assert risk_off.suggested_quantity < normal.suggested_quantity
    assert any("risk_off" in note for note in risk_off.risk_notes)


def test_reconcile_mismatch_blocks_plan(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "005930", 80.0, "BUY_CANDIDATE")
    _price(db, "005930", 100.0)
    risk = OperationalRiskContext(reconcile_status="RECONCILE_MISMATCH", blocked_reasons=["reconcile=RECONCILE_MISMATCH"])

    rec = build_recommendations(db_path=db, account=_account(), macro_context=_macro(), risk_context=risk, config=_cfg())[0]

    assert rec.allowed_for_plan is False
    assert "reconcile=RECONCILE_MISMATCH" in rec.blocked_reasons


def test_stale_snapshot_blocks_plan(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "005930", 80.0, "BUY_CANDIDATE")
    _price(db, "005930", 100.0)

    rec = build_recommendations(db_path=db, account=_account(stale=True), macro_context=_macro(), risk_context=OperationalRiskContext(), config=_cfg())[0]

    assert rec.allowed_for_plan is False
    assert "stale_account_snapshot" in rec.blocked_reasons


def test_safety_audit_blocked_blocks_plan(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "005930", 80.0, "BUY_CANDIDATE")
    _price(db, "005930", 100.0)
    risk = OperationalRiskContext(safety_audit_status="SAFETY_AUDIT_BLOCKED")

    rec = build_recommendations(db_path=db, account=_account(), macro_context=_macro(), risk_context=risk, config=_cfg())[0]

    assert rec.allowed_for_plan is False
    assert "safety_audit=SAFETY_AUDIT_BLOCKED" in rec.blocked_reasons


def test_capital_limit_applies_to_order_value(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "005930", 90.0, "BUY_CANDIDATE")
    _price(db, "005930", 100.0)

    rec = build_recommendations(db_path=db, account=_account(), macro_context=_macro(), risk_context=OperationalRiskContext(), config=_cfg(capital_limit=450.0))[0]

    assert rec.suggested_quantity == 4
    assert rec.estimated_order_value == pytest.approx(400.0)


def test_high_price_buy_rounds_to_one_share_when_within_limits(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "000660", 80.0, "BUY_CANDIDATE")
    _price(db, "000660", 2400.0)

    rec = build_recommendations(
        db_path=db,
        account=_account(equity=10_000.0),
        macro_context=_macro(),
        risk_context=OperationalRiskContext(),
        config=_cfg(capital_limit=3_000.0),
    )[0]

    assert rec.allowed_for_plan is True
    assert rec.suggested_quantity == 1
    assert rec.estimated_order_value == pytest.approx(2400.0)


def test_high_price_buy_stays_blocked_when_one_share_exceeds_capital(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "000660", 80.0, "BUY_CANDIDATE")
    _price(db, "000660", 2400.0)

    rec = build_recommendations(
        db_path=db,
        account=_account(equity=10_000.0),
        macro_context=_macro(),
        risk_context=OperationalRiskContext(),
        config=_cfg(capital_limit=2_000.0),
    )[0]

    assert rec.allowed_for_plan is False
    assert rec.suggested_quantity == 0
    assert "suggested_quantity_zero" in rec.blocked_reasons


def test_duplicate_recent_order_blocks_plan(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _signal(db, "005930", 80.0, "BUY_CANDIDATE")
    _price(db, "005930", 100.0)
    save_real_order_history(db, broker="kis", symbol="005930", side="BUY", quantity=1, status="PENDING", created_at=datetime.now().isoformat(timespec="seconds"))

    rec = build_recommendations(db_path=db, account=_account(), macro_context=_macro(), risk_context=OperationalRiskContext(), config=_cfg())[0]

    assert rec.allowed_for_plan is False
    assert "duplicate_order_risk:005930" in rec.blocked_reasons
