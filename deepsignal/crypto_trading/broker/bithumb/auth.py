"""Bithumb Open API v2 JWT (HS256) without PyJWT dependency."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any
from urllib.parse import urlencode


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_bithumb_jwt(
    access_key: str,
    secret_key: str,
    *,
    query: dict[str, Any] | None = None,
    nonce: str | None = None,
    timestamp_ms: int | None = None,
) -> str:
    payload: dict[str, Any] = {
        "access_key": access_key,
        "nonce": nonce if nonce is not None else str(uuid.uuid4()),
        "timestamp": timestamp_ms if timestamp_ms is not None else round(time.time() * 1000),
    }
    if query:
        qs = urlencode(query, doseq=True).encode("utf-8")
        digest = hashlib.sha512()
        digest.update(qs)
        payload["query_hash"] = digest.hexdigest()
        payload["query_hash_alg"] = "SHA512"
    header = {"alg": "HS256", "typ": "JWT"}
    segments = [
        _b64url(json.dumps(header, separators=(",", ":")).encode()),
        _b64url(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    signing_input = ".".join(segments).encode("ascii")
    sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    segments.append(_b64url(sig))
    return ".".join(segments)


def authorization_header(
    access_key: str,
    secret_key: str,
    *,
    query: dict[str, Any] | None = None,
) -> dict[str, str]:
    token = build_bithumb_jwt(access_key, secret_key, query=query)
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}
