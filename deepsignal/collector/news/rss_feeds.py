"""기본 RSS 피드 목록 (API 키·로그인 불필요 공개 피드). 환경 변수로 덮어쓸 수 있다."""

from __future__ import annotations

# (논리적 소스 이름, RSS URL)
DEFAULT_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("yahoo_finance", "https://finance.yahoo.com/news/rssindex"),
    ("marketwatch", "http://feeds.marketwatch.com/marketwatch/topstories/"),
    # 코인 전문 피드 (감성/악재 분석용)
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("decrypt", "https://decrypt.co/feed"),
    ("cryptoslate", "https://cryptoslate.com/feed/"),
)
