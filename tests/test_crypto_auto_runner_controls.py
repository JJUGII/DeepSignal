from __future__ import annotations

from deepsignal.crypto_trading.crypto_auto_runner import _excluded_by_cooldown, _state_today
from deepsignal.live_trading.time_utils import now_kst


def test_state_today_resets_daily_fields() -> None:
    state = {
        "daily_key": "1999-01-01",
        "orders_today": 9,
        "buy_krw_today": 99999.0,
        "buy_markets_today": ["KRW-AAA"],
    }
    out = _state_today(state)
    assert out["orders_today"] == 0
    assert out["buy_krw_today"] == 0.0
    assert out["buy_markets_today"] == []


def test_excluded_by_cooldown_filters_recent_market() -> None:
    now_ts = float(now_kst().timestamp())
    state = {
        "last_buy_by_market": {
            "KRW-RENDER": now_ts - (10 * 60),
            "KRW-ERA": now_ts - (400 * 60),
        }
    }
    excluded = _excluded_by_cooldown(state, cooldown_minutes=180)
    assert "KRW-RENDER" in excluded
    assert "KRW-ERA" not in excluded
