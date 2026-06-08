"""crypto_recommendation_diagnostics — no-recommendation BUY/SELL 진단."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.crypto_trading.crypto_order_plan import CRYPTO_PLAN_JSON, CRYPTO_PLAN_MD
from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig
from deepsignal.crypto_trading.crypto_recommendation_diagnostics import (
    build_crypto_recommendation_diagnostics,
    diagnose_buy_candidate,
    diagnose_sell_candidate,
    save_crypto_no_recommendation_artifacts,
)
from deepsignal.crypto_trading.upbit_broker import CryptoHolding, UpbitBroker, UpbitConfig, UpbitTicker


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def _rising_candles(n: int = 30, start: float = 2000.0, step: float = 1.06) -> list[dict]:
    candles = []
    price = start
    for _ in range(n):
        price *= step
        candles.append(
            {
                "trade_price": price,
                "high_price": price * 1.01,
                "low_price": price * 0.99,
                "candle_acc_trade_volume": 1e10,
            }
        )
    return candles


def test_rsi_overheat_blocked_reason() -> None:
    br = _br()
    br.get_daily_candles = lambda market, count=30: _rising_candles()  # type: ignore[method-assign]
    ticker = br.get_ticker("KRW-XRP")
    diag = diagnose_buy_candidate(
        br,
        ticker,
        buy_quality=CryptoBuyQualityConfig(max_rsi=75),
    )
    assert diag.rsi_pass is False
    assert diag.atr_action == "block"
    assert any("RSI" in r for r in diag.blocked_reasons)


def test_volume_ratio_insufficient_blocked_reason() -> None:
    br = _br()
    ticker = UpbitTicker(
        market="KRW-XRP",
        trade_price=2050.0,
        signed_change_rate=0.01,
        acc_trade_price_24h=1.0,
    )
    diag = diagnose_buy_candidate(
        br,
        ticker,
        buy_quality=CryptoBuyQualityConfig(min_volume_ratio=0.8, max_rsi=100.0),
    )
    assert diag.volume_pass is False
    assert diag.quality_ok is False
    assert any("거래량" in r for r in diag.blocked_reasons)


def test_atr_high_reduce_not_block_when_quality_ok() -> None:
    br = _br()
    volatile: list[dict] = []
    price = 100.0
    for i in range(30):
        swing = 15.0 if i > 0 else 5.0
        volatile.append(
            {
                "trade_price": price,
                "high_price": price + swing,
                "low_price": max(1.0, price - swing),
                "candle_acc_trade_volume": 1e10,
            }
        )
        price += 0.5
    br.get_daily_candles = lambda market, count=30: volatile[-count:]  # type: ignore[method-assign]
    ticker = UpbitTicker(
        market="KRW-ETH",
        trade_price=115.0,
        signed_change_rate=0.02,
        acc_trade_price_24h=5e11,
    )
    cfg = CryptoBuyQualityConfig(max_rsi=100.0, min_volume_ratio=0.01, max_atr_pct=1.0)
    diag = diagnose_buy_candidate(br, ticker, buy_quality=cfg)
    assert diag.quality_ok is True
    assert diag.atr_action == "reduce"
    assert diag.size_multiplier < 1.0
    assert any("ATR" in r for r in diag.blocked_reasons)


def test_atr_extreme_blocks_when_rsi_fails() -> None:
    br = _br()
    br.get_daily_candles = lambda market, count=30: _rising_candles()  # type: ignore[method-assign]
    ticker = br.get_ticker("KRW-BTC")
    diag = diagnose_buy_candidate(br, ticker, buy_quality=CryptoBuyQualityConfig(max_rsi=70))
    assert diag.quality_ok is False
    assert diag.atr_action == "block"


def test_sell_not_triggered_blocked_reason() -> None:
    h = CryptoHolding(
        market="KRW-XRP",
        currency="XRP",
        balance=10.0,
        locked=0.0,
        available=10.0,
        avg_buy_price=2000.0,
        current_price=2050.0,
        valuation_krw=20_500.0,
        pnl_pct=2.5,
        pnl_krw=500.0,
    )
    diag = diagnose_sell_candidate(h, take_profit_pct=5.0, stop_loss_pct=-3.0)
    assert diag.sell_trigger is None
    assert diag.min_order_krw_pass is True
    assert any("부족" in r or "미도달" in r for r in diag.blocked_reasons)


def test_sell_min_order_fail() -> None:
    h = CryptoHolding(
        market="KRW-XRP",
        currency="XRP",
        balance=0.001,
        locked=0.0,
        available=0.001,
        avg_buy_price=2000.0,
        current_price=2100.0,
        valuation_krw=2.1,
        pnl_pct=5.0,
        pnl_krw=0.1,
    )
    diag = diagnose_sell_candidate(h, take_profit_pct=2.0, stop_loss_pct=-1.5)
    assert diag.min_order_krw_pass is False
    assert diag.sell_trigger is None
    assert any("최소주문" in r for r in diag.blocked_reasons)


def test_no_recommendation_json_and_md_artifacts(tmp_path: Path) -> None:
    br = _br()

    def fake_holdings():
        return [
            CryptoHolding(
                market="KRW-XRP",
                currency="XRP",
                balance=10.0,
                locked=0.0,
                available=10.0,
                avg_buy_price=2000.0,
                current_price=2050.0,
                valuation_krw=20_500.0,
                pnl_pct=2.5,
                pnl_krw=500.0,
            )
        ]

    br.get_crypto_holdings = fake_holdings  # type: ignore[method-assign]
    br.get_daily_candles = lambda market, count=30: _rising_candles()  # type: ignore[method-assign]

    diagnostics = build_crypto_recommendation_diagnostics(
        br,
        take_profit_pct=5.0,
        stop_loss_pct=-3.0,
        buy_quality=CryptoBuyQualityConfig(max_rsi=70),
    )
    jpath, mpath = save_crypto_no_recommendation_artifacts(tmp_path, diagnostics)

    assert jpath == tmp_path / CRYPTO_PLAN_JSON
    assert mpath == tmp_path / CRYPTO_PLAN_MD
    payload = json.loads(jpath.read_text(encoding="utf-8"))
    assert payload["status"] == "CRYPTO_PLAN_NO_RECOMMENDATION"
    assert "diagnostics" in payload
    assert payload["diagnostics"]["buy_candidates"]
    assert payload["diagnostics"]["sell_candidates"]
    md = mpath.read_text(encoding="utf-8")
    assert "CRYPTO_PLAN_NO_RECOMMENDATION" in md
    assert "BUY candidate diagnostics" in md
    assert "SELL candidate diagnostics" in md
    assert "Final no recommendation reason" in md
