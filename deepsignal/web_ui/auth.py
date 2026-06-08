"""Telegram WebApp initData 검증 + 세션 토큰 관리."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl


# ──────────────────────────────────────────
# Telegram WebApp initData 검증
# ──────────────────────────────────────────

def verify_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Telegram WebApp initData HMAC-SHA256 검증.
    성공 시 user dict 반환, 실패/만료 시 None.
    """
    if not init_data or not bot_token:
        return None

    params = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = params.pop("hash", None)
    if not received_hash:
        return None

    # auth_date 24시간 유효성
    try:
        auth_date = int(params.get("auth_date", 0))
    except ValueError:
        return None
    if time.time() - auth_date > 86400:
        return None

    # data-check-string: 알파벳 순 정렬 key=value\n...
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))

    # Secret key = HMAC-SHA256(key="WebAppData", data=bot_token)
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    # Expected hash
    expected = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, expected):
        return None

    user_str = params.get("user", "{}")
    try:
        user: dict = json.loads(user_str)
    except Exception:
        user = {}
    return user


# ──────────────────────────────────────────
# 세션 토큰 (HMAC-signed, no DB)
# ──────────────────────────────────────────

def _session_secret(bot_token: str) -> bytes:
    return hmac.new(b"DeepSignalSession", bot_token.encode(), hashlib.sha256).digest()


def create_session_token(user_id: int, bot_token: str, hours: int = 24) -> str:
    """user_id:expires:sig 형식의 세션 토큰 생성."""
    expires = int(time.time()) + hours * 3600
    payload = f"{user_id}:{expires}"
    sig = hmac.new(_session_secret(bot_token), payload.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{payload}:{sig}"


def verify_session_token(token: str, bot_token: str) -> int | None:
    """유효한 세션이면 user_id 반환, 아니면 None."""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        user_id_str, expires_str, sig = parts
        user_id = int(user_id_str)
        expires = int(expires_str)
    except (ValueError, AttributeError):
        return None

    if time.time() > expires:
        return None

    payload = f"{user_id}:{expires}"
    expected_sig = hmac.new(_session_secret(bot_token), payload.encode(), hashlib.sha256).hexdigest()[:24]
    if not hmac.compare_digest(sig, expected_sig):
        return None

    return user_id


# ──────────────────────────────────────────
# 환경변수 헬퍼
# ──────────────────────────────────────────

def get_auth_config() -> dict:
    return {
        "bot_token":    os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "").strip(),
        "allowed_id":   int(os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "0") or 0),
        "require_auth": os.getenv("DEEPSIGNAL_WEBUI_REQUIRE_AUTH", "false").lower() == "true",
        "session_hours": int(os.getenv("DEEPSIGNAL_WEBUI_SESSION_HOURS", "24") or 24),
        "public_url":   os.getenv("DEEPSIGNAL_WEBUI_PUBLIC_URL", "").strip(),
    }
