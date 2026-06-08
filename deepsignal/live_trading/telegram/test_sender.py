"""Minimal Telegram connection test ([긴급-MVP]).

Dry-run by default; ``--send`` calls Telegram ``sendMessage`` only.
No KIS, live-approve, or order execution.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from deepsignal.live_trading.telegram_approval import telegram_api_post
from deepsignal.live_trading.time_utils import now_kst, stamp_daily_ai_payload

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # telegram/ → live_trading/ → deepsignal/ → project root
_ENV_FILE = _PROJECT_ROOT / ".env"


def _ensure_dotenv() -> None:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_ENV_FILE)


def load_telegram_notify_env() -> tuple[str | None, str | None, list[str]]:
    _ensure_dotenv()
    bot = str(os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN") or "").strip() or None
    chat = str(os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID") or "").strip() or None
    errors: list[str] = []
    if not bot:
        errors.append("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN is not set")
    if not chat:
        errors.append("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID is not set")
    return bot, chat, errors


def run_telegram_test(
    *,
    message: str,
    send: bool,
    output_dir: str | Path = "outputs",
    timeout_seconds: float = 5.0,
) -> tuple[dict[str, Any], Path]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    bot, chat, env_errors = load_telegram_notify_env()
    text = str(message or "").strip() or "DeepSignal 연결 테스트"

    body: dict[str, Any] = {
        "message": text,
        "send": bool(send),
        "bot_token_configured": bool(bot),
        "chat_id_configured": bool(chat),
        "env_errors": list(env_errors),
        "network_called": False,
        "kis_post_called": False,
        "live_execution_called": False,
    }

    if send:
        if env_errors:
            body["status"] = "failed"
            body["telegram_result"] = {"ok": False, "status": "missing_config", "network_called": False}
        else:
            body["telegram_result"] = telegram_api_post(
                "sendMessage",
                {"chat_id": chat, "text": text},
                bot_token=bot,
                timeout_seconds=timeout_seconds,
            )
            body["network_called"] = bool(body["telegram_result"].get("network_called"))
            body["status"] = "success" if body["telegram_result"].get("ok") else "failed"
    else:
        body["status"] = "dry_run"
        body["telegram_result"] = {
            "status": "dry_run",
            "network_called": False,
            "would_send": {
                "chat_id": "(configured)" if chat else "(missing)",
                "text": text,
            },
        }

    path = root / f"telegram_test_{now_kst().strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(stamp_daily_ai_payload(body), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return body, path


def format_telegram_test_console(body: dict[str, Any], json_path: Path) -> str:
    lines = [
        f"JSON: {json_path.as_posix()}",
        f"Status: {body.get('status')}",
    ]
    if body.get("status") == "dry_run":
        lines.append("Telegram test dry-run (no network call)")
        if body.get("env_errors"):
            lines.extend(f"- {err}" for err in body["env_errors"])
        else:
            lines.append("Env: bot token and chat id configured")
    elif body.get("status") == "success":
        lines.append("Telegram 연결 성공")
    else:
        lines.append("Telegram 연결 실패")
        for err in body.get("env_errors") or []:
            lines.append(f"- {err}")
        result = body.get("telegram_result") if isinstance(body.get("telegram_result"), dict) else {}
        if result.get("description"):
            lines.append(f"- {result['description']}")
        elif result.get("status"):
            lines.append(f"- {result['status']}")
    return "\n".join(lines)
