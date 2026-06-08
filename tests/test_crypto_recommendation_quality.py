"""crypto_recommendation_quality — liquidity gate."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_recommendation_quality import check_crypto_liquidity_gate
from deepsignal.crypto_trading.crypto_recommendation_quality import check_crypto_concentration_gate
from deepsignal.crypto_trading.upbit_broker import MIN_ORDER_KRW, UpbitTicker


def test_liquidity_blocks_low_24h_volume() -> None:
    t = UpbitTicker(
        market="KRW-XRP",
        trade_price=2000,
        signed_change_rate=0.01,
        acc_trade_price_24h=1_000_000,
    )
    status, reasons = check_crypto_liquidity_gate(
        t,
        quality_ok=True,
        quality_reason="quality_ok",
        quality_diag={"volume_ratio": 1.5},
        min_volume_ratio=0.8,
        min_acc_trade_price_24h=500_000_000,
        order_krw=10_000,
    )
    assert status == "blocked"
    assert any("acc_trade" in r for r in reasons)


def test_liquidity_ok_when_volume_and_acc_sufficient() -> None:
    t = UpbitTicker(
        market="KRW-BTC",
        trade_price=100_000_000,
        signed_change_rate=0.01,
        acc_trade_price_24h=10_000_000_000_000,
    )
    status, reasons = check_crypto_liquidity_gate(
        t,
        quality_ok=True,
        quality_reason="quality_ok",
        quality_diag={"volume_ratio": 1.0},
        min_volume_ratio=0.8,
        min_acc_trade_price_24h=500_000_000,
        order_krw=max(MIN_ORDER_KRW, 5000),
    )
    assert status == "ok"
    assert not reasons


def test_concentration_gate_warn_and_block() -> None:
    warn_status, _ = check_crypto_concentration_gate(
        current_position_krw=3_500,
        total_portfolio_krw=100_000,
        order_krw=1_000,
        warn_pct=0.04,
        block_pct=0.05,
    )
    block_status, reasons = check_crypto_concentration_gate(
        current_position_krw=3_500,
        total_portfolio_krw=100_000,
        order_krw=2_000,
        warn_pct=0.04,
        block_pct=0.05,
    )
    assert warn_status == "warning"
    assert block_status == "blocked"
    assert any("concentration" in r for r in reasons)
