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
    # 한국 주식 뉴스 (LLM 촉매 분석용 — 2026-06 실측 생존 피드)
    # yna_market = 연합 증권/시장(120건, [특징주] 포함), yna_economy = 경제 전반
    ("yna_market", "https://www.yna.co.kr/rss/market.xml"),
    ("yna_economy", "https://www.yna.co.kr/rss/economy.xml"),
    ("hankyung_finance", "https://www.hankyung.com/feed/finance"),
    ("mk_stock", "https://www.mk.co.kr/rss/30100041/"),
)
