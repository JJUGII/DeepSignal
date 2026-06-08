from __future__ import annotations

from pathlib import Path

import pytest

from deepsignal.live_trading.telegram_progress_notify import (
    is_menu_scan_in_progress,
    prepare_menu_scan_lock,
    record_progress_notify,
    release_menu_scan_lock,
    should_send_progress_notify,
    try_acquire_menu_scan_lock,
)


def test_progress_notify_throttle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_PROGRESS_NOTIFY", "true")
    monkeypatch.setenv("TELEGRAM_PROGRESS_NOTIFY_MIN_SECONDS", "300")
    assert should_send_progress_notify(tmp_path, "crypto_recommend")
    record_progress_notify(tmp_path, "crypto_recommend")
    assert not should_send_progress_notify(tmp_path, "crypto_recommend")


def test_menu_scan_lock(tmp_path: Path) -> None:
    assert try_acquire_menu_scan_lock(tmp_path, "crypto_recommend")
    assert is_menu_scan_in_progress(tmp_path, "crypto_recommend")
    assert not try_acquire_menu_scan_lock(tmp_path, "crypto_recommend")
    release_menu_scan_lock(tmp_path, "crypto_recommend")
    assert not is_menu_scan_in_progress(tmp_path, "crypto_recommend")
    assert try_acquire_menu_scan_lock(tmp_path, "crypto_recommend")


def test_stale_lock_cleared_and_reacquired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_MENU_SCAN_STALE_SECONDS", "90")
    assert try_acquire_menu_scan_lock(tmp_path, "crypto_recommend")
    import time

    lock = tmp_path / ".menu_scan_crypto_recommend.lock"
    lock.write_text(
        '{"key": "crypto_recommend", "started_at_ts": '
        + str(time.time() - 120)
        + "}\n",
        encoding="utf-8",
    )
    assert prepare_menu_scan_lock(tmp_path, "crypto_recommend") == "acquired"
    release_menu_scan_lock(tmp_path, "crypto_recommend")
