"""뉴스 단건 모델 및 RSS 정규화."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


def create_source_hash(source: str, url: str, title: str) -> str:
    """
    중복 판별용 해시.
    URL이 비어 있지 않으면 URL 정규화 문자열을 우선 사용하고,
    그렇지 않으면 source + 제목을 사용한다.
    """
    u = (url or "").strip()
    if u:
        key = u.lower()
    else:
        key = f"{source.strip()}|{(title or '').strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _published_to_iso(entry: Mapping[str, Any]) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        ts = time.mktime(parsed)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.isoformat()
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def _pick_summary(entry: Mapping[str, Any]) -> str | None:
    for key in ("summary", "subtitle", "description"):
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _entry_to_raw_dict(entry: Mapping[str, Any]) -> dict[str, Any]:
    """SQLite 저장용 JSON 직렬화 가능한 최소 필드."""
    out: dict[str, Any] = {}
    for k in ("id", "link", "title", "author"):
        v = entry.get(k)
        if v is not None:
            out[k] = v
    return out


@dataclass
class NewsItem:
    """수집된 뉴스 한 건."""

    title: str
    url: str
    source: str
    published_at: str | None
    summary: str | None
    symbol: str | None
    raw: dict[str, Any]
    source_hash: str

    @classmethod
    def from_rss_entry(cls, source: str, entry: Mapping[str, Any]) -> NewsItem:
        title = (entry.get("title") or "").strip() or "(no title)"
        link = entry.get("link")
        url = link.strip() if isinstance(link, str) else ""
        summary = _pick_summary(entry)
        published_at = _published_to_iso(entry)
        raw = _entry_to_raw_dict(entry)
        h = create_source_hash(source, url, title)
        return cls(
            title=title,
            url=url,
            source=source,
            published_at=published_at,
            summary=summary,
            symbol=None,
            raw=raw,
            source_hash=h,
        )
