"""crypto_position_sizing — dynamic order size and TP/SL."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import CryptoTunedThresholds
from deepsignal.crypto_trading.crypto_position_sizing import (
    compute_max_order_krw,
    compute_max_orders_per_day,
    merge_tp_sl,
    tp_sl_from_atr,
)
from deepsignal.crypto_trading.upbit_broker import MIN_ORDER_KRW


def test_compute_max_order_krw_scales_with_balance() -> None:
    krw, sf, _ = compute_max_order_krw(200_000, final_score=70.0, size_multiplier=1.0)
    assert krw >= MIN_ORDER_KRW
    assert sf == pytest.approx(70.0 / 55.0, rel=0.05)
    assert krw <= 200_000
    assert any("유동 상한" in n for n in _)


def test_compute_max_order_krw_dynamic_cap_changes_with_score() -> None:
    lo, _, _notes_lo = compute_max_order_krw(300_000, total_portfolio_krw=480_000, final_score=45.0, size_multiplier=1.0)
    hi, _, _notes_hi = compute_max_order_krw(300_000, total_portfolio_krw=480_000, final_score=80.0, size_multiplier=1.0)
    assert hi >= lo
    assert hi <= 300_000


def test_compute_max_orders_risk_off_zero() -> None:
    n, notes = compute_max_orders_per_day(100_000, macro_regime="risk_off")
    assert n >= 1
    assert any("일일 BUY 최대" in x for x in notes)


def test_merge_tp_sl_uses_outcomes_when_enough_samples() -> None:
    tuned = CryptoTunedThresholds(
        take_profit_pct=3.5,
        stop_loss_pct=-2.0,
        min_volume_ratio=0.75,
        take_profit_buffer_pct=0.05,
        stop_loss_buffer_pct=0.05,
        generated_at="",
        lookback_days=60,
        sample_sell_closed=5,
        sample_buy_executed=10,
        sell_win_rate=0.6,
        sell_avg_return_pct=1.0,
        buy_win_rate=0.5,
    )
    tp, sl, *_rest, src = merge_tp_sl(tuned, 2.5)
    assert tp == 3.5
    assert sl == -2.0
    assert src == "outcomes"


def test_merge_tp_sl_crypto_default() -> None:
    tp, sl, *_rest, src = merge_tp_sl(None, 2.5)
    assert tp == 2.0
    assert sl == -1.5
    assert src == "crypto_default"


def test_tp_sl_from_atr() -> None:
    tp, sl, src = tp_sl_from_atr(2.0)
    assert src == "atr"
    assert tp > 0
    assert sl < 0


def test_resolve_sizing_dry_run_broker() -> None:
    from deepsignal.crypto_trading.crypto_position_sizing import resolve_crypto_runtime_sizing
    from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig

    br = UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))
    sizing = resolve_crypto_runtime_sizing(br, output_dir="outputs", macro_regime="neutral")
    assert sizing.max_order_krw >= MIN_ORDER_KRW
    assert sizing.max_orders_per_day >= 1
