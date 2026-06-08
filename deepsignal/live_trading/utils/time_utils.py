"""Timezone-aware timestamp helpers for daily AI workflow ([실전-49]).

Uses stdlib ``zoneinfo`` only. No external time APIs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import re
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Seoul"

_MD_GENERATED_AT = re.compile(
    r"^\s*[-*]\s*(?:생성 시각|Generated at)\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)


def now_kst() -> datetime:
    return datetime.now(ZoneInfo(DEFAULT_TZ))


def now_kst_iso() -> str:
    return now_kst().isoformat(timespec="seconds")


def ensure_timezone_aware(dt: datetime, *, default_tz: str = DEFAULT_TZ) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(default_tz))
    return dt


def parse_datetime_with_default_tz(value: Any, *, default_tz: str = DEFAULT_TZ) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ensure_timezone_aware(dt, default_tz=default_tz)


def local_date_string(dt: datetime, *, tz: str = DEFAULT_TZ) -> str:
    return ensure_timezone_aware(dt, default_tz=tz).astimezone(ZoneInfo(tz)).date().isoformat()


def daily_ai_timestamp_fields(dt: datetime | None = None, *, tz: str = DEFAULT_TZ) -> dict[str, str]:
    aware = ensure_timezone_aware(dt or now_kst(), default_tz=tz)
    local = aware.astimezone(ZoneInfo(tz))
    return {
        "generated_at": local.isoformat(timespec="seconds"),
        "generated_date": local.date().isoformat(),
        "timezone": tz,
    }


def stamp_daily_ai_payload(payload: dict[str, Any], *, dt: datetime | None = None, tz: str = DEFAULT_TZ) -> dict[str, Any]:
    """Merge standard daily AI timestamp fields into a JSON payload."""
    body = dict(payload)
    body.update(daily_ai_timestamp_fields(dt, tz=tz))
    return body


def markdown_timestamp_block(dt: datetime | None = None, *, tz: str = DEFAULT_TZ) -> list[str]:
    fields = daily_ai_timestamp_fields(dt, tz=tz)
    return [
        f"- 생성 시각: {fields['generated_at']}",
        f"- 기준 날짜: {fields['generated_date']}",
        f"- 타임존: {fields['timezone']}",
    ]


def parse_markdown_generated_at(text: str, *, default_tz: str = DEFAULT_TZ) -> datetime | None:
    for line in text.splitlines()[:40]:
        match = _MD_GENERATED_AT.match(line)
        if match:
            return parse_datetime_with_default_tz(match.group(1).strip(), default_tz=default_tz)
    return None
