"""Optional Telegram alerts for listing watch (read-only, no auto-buy)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.listing.config import LISTING_ALERT_COOLDOWN_JSON, ListingWatchConfig
from deepsignal.crypto_trading.listing.models import ListingCandidate


def _load_cooldown(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {str(k): float(v) for k, v in raw.items()}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _save_cooldown(path: Path, data: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def maybe_send_listing_alerts(
    output_dir: str | Path,
    candidates: list[ListingCandidate],
    cfg: ListingWatchConfig,
) -> list[str]:
    """Send Telegram for high-score candidates; returns markets alerted."""
    if not cfg.telegram_alert:
        return []

    try:
        from deepsignal.crypto_trading.crypto_telegram_flow import (
            load_crypto_telegram_config_from_env,
            telegram_send_plain,
        )
    except ImportError:
        return []

    tg = load_crypto_telegram_config_from_env()
    if not tg.bot_token or not tg.allowed_chat_id:
        return []

    cooldown_path = Path(output_dir) / LISTING_ALERT_COOLDOWN_JSON
    cooldown = _load_cooldown(cooldown_path)
    now = time.time()
    sent: list[str] = []

    for c in candidates:
        if c.anomaly_score < cfg.anomaly_score_alert:
            continue
        last = cooldown.get(c.market, 0.0)
        if now - last < cfg.telegram_cooldown_sec:
            continue

        vol_txt = f"{c.vol_ratio:.1f}x" if c.vol_ratio is not None else "-"
        chg1h = f"{c.chg_1h_pct:+.1f}%" if c.chg_1h_pct is not None else "-"
        tag_txt = ", ".join(c.tags[:4]) if c.tags else "-"
        text = (
            f"[상장감시] {c.display_name} ({c.market})\n"
            f"거래소: {c.exchange_side} | 점수 {c.anomaly_score:.0f}\n"
            f"24h {c.signed_change_rate:+.1f}% | 1h {chg1h} | 거래량비 {vol_txt}\n"
            f"24h거래대금 {c.acc_trade_price_24h:,.0f}원\n"
            f"태그: {tag_txt}\n"
            f"※ 조회·알림 전용 (자동매수 없음)"
        )
        try:
            telegram_send_plain(tg, text)
        except Exception:
            continue
        cooldown[c.market] = now
        sent.append(c.market)

    if sent:
        _save_cooldown(cooldown_path, cooldown)
    return sent
