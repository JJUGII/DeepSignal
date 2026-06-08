"""PaperTradingEngine v1 테스트."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from deepsignal.collector.market.market_data import MarketData
from deepsignal.paper_trading.paper_trading_engine import PaperTradingEngine
from deepsignal.scoring.signal_scorer import SignalResult, SignalScorer
from deepsignal.storage.database import (
    init_database,
    insert_market_prices,
    upsert_paper_position,
)


def _market_rows(symbol: str, n: int = 35) -> list[MarketData]:
    base = date(2026, 1, 4)
    out: list[MarketData] = []
    for i in range(n):
        d = (base + timedelta(days=i)).isoformat()
        c = 100.0 + i * 0.4
        out.append(
            MarketData(
                symbol=symbol,
                trade_date=d,
                open=c - 0.5,
                high=c + 0.5,
                low=c - 1.0,
                close=c,
                adjusted_close=None,
                volume=1000,
                source="yfinance",
                raw={},
            )
        )
    return out


def test_buy_creates_position(tmp_path) -> None:
    db = tmp_path / "p1.db"
    p = str(db)
    init_database(p)
    insert_market_prices(p, _market_rows("PTB"))

    class _S(SignalScorer):
        def score_latest(self, symbol, indicators):
            return SignalResult(
                symbol=symbol,
                signal_date=indicators[-1].trade_date,
                technical_score=70.0,
                news_score=None,
                macro_score=None,
                final_score=70.0,
                action="BUY_CANDIDATE",
                confidence=0.7,
                reason="mock buy",
                raw={},
            )

    eng = PaperTradingEngine(scorer=_S())
    snap = eng.run_step(p, "PTB")
    assert snap is not None
    assert snap.last_action == "BUY_CANDIDATE"
    assert len(snap.positions) == 1
    assert snap.positions[0].symbol == "PTB"
    assert snap.positions[0].quantity > 0
    assert snap.cash < 10000.0


def test_sell_closes_position(tmp_path) -> None:
    db = tmp_path / "p2.db"
    p = str(db)
    init_database(p)
    insert_market_prices(p, _market_rows("PTS"))

    class _Sell(SignalScorer):
        def score_latest(self, symbol, indicators):
            return SignalResult(
                symbol=symbol,
                signal_date=indicators[-1].trade_date,
                technical_score=-70.0,
                news_score=None,
                macro_score=None,
                final_score=-70.0,
                action="SELL_CANDIDATE",
                confidence=0.7,
                reason="mock sell",
                raw={},
            )

    upsert_paper_position(p, {"symbol": "PTS", "quantity": 10, "avg_price": 100.0})
    eng = PaperTradingEngine(scorer=_Sell())
    snap = eng.run_step(p, "PTS")
    assert snap is not None
    assert snap.last_action == "SELL_CANDIDATE"
    assert snap.positions == []


def test_hold_no_trade_row(tmp_path) -> None:
    db = tmp_path / "p3.db"
    p = str(db)
    init_database(p)
    insert_market_prices(p, _market_rows("PTH"))

    class _H(SignalScorer):
        def score_latest(self, symbol, indicators):
            return SignalResult(
                symbol=symbol,
                signal_date=indicators[-1].trade_date,
                technical_score=0.0,
                news_score=None,
                macro_score=None,
                final_score=0.0,
                action="HOLD",
                confidence=0.0,
                reason="hold",
                raw={},
            )

    eng = PaperTradingEngine(scorer=_H())
    eng.run_step(p, "PTH")
    with sqlite3.connect(p) as conn:
        n = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    assert int(n) == 0


def test_equity_equals_cash_plus_positions_value(tmp_path) -> None:
    db = tmp_path / "p4.db"
    p = str(db)
    init_database(p)
    insert_market_prices(p, _market_rows("PTX"))

    class _S(SignalScorer):
        def score_latest(self, symbol, indicators):
            return SignalResult(
                symbol=symbol,
                signal_date=indicators[-1].trade_date,
                technical_score=70.0,
                news_score=None,
                macro_score=None,
                final_score=70.0,
                action="BUY_CANDIDATE",
                confidence=0.7,
                reason="buy",
                raw={},
            )

    snap = PaperTradingEngine(scorer=_S()).run_step(p, "PTX")
    assert snap is not None
    assert abs(snap.equity - (snap.cash + snap.positions_value)) < 1e-6


def test_insufficient_data_returns_none(tmp_path) -> None:
    db = tmp_path / "p5.db"
    p = str(db)
    init_database(p)
    eng = PaperTradingEngine()
    assert eng.run_step(p, "NOPE") is None
