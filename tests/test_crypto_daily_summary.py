"""Crypto daily Telegram summary schedule."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from zoneinfo import ZoneInfo

from deepsignal.crypto_trading.crypto_recommendation_outcomes import (
    CRYPTO_DAILY_SUMMARY_HOUR_KST,
    is_crypto_daily_summary_time,
    maybe_send_crypto_daily_summary,
)


def test_daily_summary_hour_constant() -> None:
    assert CRYPTO_DAILY_SUMMARY_HOUR_KST == 21


def test_is_crypto_daily_summary_time() -> None:
    kst = ZoneInfo("Asia/Seoul")
    assert is_crypto_daily_summary_time(datetime(2026, 5, 25, 21, 5, tzinfo=kst))
    assert not is_crypto_daily_summary_time(datetime(2026, 5, 25, 13, 0, tzinfo=kst))


def test_maybe_send_only_in_21h_window(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = MagicMock(bot_token="tok", send=True)
    state: dict = {}
    broker = MagicMock()
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_recommendation_outcomes.is_crypto_daily_summary_time",
        lambda now=None: False,
    )
    assert maybe_send_crypto_daily_summary(broker, cfg, outcomes_db="outputs", runner_state=state) is None

    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_recommendation_outcomes.is_crypto_daily_summary_time",
        lambda now=None: True,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_recommendation_outcomes.build_crypto_daily_telegram_summary",
        lambda *a, **k: "daily",
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_telegram_flow.telegram_send_plain",
        lambda *a, **k: {"ok": True},
    )
    out = maybe_send_crypto_daily_summary(broker, cfg, outcomes_db="outputs", runner_state=state)
    assert out is not None
    assert out.get("summary_sent") is True
