"""키워드 기반 SentimentAnalyzer v1."""

from __future__ import annotations

from deepsignal.analyzer.sentiment.sentiment_analyzer import SentimentAnalyzer


def test_analyze_text_positive() -> None:
    s, r = SentimentAnalyzer().analyze_text("Company posts strong profit growth")
    assert s == 1.0
    assert "긍정" in r


def test_analyze_text_negative() -> None:
    s, r = SentimentAnalyzer().analyze_text("Shares plunge after downgrade warning")
    assert s == -1.0
    assert "부정" in r


def test_analyze_text_neutral() -> None:
    s, r = SentimentAnalyzer().analyze_text("Markets closed on holiday")
    assert s == 0.0
    assert "없음" in r or "중립" in r


def test_analyze_news_items_average_and_confidence() -> None:
    rows = [
        {"title": "rally and surge", "summary": ""},
        {"title": "neutral headline", "summary": "nothing special"},
        {"title": "loss and lawsuit", "summary": "bearish tone"},
    ]
    res = SentimentAnalyzer().analyze_news_items("MSFT", rows)
    assert res.news_count == 3
    assert res.positive_count == 1
    assert res.negative_count == 1
    assert res.neutral_count == 1
    assert res.news_score is not None
    assert abs(res.news_score - 0.0) < 1e-6
    assert res.confidence is not None
    assert abs(res.confidence - (2 / 3)) < 1e-6


def test_analyze_news_items_empty() -> None:
    r = SentimentAnalyzer().analyze_news_items("AAPL", [])
    assert r.news_score is None
    assert r.confidence is None
    assert r.news_count == 0
    assert "없습니다" in r.reason
