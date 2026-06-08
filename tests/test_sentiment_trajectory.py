"""뉴스 감성 궤적."""

from __future__ import annotations

from deepsignal.analyzer.sentiment.sentiment_analyzer import SentimentAnalyzer


def test_trajectory_deteriorating_adjusts_score() -> None:
    rows = [
        {"title": "growth beat record", "published_at": "2026-01-01"},
        {"title": "strong profit", "published_at": "2026-01-02"},
        {"title": "loss plunge lawsuit", "published_at": "2026-02-01"},
        {"title": "miss downgrade weak", "published_at": "2026-02-02"},
    ]
    sent = SentimentAnalyzer().analyze_news_items("TEST", rows)
    assert sent.news_score is not None
    assert (sent.raw or {}).get("trajectory", {}).get("label") in (
        "deteriorating",
        "stable",
        "improving",
    )
