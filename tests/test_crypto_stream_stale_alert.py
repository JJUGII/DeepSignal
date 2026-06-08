"""Binance stream stale Telegram alert."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from deepsignal.crypto_trading.crypto_stream_stale_alert import (
    maybe_alert_binance_stream_stale,
    stale_alert_message,
    stream_stale_threshold_seconds,
)
def test_stale_message_format() -> None:
    msg = stale_alert_message(age_seconds=200.0, threshold_seconds=180.0)
    assert "BTC 스트림 stale" in msg
    assert "3분" in msg


def test_alert_sent_when_stale(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_STREAM_STALE_SECONDS", "180")
    stream_dir = tmp_path / "binance_stream"
    stream_dir.mkdir(parents=True)
    from deepsignal.live_trading.time_utils import now_kst

    dt = now_kst() - timedelta(minutes=5)
    payload = {
        "generated_at": dt.isoformat(),
        "symbols": ["BTCUSDT"],
        "btc": {"symbol": "BTCUSDT", "price": 1.0},
    }
    (stream_dir / "live_state.json").write_text(json.dumps(payload), encoding="utf-8")

    sent: list[str] = []

    def _fake_plain(cfg: object, text: str) -> dict[str, object]:
        sent.append(text)
        return {"ok": True}

    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_stream_stale_alert.telegram_send_plain",
        _fake_plain,
    )
    state: dict = {}
    out = maybe_alert_binance_stream_stale(
        str(tmp_path),
        runner_state=state,
        send_telegram=True,
    )
    assert out["stale"] is True
    assert out["alert_sent"] is True
    assert len(sent) == 1
    assert stream_stale_threshold_seconds() == 180.0
