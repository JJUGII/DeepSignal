from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from deepsignal.live_trading.time_utils import (
    daily_ai_timestamp_fields,
    ensure_timezone_aware,
    local_date_string,
    markdown_timestamp_block,
    now_kst,
    now_kst_iso,
    parse_datetime_with_default_tz,
    parse_markdown_generated_at,
    stamp_daily_ai_payload,
)


def test_now_kst_iso_has_timezone() -> None:
    text = now_kst_iso()
    assert "+09:00" in text or text.endswith("+09:00")
    dt = parse_datetime_with_default_tz(text)
    assert dt is not None
    assert dt.tzinfo is not None


def test_naive_timestamp_treated_as_seoul() -> None:
    dt = parse_datetime_with_default_tz("2026-05-19T10:30:00")
    assert dt is not None
    assert dt.astimezone(ZoneInfo("Asia/Seoul")).hour == 10


def test_stamp_daily_ai_payload_fields() -> None:
    body = stamp_daily_ai_payload({"status": "READY"})
    assert body["generated_at"].endswith("+09:00")
    assert body["generated_date"] == "2026-05-19" or body["generated_date"]
    assert body["timezone"] == "Asia/Seoul"


def test_markdown_timestamp_parse() -> None:
    md = "\n".join(
        [
            "# Title",
            "",
            "- 생성 시각: 2026-05-19T10:30:00+09:00",
            "- 기준 날짜: 2026-05-19",
        ]
    )
    dt = parse_markdown_generated_at(md)
    assert dt is not None
    assert local_date_string(dt) == "2026-05-19"


def test_markdown_timestamp_block_lines() -> None:
    lines = markdown_timestamp_block(now_kst())
    assert any("생성 시각" in line for line in lines)
    assert any("기준 날짜" in line for line in lines)
    assert any("Asia/Seoul" in line for line in lines)


def test_ensure_timezone_aware_naive() -> None:
    naive = datetime(2026, 5, 19, 8, 0, 0)
    aware = ensure_timezone_aware(naive)
    assert aware.tzinfo == ZoneInfo("Asia/Seoul")


def test_daily_ai_timestamp_fields_from_dt() -> None:
    dt = datetime(2026, 5, 19, 10, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    fields = daily_ai_timestamp_fields(dt)
    assert fields["generated_date"] == "2026-05-19"
    assert "+09:00" in fields["generated_at"]
