from __future__ import annotations

import json

from deepsignal.crypto_trading.broker.bithumb.auth import build_bithumb_jwt


def test_bithumb_jwt_has_three_segments() -> None:
    token = build_bithumb_jwt(
        "test-key",
        "test-secret",
        nonce="fixed-nonce",
        timestamp_ms=1712230310689,
    )
    parts = token.split(".")
    assert len(parts) == 3


def test_bithumb_jwt_includes_timestamp_and_query_hash() -> None:
    token = build_bithumb_jwt(
        "test-key",
        "test-secret",
        query={"market": "KRW-BTC"},
        nonce="fixed-nonce",
        timestamp_ms=1712230310689,
    )
    payload_b64 = token.split(".")[1]
    pad = "=" * (-len(payload_b64) % 4)
    import base64

    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
    assert payload["access_key"] == "test-key"
    assert payload["nonce"] == "fixed-nonce"
    assert payload["timestamp"] == 1712230310689
    assert payload["query_hash_alg"] == "SHA512"
    assert len(payload["query_hash"]) == 128
