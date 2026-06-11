from __future__ import annotations

from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig, evaluate_crypto_buy_quality
from deepsignal.crypto_trading.crypto_recommendation import build_crypto_recommendation
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig, UpbitTicker


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def test_rsi_overbought_blocks_buy() -> None:
    br = _br()
    candles = []
    price = 2000.0
    for _ in range(30):
        price *= 1.06
        candles.append(
            {
                "trade_price": price,
                "high_price": price * 1.01,
                "low_price": price * 0.99,
                "candle_acc_trade_volume": 1e10,
            }
        )
    ticker = br.get_ticker("KRW-XRP")

    def fake_candles(market: str, count: int = 30):
        return candles[-count:]

    br.get_daily_candles = fake_candles  # type: ignore[method-assign]
    ok, reason, _, diag = evaluate_crypto_buy_quality(br, "KRW-XRP", ticker, cfg=CryptoBuyQualityConfig(max_rsi=75))
    assert ok is False
    assert "RSI" in reason
    assert diag.get("rsi_14") is not None


def test_low_volume_ratio_blocks() -> None:
    br = _br()
    ticker = UpbitTicker(
        market="KRW-XRP",
        trade_price=2050.0,
        signed_change_rate=0.01,
        acc_trade_price_24h=1.0,
    )
    ok, reason, _, _ = evaluate_crypto_buy_quality(
        br,
        "KRW-XRP",
        ticker,
        cfg=CryptoBuyQualityConfig(min_volume_ratio=0.8, max_rsi=100.0),
    )
    assert ok is False
    assert "거래량" in reason


def test_build_crypto_buy_still_works_with_filters(monkeypatch) -> None:
    monkeypatch.setenv("CRYPTO_ML_BUY_GATE", "false")
    monkeypatch.setenv("CRYPTO_ML_ENSEMBLE", "false")
    # live fail-open 가드(_live_auto_crypto_buy_requires_ml_gate) 중화 —
    # 실운영 .env가 CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL=true면 ML off 상태 BUY가
    # 차단되어 rec=None이 됨. 이 테스트는 품질 필터 검증이므로 paper 모드로 격리.
    monkeypatch.setenv("CRYPTO_PAPER_MODE", "true")
    br = _br()
    rec = build_crypto_recommendation(br, buy_quality=CryptoBuyQualityConfig())
    assert rec is not None
    assert rec.side == "buy"
