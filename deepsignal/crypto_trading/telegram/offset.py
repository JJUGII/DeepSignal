"""Persistent Telegram getUpdates offset (approval callbacks + menu messages)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OFFSET_FILE = "CRYPTO_TELEGRAM_OFFSET.json"


def offset_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / OFFSET_FILE


def _read_offset_doc(output_dir: str | Path) -> dict[str, Any]:
    path = offset_path(output_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_telegram_offset(output_dir: str | Path) -> int | None:
    """Offset for getUpdates (next update_id to pass)."""
    data = _read_offset_doc(output_dir)
    for key in ("last_update_id", "offset", "message_offset", "callback_offset"):
        raw = data.get(key)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
    return None


def save_telegram_offset(output_dir: str | Path, offset: int) -> None:
    """Persist unified offset; legacy keys mirror last_update_id."""
    path = offset_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    off = int(offset)
    doc = {
        "last_update_id": off,
        "callback_offset": off,
        "message_offset": off,
        "offset": off,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def acknowledge_update(output_dir: str | Path, update_id: int) -> int:
    """Persist offset so long-running handlers are not re-delivered on the next poll."""
    current = load_telegram_offset(output_dir) or 0
    next_off = max(int(current), int(update_id) + 1)
    save_telegram_offset(output_dir, next_off)
    return next_off


def advance_offset_from_updates(output_dir: str | Path, updates: list[dict[str, Any]]) -> int | None:
    if not updates:
        return load_telegram_offset(output_dir)
    next_off = max(int(u.get("update_id", 0)) for u in updates) + 1
    save_telegram_offset(output_dir, next_off)
    return next_off
