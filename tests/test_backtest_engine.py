"""BacktestEngine v1 단위 테스트."""

from __future__ import annotations

import math

from deepsignal.backtest.backtest_engine import BacktestEngine, BacktestResult
from deepsignal.scoring.signal_scorer import SignalResult, SignalScorer


def _rows_uptrend(n: int = 45, sym: str = "TST") -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        p = 100.0 + float(i) * 0.8
        d = f"2024-01-{i + 1:02d}" if i < 31 else f"2024-02-{i - 30:02d}"
        out.append(
            {
                "symbol": sym,
                "timeframe": "1d",
                "bar_time": d,
                "source": "yfinance",
                "close": p,
                "open": p,
                "high": p,
                "low": p,
            }
        )
    return out


def test_uptrend_returns_result() -> None:
    eng = BacktestEngine()
    r = eng.run_symbol_backtest("TST", _rows_uptrend())
    assert r is not None
    assert isinstance(r, BacktestResult)
    assert r.symbol == "TST"
    assert r.initial_cash == 10000.0
    assert math.isfinite(r.final_value)
    assert "trades" in r.raw and "equity_curve" in r.raw and "parameters" in r.raw


def test_buy_candidate_opens_position() -> None:
    class _S(SignalScorer):
        def score_latest(self, symbol, indicators, *, news_score=None, macro_score=None, extra_raw=None):
            i = len(indicators) - 1
            if i < 8:
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

    rows = _rows_uptrend(20)
    eng = BacktestEngine(scorer=_S())
    r = eng.run_symbol_backtest("TST", rows)
    assert r is not None
    assert any(t.get("entry_date") for t in r.raw["trades"])


def test_raw_has_trades_equity_parameters() -> None:
    eng = BacktestEngine()
    r = eng.run_symbol_backtest("TST", _rows_uptrend(30))
    assert r is not None
    assert isinstance(r.raw["trades"], list)
    assert isinstance(r.raw["equity_curve"], list) and len(r.raw["equity_curve"]) > 0
    p = r.raw["parameters"]
    assert p.get("include_news") is False
    assert p.get("db_path_used") is False
    assert p["commission_rate"] == 0.0
    assert p["slippage_bps"] == 0.0
    assert p["execution"] == "next_bar_close"


def test_skips_none_close_without_error() -> None:
    rows = _rows_uptrend(12)
    rows[3] = {**rows[3], "close": None}
    rows[7] = {**rows[7], "close": None}
    eng = BacktestEngine()
    r = eng.run_symbol_backtest("TST", rows)
    assert r is not None
