"""애플리케이션 설정. 비밀값은 환경 변수(.env)로만 주입한다."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_DEFAULT_DB_PATH = "data/deepsignal.db"
_DEFAULT_MARKET_SYMBOLS: tuple[str, ...] = ("AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PROJECT_ROOT / ".env"


def _env_str(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value


def _parse_rss_feeds_json(raw: Optional[str]) -> Optional[tuple[tuple[str, str], ...]]:
    """
    RSS_FEEDS_JSON 환경 변수 파싱.
    형식: [["소스이름","https://..."], ...] JSON 배열.
    """
    if raw is None or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    out: list[tuple[str, str]] = []
    for row in data:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            name, url = str(row[0]).strip(), str(row[1]).strip()
            if name and url:
                out.append((name, url))
    if not out:
        return None
    return tuple(out)


def _parse_market_symbols(raw: Optional[str]) -> tuple[str, ...]:
    """MARKET_SYMBOLS: 쉼표 구분 티커 목록."""
    if raw is None or not str(raw).strip():
        return _DEFAULT_MARKET_SYMBOLS
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    return tuple(parts) if parts else _DEFAULT_MARKET_SYMBOLS


def _parse_notify_on_failure(raw: Optional[str]) -> bool:
    if raw is None or not str(raw).strip():
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _parse_notify_timeout_seconds(raw: Optional[str]) -> int:
    if raw is None or not str(raw).strip():
        return 5
    try:
        v = int(str(raw).strip())
    except ValueError:
        return 5
    return max(1, min(v, 300))


@dataclass(frozen=True)
class Settings:
    """런타임 설정. API 키는 모두 optional."""

    db_path: str
    fred_api_key: Optional[str] = None
    korea_bank_api_key: Optional[str] = None
    kis_app_key: Optional[str] = None
    kis_app_secret: Optional[str] = None
    openai_api_key: Optional[str] = None
    rss_feeds: Optional[tuple[tuple[str, str], ...]] = None
    market_symbols: tuple[str, ...] = _DEFAULT_MARKET_SYMBOLS
    market_period: str = "1mo"
    market_interval: str = "1d"
    notify_on_failure: bool = False
    webhook_url: Optional[str] = None
    notify_timeout_seconds: int = 5


def load_settings() -> Settings:
    """.env 및 프로세스 환경에서 설정을 로드한다."""
    load_dotenv(dotenv_path=_ENV_FILE)
    db_path = os.getenv("DB_PATH", _DEFAULT_DB_PATH)
    rss_feeds = _parse_rss_feeds_json(os.getenv("RSS_FEEDS_JSON"))
    market_symbols = _parse_market_symbols(os.getenv("MARKET_SYMBOLS"))
    market_period = (os.getenv("MARKET_PERIOD") or "1mo").strip() or "1mo"
    market_interval = (os.getenv("MARKET_INTERVAL") or "1d").strip() or "1d"
    notify_on_failure = _parse_notify_on_failure(os.getenv("NOTIFY_ON_FAILURE"))
    webhook_url = _env_str("WEBHOOK_URL")
    notify_timeout_seconds = _parse_notify_timeout_seconds(
        os.getenv("NOTIFY_TIMEOUT_SECONDS")
    )
    return Settings(
        db_path=db_path,
        fred_api_key=_env_str("FRED_API_KEY"),
        korea_bank_api_key=_env_str("KOREA_BANK_API_KEY"),
        kis_app_key=_env_str("KIS_APP_KEY"),
        kis_app_secret=_env_str("KIS_APP_SECRET"),
        openai_api_key=_env_str("OPENAI_API_KEY"),
        rss_feeds=rss_feeds,
        market_symbols=market_symbols,
        market_period=market_period,
        market_interval=market_interval,
        notify_on_failure=notify_on_failure,
        webhook_url=webhook_url,
        notify_timeout_seconds=notify_timeout_seconds,
    )
