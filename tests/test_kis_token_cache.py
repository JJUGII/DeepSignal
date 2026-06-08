from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from deepsignal.live_trading.kis_token_cache import (
    clear_cached_token,
    load_cached_token,
    save_cached_token,
)


def test_save_and_load_cached_token(tmp_path) -> None:
    path = tmp_path / "kis_token_cache.json"
    saved = save_cached_token(
        path,
        "access-token",
        600,
        env="live",
        app_key="app-key",
        token_type="Bearer",
    )

    loaded = load_cached_token(path, env="live", app_key="app-key")
    assert loaded is not None
    assert loaded.access_token == "access-token"
    assert loaded.token_type == "Bearer"
    assert loaded.env == "live"
    assert loaded.app_key_hash == saved.app_key_hash


def test_cache_file_does_not_store_secrets(tmp_path) -> None:
    path = tmp_path / "kis_token_cache.json"
    save_cached_token(path, "tok", 600, env="paper", app_key="app-key")

    raw_text = path.read_text(encoding="utf-8")
    assert "tok" in raw_text
    assert "app-key" not in raw_text
    assert "app-secret" not in raw_text
    assert "12345678" not in raw_text
    data = json.loads(raw_text)
    assert set(data) == {"access_token", "token_type", "expires_at", "env", "app_key_hash"}


def test_cache_mismatch_or_expired_returns_none(tmp_path) -> None:
    path = tmp_path / "kis_token_cache.json"
    save_cached_token(path, "tok", 600, env="live", app_key="app-key")

    assert load_cached_token(path, env="paper", app_key="app-key") is None
    assert load_cached_token(path, env="live", app_key="other-key") is None

    expired_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["expires_at"] = expired_at
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_cached_token(path, env="live", app_key="app-key") is None


def test_clear_cached_token(tmp_path) -> None:
    path = tmp_path / "kis_token_cache.json"
    save_cached_token(path, "tok", 600, env="paper", app_key="app-key")
    assert path.exists()
    clear_cached_token(path)
    assert not path.exists()
    clear_cached_token(path)
