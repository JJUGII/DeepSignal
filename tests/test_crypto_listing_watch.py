"""Tests for cross-exchange listing watch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from deepsignal.crypto_trading.broker.interface import CryptoTicker
from deepsignal.crypto_trading.listing.anomaly import (
    change_1h_pct_from_minute_candles,
    compute_anomaly_score,
    volume_ratio_from_candles,
)
from deepsignal.crypto_trading.listing.config import ListingWatchConfig
from deepsignal.crypto_trading.listing.scanner import run_listing_scan
from deepsignal.crypto_trading.listing.snapshot import diff_new_markets


@dataclass
class _FakeTicker:
    trade_price: float = 100.0
    signed_change_rate: float = 0.12
    acc_trade_price_24h: float = 500_000_000.0


class _FakeBroker:
    exchange_id = "bithumb"

    def __init__(self, markets: list[str], tickers: dict[str, _FakeTicker] | None = None):
        self._markets = markets
        self._tickers = tickers or {}

    def get_market_all(self, *, is_details: bool = True) -> list[dict[str, Any]]:
        return [{"market": m, "korean_name": m.replace("KRW-", "")} for m in self._markets]

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if path == "/market/all":
            return [{"market": m, "korean_name": m.replace("KRW-", "")} for m in self._markets]
        raise RuntimeError(path)

    def get_daily_candles(self, market: str, *, count: int = 20) -> list[dict[str, Any]]:
        return [
            {"candle_acc_trade_volume": 1000, "trade_price": 90},
            {"candle_acc_trade_volume": 1100, "trade_price": 95},
            {"candle_acc_trade_volume": 1200, "trade_price": 100},
            {"candle_acc_trade_volume": 1300, "trade_price": 105},
            {"candle_acc_trade_volume": 1400, "trade_price": 110},
        ]

    def get_minute_candles(self, market: str, unit: int, count: int) -> list[dict[str, Any]]:
        return [
            {"time": 1, "close": 100},
            {"time": 2, "close": 106},
        ]


def test_diff_new_markets():
    assert diff_new_markets({"KRW-A", "KRW-B"}, {"KRW-A"}) == ["KRW-B"]
    assert diff_new_markets({"KRW-A"}, None) == []


def test_volume_ratio_and_score():
    ticker = CryptoTicker(market="KRW-TEST", trade_price=100, signed_change_rate=0.15, acc_trade_price_24h=1_000_000)
    candles = [
        {"candle_acc_trade_volume": 100, "trade_price": 100},
        {"candle_acc_trade_volume": 100, "trade_price": 100},
        {"candle_acc_trade_volume": 100, "trade_price": 100},
        {"candle_acc_trade_volume": 100, "trade_price": 100},
        {"candle_acc_trade_volume": 5000, "trade_price": 100},
    ]
    ratio = volume_ratio_from_candles(candles, ticker)
    assert ratio is not None
    assert ratio > 5

    chg1h = change_1h_pct_from_minute_candles(
        [{"time": 1, "close": 100}, {"time": 2, "close": 108}]
    )
    assert chg1h == pytest.approx(8.0)

    score, tags = compute_anomaly_score(
        vol_ratio=ratio,
        chg_24h_pct=15.0,
        chg_1h_pct=chg1h,
        is_new=True,
        new_on="bithumb",
        vol_ratio_alert=2.5,
    )
    assert score >= 65
    assert "빗썸신규" in tags


def test_run_listing_scan_offline(tmp_path, monkeypatch):
    up_markets = ["KRW-BTC", "KRW-ETH", "KRW-ONLY-UP"]
    bi_markets = ["KRW-BTC", "KRW-ETH", "KRW-ONLY-BI"]
    up = _FakeBroker(up_markets)
    up.exchange_id = "upbit"
    bi = _FakeBroker(
        bi_markets,
        tickers={
            "KRW-ONLY-BI": _FakeTicker(acc_trade_price_24h=200_000_000, signed_change_rate=0.2),
        },
    )

    def fake_load(name: str, *, dry_run: bool | None = None):
        if name == "upbit":
            return up
        if name == "bithumb":
            return bi
        raise ValueError(name)

    monkeypatch.setattr(
        "deepsignal.crypto_trading.listing.scanner.load_crypto_broker",
        fake_load,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.listing.scanner.fetch_tickers_batched",
        lambda broker, markets, **kw: {
            m: CryptoTicker(
                market=m,
                trade_price=100.0,
                signed_change_rate=0.2,
                acc_trade_price_24h=200_000_000.0,
            )
            for m in markets
        },
    )

    cfg = ListingWatchConfig(
        min_acc_trade_24h=100_000_000,
        max_deep_scan=10,
        telegram_alert=False,
    )
    result = run_listing_scan(tmp_path, cfg=cfg, network=True, send_alerts=False)
    assert result.bithumb_only_count == 1
    assert result.upbit_only_count == 1
    assert any(c.market == "KRW-ONLY-BI" for c in result.candidates)

    bi_markets.append("KRW-NEW")
    result2 = run_listing_scan(tmp_path, cfg=cfg, network=True, send_alerts=False)
    assert "KRW-NEW" in result2.new_on_bithumb


def test_run_listing_scan_no_network_returns_cache(tmp_path):
    cfg = ListingWatchConfig()
    empty = run_listing_scan(tmp_path, cfg=cfg, network=False, send_alerts=False)
    assert empty.candidates == []
