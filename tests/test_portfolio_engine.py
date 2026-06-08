"""PortfolioEngine v1 규칙 테스트."""

from __future__ import annotations

from deepsignal.portfolio.portfolio_engine import PortfolioEngine
from deepsignal.scoring.macro_scorer import MacroScoreResult


def _sig(
    symbol: str,
    *,
    action: str = "BUY_CANDIDATE",
    final: float = 70.0,
    conf: float = 0.5,
) -> dict:
    return {
        "symbol": symbol,
        "signal_date": "2026-01-01",
        "final_score": final,
        "action": action,
        "confidence": conf,
        "technical_score": final,
        "news_score": None,
        "macro_score": None,
    }


def test_only_buy_candidate_and_positive_score() -> None:
    eng = PortfolioEngine()
    snap = eng.build_portfolio(
        [
            _sig("AAA", final=80),
            _sig("BBB", action="HOLD", final=90),
            _sig("CCC", final=-10),
        ],
        10_000.0,
        None,
    )
    syms = {a.symbol for a in snap.allocations}
    assert syms == {"AAA"}


def test_low_confidence_excluded() -> None:
    eng = PortfolioEngine()
    snap = eng.build_portfolio([_sig("LOW", conf=0.1, final=99)], 10_000.0, None)
    assert snap.allocations == []


def test_max_five_symbols() -> None:
    eng = PortfolioEngine()
    rows = [_sig(f"S{i}", final=60.0 + i) for i in range(10)]
    snap = eng.build_portfolio(rows, 100_000.0, None)
    assert len(snap.allocations) <= 5


def test_risk_off_invest_cap() -> None:
    eng = PortfolioEngine()
    rows = [_sig("A", final=80), _sig("B", final=70)]
    macro = MacroScoreResult(
        analyzed_at="t",
        macro_score=-50.0,
        market_regime="risk_off",
        confidence=1.0,
        reason="x",
        raw={},
    )
    snap = eng.build_portfolio(rows, 10_000.0, macro)
    tw = sum(a.target_weight for a in snap.allocations)
    assert tw <= 0.40 + 1e-6


def test_max_inner_weight_forty_percent() -> None:
    eng = PortfolioEngine()
    rows = [
        _sig("BIG", final=1000),
        _sig("S2", final=10),
        _sig("S3", final=10),
        _sig("S4", final=10),
        _sig("S5", final=10),
    ]
    snap = eng.build_portfolio(rows, 50_000.0, None)
    for a in snap.allocations:
        inner = float(a.raw.get("inner_weight", 0))
        assert inner <= 0.40 + 1e-5


def test_allocations_for_paper_in_raw() -> None:
    eng = PortfolioEngine()
    snap = eng.build_portfolio([_sig("X", final=65)], 20_000.0, None)
    ap = snap.raw.get("allocations_for_paper")
    assert isinstance(ap, list) and len(ap) == 1
    assert ap[0]["symbol"] == "X"
    assert "target_weight" in ap[0]
