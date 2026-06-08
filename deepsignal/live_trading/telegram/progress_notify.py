"""Throttle Telegram 'scan in progress' messages and prevent duplicate menu scans."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

STATE_FILE = "TELEGRAM_PROGRESS_NOTIFY.json"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def progress_notify_enabled() -> bool:
    raw = (os.environ.get("TELEGRAM_PROGRESS_NOTIFY") or "true").strip()
    return _truthy(raw)


def progress_notify_min_seconds() -> float:
    raw = (os.environ.get("TELEGRAM_PROGRESS_NOTIFY_MIN_SECONDS") or "300").strip()
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 300.0


def _state_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / STATE_FILE


def _read_state(output_dir: str | Path) -> dict[str, Any]:
    path = _state_path(output_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(output_dir: str | Path, data: dict[str, Any]) -> None:
    path = _state_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def should_send_progress_notify(output_dir: str | Path, key: str) -> bool:
    if not progress_notify_enabled():
        return False
    state = _read_state(output_dir)
    last = state.get(key) or {}
    try:
        sent_at = float(last.get("sent_at_ts") or 0.0)
    except (TypeError, ValueError):
        sent_at = 0.0
    return (time.time() - sent_at) >= progress_notify_min_seconds()


def record_progress_notify(output_dir: str | Path, key: str) -> None:
    state = _read_state(output_dir)
    state[key] = {"sent_at_ts": time.time()}
    _write_state(output_dir, state)


def _lock_path(output_dir: str | Path, key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
    return Path(output_dir) / f".menu_scan_{safe}.lock"


def menu_scan_lock_max_seconds() -> float:
    raw = (os.environ.get("TELEGRAM_MENU_SCAN_LOCK_SECONDS") or "600").strip()
    try:
        return max(60.0, float(raw))
    except ValueError:
        return 600.0


def menu_scan_stale_seconds() -> float:
    """Locks older than this are treated as crashed/hung scans and cleared."""
    raw = (os.environ.get("TELEGRAM_MENU_SCAN_STALE_SECONDS") or "90").strip()
    try:
        return max(30.0, min(float(raw), menu_scan_lock_max_seconds()))
    except ValueError:
        return 90.0


def _lock_age_seconds(output_dir: str | Path, key: str) -> float | None:
    path = _lock_path(output_dir, key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return max(0.0, time.time() - float(data.get("started_at_ts") or 0.0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def try_acquire_menu_scan_lock(output_dir: str | Path, key: str) -> bool:
    """Return False when another scan for this key is still in progress."""
    path = _lock_path(output_dir, key)
    now = time.time()
    stale = menu_scan_stale_seconds()
    max_age = menu_scan_lock_max_seconds()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            started = float(data.get("started_at_ts") or 0.0)
            age = now - started
            if age < stale:
                return False
            if age < max_age:
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"key": key, "started_at_ts": now}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def prepare_menu_scan_lock(output_dir: str | Path, key: str) -> str:
    """Return 'acquired' or 'in_progress' (fresh lock held by another request)."""
    age = _lock_age_seconds(output_dir, key)
    if age is not None and age < menu_scan_stale_seconds():
        return "in_progress"
    if try_acquire_menu_scan_lock(output_dir, key):
        return "acquired"
    return "in_progress"


def release_menu_scan_lock(output_dir: str | Path, key: str) -> None:
    path = _lock_path(output_dir, key)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def is_menu_scan_in_progress(output_dir: str | Path, key: str) -> bool:
    age = _lock_age_seconds(output_dir, key)
    return age is not None and age < menu_scan_stale_seconds()
