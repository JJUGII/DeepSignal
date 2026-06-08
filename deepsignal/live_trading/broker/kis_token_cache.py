"""KIS OAuth access token file cache.

Only the access token and non-secret metadata are stored. App secret and
account identifiers must never be persisted here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class KISTokenCacheEntry:
    access_token: str
    token_type: str | None
    expires_at: str
    env: str
    app_key_hash: str


def _app_key_hash(app_key: str) -> str:
    return hashlib.sha256(str(app_key).encode("utf-8")).hexdigest()


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_expires_at(value: str) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def get_default_token_cache_path() -> Path:
    """Default local cache path. `outputs/` is ignored by git."""
    return Path("outputs") / ".kis_token_cache.json"


def load_cached_token(
    path: str | Path,
    env: str,
    app_key: str,
    *,
    min_ttl_seconds: int = 120,
) -> KISTokenCacheEntry | None:
    """Return a valid matching cache entry, otherwise `None`."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    token = raw.get("access_token")
    expires_at_raw = raw.get("expires_at")
    cached_env = str(raw.get("env") or "").strip().lower()
    cached_hash = str(raw.get("app_key_hash") or "").strip()
    if not isinstance(token, str) or not token.strip():
        return None
    if cached_env != str(env or "").strip().lower():
        return None
    if cached_hash != _app_key_hash(app_key):
        return None

    expires_at = _parse_expires_at(str(expires_at_raw or ""))
    if expires_at is None:
        return None
    if expires_at <= _now_utc() + timedelta(seconds=max(0, int(min_ttl_seconds))):
        return None

    token_type_raw = raw.get("token_type")
    token_type = str(token_type_raw).strip() if token_type_raw is not None and str(token_type_raw).strip() else None
    return KISTokenCacheEntry(
        access_token=token,
        token_type=token_type,
        expires_at=expires_at.isoformat(),
        env=cached_env,
        app_key_hash=cached_hash,
    )


def save_cached_token(
    path: str | Path,
    token: str,
    expires_in: int,
    env: str,
    app_key: str,
    *,
    token_type: str | None = None,
) -> KISTokenCacheEntry:
    """Persist an access token without app secret or account identifiers."""
    expires_seconds = max(60, int(expires_in))
    expires_at = _now_utc() + timedelta(seconds=expires_seconds)
    entry = KISTokenCacheEntry(
        access_token=str(token),
        token_type=str(token_type).strip() if token_type is not None and str(token_type).strip() else None,
        expires_at=expires_at.isoformat(),
        env=str(env or "").strip().lower(),
        app_key_hash=_app_key_hash(app_key),
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_text(json.dumps(asdict(entry), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return entry


def clear_cached_token(path: str | Path) -> None:
    p = Path(path)
    try:
        p.unlink()
    except FileNotFoundError:
        return
