"""SignalScorer 단위 테스트."""

from __future__ import annotations

from deepsignal.analyzer.technical.technical_analyzer import TechnicalIndicator
from deepsignal.scoring.signal_scorer import SignalScorer


def _ind(
    *,
    close: float | None = 100.0,
    ema_12: float | None = 90.0,
    ema_26: float | None = 85.0,
    rsi: float | None = 50.0,
    trend: float | None = 0.0,
    date: str = "2024-06-01",
) -> TechnicalIndicator:
    return TechnicalIndicator(
        symbol="T",
        trade_date=date,
        close=close,
        ema_12=ema_12,
        ema_26=ema_26,
        rsi_14=rsi,
        trend_score=trend,
        raw={},
    )


def test_trend_positive_increases_score() -> None:
    sc = SignalScorer()
    s = sc.score_technical(_ind(trend=1.0, rsi=50.0, close=100.0, ema_12=90.0))
    assert s is not None and s > 0


def test_rsi_overbought_deducts() -> None:
    sc = SignalScorer()
    base = sc.score_technical(_ind(trend=0.0, rsi=50.0, close=100.0, ema_12=100.0))
    hot = sc.score_technical(_ind(trend=0.0, rsi=76.0, close=100.0, ema_12=100.0))
    assert base is not None and hot is not None
    assert hot < base


def test_rsi_oversold_adds() -> None:
    sc = SignalScorer()
    base = sc.score_technical(_ind(trend=0.0, rsi=50.0, close=100.0, ema_12=100.0))
    cold = sc.score_technical(_ind(trend=0.0, rsi=20.0, close=100.0, ema_12=100.0))
    assert base is not None and cold is not None
    assert cold > base


def test_buy_candidate_threshold() -> None:
    sc = SignalScorer()
    # 추세 +60, 종가>EMA12 +10 → 70
    tech = sc.score_technical(_ind(trend=1.0, rsi=50.0, close=100.0, ema_12=90.0))
    final = sc.score_final(tech, None, None)
    assert final is not None
    assert sc.decide_action(final) == "BUY_CANDIDATE"


def test_sell_candidate_threshold() -> None:
    sc = SignalScorer()
    tech = sc.score_technical(_ind(trend=-1.0, rsi=50.0, close=80.0, ema_12=90.0))
    final = sc.score_final(tech, None, None)
    assert final is not None
    assert sc.decide_action(final) == "SELL_CANDIDATE"


def test_insufficient_data_action() -> None:
    sc = SignalScorer()
    ind = TechnicalIndicator(
        symbol="X",
        trade_date="2024-01-01",
        close=None,
        ema_12=None,
        ema_26=None,
        rsi_14=None,
        trend_score=None,
        raw={},
    )
    assert sc.score_technical(ind) is None
    assert sc.decide_action(None) == "INSUFFICIENT_DATA"


def test_score_latest_returns_result() -> None:
    sc = SignalScorer()
    rows = [_ind(trend=0.5, rsi=50.0, close=100.0, ema_12=99.0, date=f"2024-01-{i:02d}") for i in range(1, 6)]
    r = sc.score_latest("T", rows)
    assert r is not None
    assert r.signal_date == "2024-01-05"


def test_score_final_with_news_weight() -> None:
    sc = SignalScorer()
    assert sc.score_final(100.0, None, None) == 100.0
    blended = sc.score_final(100.0, 0.0, None)
    assert blended is not None
    assert abs(blended - 75.0) < 1e-6
    blended2 = sc.score_final(100.0, 100.0, -100.0)
    assert blended2 is not None
    assert abs(blended2 - 60.0) < 1e-6


def test_score_final_tech_macro_only() -> None:
    sc = SignalScorer()
    b = sc.score_final(100.0, None, 0.0)
    assert b is not None and abs(b - 75.0) < 1e-6


def test_score_latest_with_news_score_sets_fields_and_final() -> None:
    sc = SignalScorer()
    rows = [_ind(trend=1.0, rsi=50.0, close=100.0, ema_12=90.0, date="2024-01-05")]
    tech = sc.score_technical(rows[-1])
    assert tech is not None
    r = sc.score_latest("T", rows, news_score=0.0, extra_raw={"news_sentiment": {"news_count": 2}})
    assert r.news_score == 0.0
    assert r.final_score == sc.score_final(tech, 0.0, None)
    assert "뉴스 감성 점수 0.00 반영" in r.reason
    assert r.raw.get("news_sentiment", {}).get("news_count") == 2


def test_score_latest_news_score_none_matches_technical_only() -> None:
    sc = SignalScorer()
    rows = [_ind(trend=0.5, rsi=50.0, close=100.0, ema_12=99.0, date="2024-01-03")]
    base = sc.score_latest("T", rows)
    with_news_none = sc.score_latest("T", rows, news_score=None)
    assert base is not None and with_news_none is not None
    assert with_news_none.news_score is None
    assert with_news_none.final_score == base.final_score
    assert "뉴스 감성 점수" not in with_news_none.reason


def test_score_latest_macro_in_final_and_reason() -> None:
    sc = SignalScorer()
    rows = [_ind(trend=1.0, rsi=50.0, close=100.0, ema_12=90.0, date="2024-01-05")]
    tech = sc.score_technical(rows[-1])
    assert tech is not None
    r = sc.score_latest("T", rows, news_score=None, macro_score=50.0)
    assert r.macro_score == 50.0
    assert r.final_score == sc.score_final(tech, None, 50.0)
    assert "거시 점수 50.00 반영" in r.reason


def test_score_final_three_factors_normalized() -> None:
    sc = SignalScorer()
    # 0.6*100 + 0.2*50 + 0.2*(-50) = 60 + 10 - 10 = 60
    assert abs(sc.score_final(100.0, 50.0, -50.0) - 60.0) < 1e-6
