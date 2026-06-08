"""RSS 뉴스 수집 (공개 피드, API 키 불필요)."""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from typing import Optional

import feedparser

from deepsignal.collector.news.news_item import NewsItem
from deepsignal.collector.news.rss_feeds import DEFAULT_RSS_FEEDS

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 30
_USER_AGENT = "DeepSignal/0.1 (news collector; +https://github.com/local/deepsignal)"


class NewsCollector:
    """RSS 기반 뉴스 수집."""

    def __init__(self, feeds: Optional[Sequence[tuple[str, str]]] = None) -> None:
        self._feeds: tuple[tuple[str, str], ...] = (
            tuple(feeds) if feeds else DEFAULT_RSS_FEEDS
        )

    def iter_sources(self) -> Iterator[tuple[str, str]]:
        yield from self._feeds

    def fetch_source(self, source_name: str, rss_url: str) -> tuple[list[NewsItem], str | None]:
        """
        단일 RSS 소스를 가져온다.

        Returns:
            (items, error_message) — 전체 실패 시 items는 빈 리스트, error_message는 사유.
        """
        items: list[NewsItem] = []
        try:
            req = urllib.request.Request(
                rss_url,
                headers={"User-Agent": _USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT_SEC) as resp:
                data = resp.read()
        except (TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("RSS fetch failed source=%s url=%s err=%s", source_name, rss_url, exc)
            return [], str(exc)
        try:
            parsed = feedparser.parse(data)
        except Exception as exc:  # noqa: BLE001 — 파서 예외 포괄
            logger.warning("RSS parse failed source=%s err=%s", source_name, exc)
            return [], str(exc)

        if getattr(parsed, "bozo", False) and not getattr(parsed, "entries", None):
            bozo_msg = str(getattr(parsed, "bozo_exception", "parse error"))
            logger.warning("RSS bozo source=%s msg=%s", source_name, bozo_msg)
            return [], bozo_msg

        for entry in getattr(parsed, "entries", []) or []:
            try:
                items.append(NewsItem.from_rss_entry(source_name, entry))
            except Exception as exc:  # noqa: BLE001
                logger.warning("RSS entry skip source=%s err=%s", source_name, exc)
        return items, None

    def collect_per_source(self) -> Iterator[tuple[str, list[NewsItem], str | None]]:
        """소스별로 (이름, 기사 목록, 오류)를 순회한다."""
        for source_name, rss_url in self._feeds:
            batch, err = self.fetch_source(source_name, rss_url)
            yield source_name, batch, err

    def collect(self) -> list[NewsItem]:
        """등록된 모든 소스에서 뉴스를 수집한다. 소스별 실패는 로그만 남기고 계속한다."""
        out: list[NewsItem] = []
        for _, batch, err in self.collect_per_source():
            if err:
                logger.info("RSS source error items=%d err=%s", len(batch), err)
            out.extend(batch)
        return out
