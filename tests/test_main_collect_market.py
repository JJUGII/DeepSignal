"""collect-market CLI 경로 테스트 (네트워크 없음)."""

from __future__ import annotations

import main as main_mod
from deepsignal.collector.market import market_collector as mc_mod
from deepsignal.collector.market.market_data import MarketData


def _fake_collect_per_symbol(self, symbols=None, period=None, interval=None):
    m = MarketData(
        symbol="FAKE",
        trade_date="2024-02-01",
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        adjusted_close=1.4,
        volume=100,
        source="yfinance",
        raw={"stub": True},
    )
    yield "FAKE", [m], None


def test_main_collect_market_pipeline(monkeypatch, tmp_path, capsys) -> None:
    db = tmp_path / "cli_m.db"
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("MARKET_SYMBOLS", "FAKE")
    monkeypatch.setattr(
        mc_mod.MarketCollector, "collect_per_symbol", _fake_collect_per_symbol, raising=True
    )

    main_mod.main(["collect-market"])
    out = capsys.readouterr().out
    assert "DeepSignal market collection finished" in out
    assert "Symbols: FAKE" in out
    assert "Collected: 1" in out
    assert "Inserted: 1" in out
