"""Tests for whale trade watch."""

from __future__ import annotations

from deepsignal.crypto_trading.whale.buffer import make_whale_trade
from deepsignal.crypto_trading.whale.config import WhaleWatchConfig
from deepsignal.crypto_trading.whale.streams import _parse_trade
from deepsignal.crypto_trading.whale.tracker import MarketFlowTracker


def test_parse_upbit_trade():
    row = {
        "type": "trade",
        "code": "KRW-XTER",
        "trade_price": 100.0,
        "trade_volume": 2_000_000.0,
        "ask_bid": "BID",
        "trade_timestamp": 1_700_000_000_000,
        "stream_type": "REALTIME",
    }
    parsed = _parse_trade(row, "upbit")
    assert parsed is not None
    market, px, vol, side, _ = parsed
    assert market == "KRW-XTER"
    assert px == 100.0
    assert vol == 2_000_000.0
    assert side == "buy"


def test_market_flow_tracker_surge():
    tr = MarketFlowTracker(min_krw=100_000_000.0, surge_ratio=2.5)
    base = 1_000_000.0
    for i in range(60):
        tr.on_trade(1000.0 + i, base)
    big = tr.on_trade(1100.0, 200_000_000.0)
    assert big.whale_count_5m == 1
    assert big.vol_1m_krw >= 200_000_000.0


def test_whale_config_from_env(monkeypatch):
    monkeypatch.setenv("WHALE_MIN_KRW", "30000000")
    monkeypatch.setenv("WHALE_WATCH_ENABLED", "true")
    cfg = WhaleWatchConfig.from_env()
    assert cfg.min_krw == 30_000_000.0
    assert cfg.enabled is True


def test_make_whale_trade():
    t = make_whale_trade(
        exchange="bithumb",
        market="KRW-TEST",
        side="sell",
        price=1000.0,
        volume=150_000.0,
        krw=150_000_000.0,
        vol_1m_krw=300_000_000.0,
        vol_surge_ratio=3.0,
        whale_count_5m=2,
        vol_surge=True,
        ts_sec=1_700_000_000.0,
    )
    assert t.symbol == "TEST"
    assert t.vol_surge is True
    d = t.to_dict()
    assert d["krw"] == 150_000_000.0
