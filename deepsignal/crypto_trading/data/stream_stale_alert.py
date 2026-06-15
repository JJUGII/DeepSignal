"""Telegram alert when Binance live_state.json is stale."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_telegram_flow import load_crypto_telegram_config_from_env
from deepsignal.live_trading.time_utils import now_kst

_DEFAULT_STALE_SECONDS = 180.0


def stream_stale_threshold_seconds() -> float:
    raw = os.getenv("CRYPTO_STREAM_STALE_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_STALE_SECONDS
    try:
        return max(30.0, float(raw))
    except ValueError:
        return _DEFAULT_STALE_SECONDS


def live_state_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / "binance_stream" / "live_state.json"


def _parse_generated_at(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_live_state_age_seconds(
    payload: dict[str, Any],
    *,
    path: Path | None = None,
) -> float | None:
    generated_at = _parse_generated_at(str(payload.get("generated_at") or ""))
    if generated_at is not None:
        if generated_at.tzinfo is None:
            age = (now_kst().replace(tzinfo=None) - generated_at).total_seconds()
        else:
            age = (now_kst() - generated_at.astimezone(now_kst().tzinfo)).total_seconds()
        return max(0.0, age)
    if path is not None and path.is_file():
        try:
            return max(0.0, now_kst().timestamp() - path.stat().st_mtime)
        except OSError:
            return None
    return None


def stale_alert_message(*, age_seconds: float, threshold_seconds: float) -> str:
    minutes = max(1, int(round(threshold_seconds / 60.0)))
    return (
        f"⚠️ [DeepSignal] BTC 스트림 stale\n"
        f"- 경과: {age_seconds:.0f}초 (기준 {minutes}분 / {threshold_seconds:.0f}초)\n"
        f"- Binance live_state 갱신을 확인하세요."
    )


def maybe_alert_binance_stream_stale(
    output_dir: str | Path,
    *,
    runner_state: dict[str, Any] | None = None,
    send_telegram: bool = False,
) -> dict[str, Any]:
    path = live_state_path(output_dir)
    threshold = stream_stale_threshold_seconds()
    state = runner_state if runner_state is not None else {}

    if not path.is_file():
        return {
            "stale": True,
            "alert_sent": False,
            "reason": "missing",
            "path": str(path),
            "threshold_seconds": threshold,
        }

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "stale": True,
            "alert_sent": False,
            "reason": f"read_error: {exc}",
            "path": str(path),
            "threshold_seconds": threshold,
        }

    if not isinstance(payload, dict):
        payload = {}

    age = parse_live_state_age_seconds(payload, path=path)
    stale = age is None or age > threshold
    result: dict[str, Any] = {
        "stale": stale,
        "alert_sent": False,
        "age_seconds": age,
        "threshold_seconds": threshold,
        "path": str(path),
        "generated_at": payload.get("generated_at"),
    }
    if not stale:
        return result

    alert_key = "last_stream_stale_alert_at"
    if state.get(alert_key) == str(payload.get("generated_at") or ""):
        result["reason"] = "already_alerted_for_snapshot"
        return result

    message = stale_alert_message(
        age_seconds=float(age or threshold + 1),
        threshold_seconds=threshold,
    )
    if send_telegram:
        from deepsignal.crypto_trading import crypto_stream_stale_alert as alert_mod

        cfg = load_crypto_telegram_config_from_env(output_dir=str(output_dir))
        cfg.send = True
        send_result = alert_mod.telegram_send_plain(cfg, message)
        result["alert_sent"] = bool(send_result.get("ok"))
        result["telegram"] = send_result
        if result["alert_sent"]:
            state[alert_key] = str(payload.get("generated_at") or path.stat().st_mtime)
    return result
