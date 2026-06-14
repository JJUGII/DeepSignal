"""Upbit mock tickers/candles and default core crypto markets."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from deepsignal.crypto_trading.upbit_broker import UpbitTicker

DEFAULT_CRYPTO_MARKETS: tuple[str, ...] = ("KRW-BTC", "KRW-ETH", "KRW-XRP")

_MOCK_PRICES: dict[str, float] = {
    "KRW-BTC": 95_000_000.0,
    "KRW-ETH": 3_500_000.0,
    "KRW-XRP": 800.0,
    "KRW-SOL": 180_000.0,
    "KRW-DOGE": 200.0,
    "KRW-ADA": 600.0,
    "KRW-AVAX": 40_000.0,
    "KRW-LINK": 20_000.0,
    "KRW-DOT": 8_000.0,
    "KRW-ATOM": 12_000.0,
}


def _mock_price(market: str) -> float:
    m = market.strip().upper()
    if m in _MOCK_PRICES:
        return _MOCK_PRICES[m]
    if m.startswith("KRW-"):
        return 10_000.0
    return 1_000.0


def mock_ticker(market: str) -> UpbitTicker:
    m = market.strip().upper()
    return UpbitTicker(
        market=m,
        trade_price=_mock_price(m),
        signed_change_rate=0.01,
        acc_trade_price_24h=5_000_000_000.0,
    )


def mock_tickers(markets: tuple[str, ...]) -> dict[str, UpbitTicker]:
    return {m.strip().upper(): mock_ticker(m) for m in markets if m and str(m).strip()}


def mock_daily_candles(market: str, *, count: int = 20) -> list[dict[str, Any]]:
    m = market.strip().upper()
    base = _mock_price(m)
    n = max(2, min(int(count), 200))
    today = date.today()
    out: list[dict[str, Any]] = []
    for i in range(n):
        d = today - timedelta(days=n - 1 - i)
        px = base * (1.0 + 0.002 * (i - n // 2))
        out.append(
            {
                "candle_date_time_kst": d.isoformat(),
                "trade_price": round(px, 2),
                "opening_price": round(px * 0.998, 2),
                "high_price": round(px * 1.01, 2),
                "low_price": round(px * 0.99, 2),
                "candle_acc_trade_volume": 1000.0 + i,
            }
        )
    return out
