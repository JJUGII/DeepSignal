from __future__ import annotations

from datetime import datetime
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
from deepsignal.storage.database import init_database, insert_market_prices, insert_signal_result


def _db(tmp_path: Path) -> str:
    path = tmp_path / "q.db"
    init_database(str(path))
    return str(path)


def _seed(db: str, symbol: str, *, score: float = 80.0, volume: float = 100_000.0) -> None:
    insert_signal_result(
        db,
        SignalResult(
            symbol=symbol,
            signal_date="2026-05-17",
            technical_score=score,
            news_score=10.0,
            macro_score=5.0,
            final_score=score,
            action="BUY_CANDIDATE",
            confidence=0.8,
            reason="test",
            raw={},
        ),
    )
    rows = []
    for i in range(25):
        d = f"2026-05-{i+1:02d}"
        rows.append(
            MarketData(
                symbol=symbol,
                trade_date=d,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                adjusted_close=100.0,
                volume=volume,
                source="yfinance",
                raw={},
            )
        )
    insert_market_prices(db, rows)


def test_quality_gates_add_breakdown(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db, "005930")
    recs = build_recommendations(
        db_path=db,
        account=AccountContext(cash=10_000.0, withdrawable_cash=10_000.0, total_equity=10_000.0),
        macro_context={"market_regime": "neutral", "macro_score": 0.0},
        risk_context=OperationalRiskContext(),
        config=RecommendationConfig(capital_limit=10_000.0, enable_quality_gates=True),
    )
    rec = recs[0]
    assert rec.score_breakdown.get("display")
    assert "technical" in rec.score_breakdown["display"]


def test_liquidity_blocks_low_volume(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db, "005930", volume=10.0)
    recs = build_recommendations(
        db_path=db,
        account=AccountContext(cash=10_000.0, withdrawable_cash=10_000.0, total_equity=10_000.0),
        macro_context={"market_regime": "neutral", "macro_score": 0.0},
        risk_context=OperationalRiskContext(),
        config=RecommendationConfig(
            capital_limit=10_000.0,
            enable_quality_gates=True,
            liquidity_limit_pct=0.01,
        ),
    )
    assert recs[0].allowed_for_plan is False
    assert any("SKIP" in b or "liquidity" in b.lower() for b in recs[0].blocked_reasons)


def test_min_final_score_blocks(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed(db, "005930", score=55.0)
    recs = build_recommendations(
        db_path=db,
        account=AccountContext(cash=10_000.0, withdrawable_cash=10_000.0, total_equity=10_000.0),
        macro_context={"market_regime": "neutral", "macro_score": 0.0},
        risk_context=OperationalRiskContext(),
        config=RecommendationConfig(
            min_final_score=60.0,
            enable_quality_gates=True,
            use_validation_tuned_min_score=False,
        ),
    )
    assert recs[0].action == "BUY"
    assert recs[0].allowed_for_plan is False
