"""Active crypto exchange selection (CRYPTO_BROKER env)."""

from __future__ import annotations

import os

SUPPORTED_CRYPTO_BROKERS = ("upbit", "bithumb")
DEFAULT_CRYPTO_BROKER = "upbit"


def normalize_crypto_broker_name(name: str | None = None) -> str:
    raw = (name or os.environ.get("CRYPTO_BROKER") or DEFAULT_CRYPTO_BROKER).strip().lower()
    if raw in SUPPORTED_CRYPTO_BROKERS:
        return raw
    return DEFAULT_CRYPTO_BROKER


def crypto_broker_label(name: str | None = None) -> str:
    bid = normalize_crypto_broker_name(name)
    return {"upbit": "Upbit", "bithumb": "Bithumb"}.get(bid, bid)
