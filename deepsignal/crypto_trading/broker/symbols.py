"""내부 표준 마켓(KRW-BTC) ↔ 거래소별 심볼 변환."""

from __future__ import annotations

import re

_INTERNAL_RE = re.compile(r"^KRW-([A-Z0-9]+)$")
_BITHUMB_RE = re.compile(r"^([A-Z0-9]+)_KRW$")


def normalize_market(market: str) -> str:
    """KRW-BTC 형식으로 정규화. BTC_KRW / btc-krw 등도 수용."""
    raw = str(market or "").strip().upper()
    if not raw:
        raise ValueError("empty market")
    if _INTERNAL_RE.match(raw):
        return raw
    m = _BITHUMB_RE.match(raw)
    if m:
        return f"KRW-{m.group(1)}"
    if raw.startswith("KRW-"):
        return raw
    if "_" in raw:
        base, quote = raw.split("_", 1)
        if quote == "KRW":
            return f"KRW-{base}"
    return f"KRW-{raw.replace('-', '')}"


def to_upbit_market(market: str) -> str:
    return normalize_market(market)


def to_bithumb_market(market: str) -> str:
    internal = normalize_market(market)
    base = internal.split("-", 1)[1]
    return f"{base}_KRW"


def currency_from_market(market: str) -> str:
    return normalize_market(market).split("-", 1)[1]
