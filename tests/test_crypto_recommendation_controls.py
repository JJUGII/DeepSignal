from __future__ import annotations

import pytest

from deepsignal.crypto_trading import crypto_recommendation as rec_mod
from deepsignal.crypto_trading.crypto_recommendation import build_crypto_recommendation
from deepsignal.crypto_trading.crypto_recommendation_quality import check_crypto_concentration_gate
from deepsignal.crypto_trading.crypto_signal_scorer import CryptoMarketScore
from deepsignal.crypto_trading.upbit_broker import CryptoHolding, UpbitBroker, UpbitConfig, UpbitTicker


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def test_concentration_gate_blocks_after_next_order() -> None:
    status, reasons = check_crypto_concentration_gate(
        current_position_krw=30_000,
        total_portfolio_krw=100_000,
        order_krw=25_000,
        warn_pct=0.04,
        block_pct=0.05,
    )
    assert status == "blocked"
    assert any("concentration" in r for r in reasons)


def test_build_recommendation_excludes_cooldown_market(monkeypatch: pytest.MonkeyPatch) -> None:
    br = _br()
    ticker = UpbitTicker(
        market="KRW-RENDER",
        trade_price=3000.0,
        signed_change_rate=0.02,
        acc_trade_price_24h=1e10,
    )
    monkeypatch.setattr(br, "get_crypto_holdings", lambda: [])
    monkeypatch.setattr(br, "get_ticker", lambda market: ticker)
    from deepsignal.crypto_trading import crypto_universe as uni_mod

    monkeypatch.setattr(uni_mod, "fetch_tickers_batched", lambda *_args, **_kwargs: {"KRW-RENDER": ticker})

    def fake_score(*_args, **_kwargs) -> CryptoMarketScore:
        return CryptoMarketScore(
            market="KRW-RENDER",
            display_name="RENDER",
            technical_score=70.0,
            macro_score=0.0,
            final_score=70.0,
            macro_regime="neutral",
        )

    monkeypatch.setattr(rec_mod, "score_crypto_market", fake_score)
    out = build_crypto_recommendation(
        br,
        markets=("KRW-RENDER",),
        max_order_value=10_000,
        exclude_markets=("KRW-RENDER",),
    )
    assert out is None


def test_prefer_non_holding_buy_over_holding(monkeypatch: pytest.MonkeyPatch) -> None:
    br = _br()
    holdings = [
        CryptoHolding(
            market="KRW-RENDER",
            currency="RENDER",
            balance=10.0,
            locked=0.0,
            available=10.0,
            avg_buy_price=3250.0,
            current_price=3240.0,
            valuation_krw=32_400.0,
            pnl_pct=-0.3,
            pnl_krw=-100.0,
        )
    ]
    ticker_map = {
        "KRW-RENDER": UpbitTicker("KRW-RENDER", 3240.0, 0.01, 1e10),
        "KRW-ERA": UpbitTicker("KRW-ERA", 240.0, 0.01, 1e10),
    }
    monkeypatch.setattr(br, "get_crypto_holdings", lambda: holdings)
    monkeypatch.setattr(br, "get_krw_available", lambda: 300_000.0)
    monkeypatch.setattr(br, "get_ticker", lambda market: ticker_map[market])
    monkeypatch.setattr(rec_mod, "load_crypto_macro_context", lambda _path=None: {"market_regime": "neutral"})
    from deepsignal.crypto_trading import crypto_universe as uni_mod

    monkeypatch.setattr(uni_mod, "fetch_tickers_batched", lambda *_args, **_kwargs: dict(ticker_map))

    def fake_score(_broker, ticker, **_kwargs) -> CryptoMarketScore:
        final = 90.0 if ticker.market == "KRW-RENDER" else 80.0
        return CryptoMarketScore(
            market=ticker.market,
            display_name=ticker.market,
            technical_score=final,
            macro_score=0.0,
            final_score=final,
            macro_regime="neutral",
        )

    monkeypatch.setattr(rec_mod, "score_crypto_market", fake_score)
    rec = build_crypto_recommendation(
        br,
        markets=("KRW-RENDER", "KRW-ERA"),
        max_order_value=10_000,
        prefer_non_holding_buy=True,
    )
    assert rec is not None
    assert rec.market == "KRW-ERA"
