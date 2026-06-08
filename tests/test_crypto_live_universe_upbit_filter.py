from __future__ import annotations

from deepsignal.crypto_trading.crypto_live_universe import (
    binance_symbol_to_upbit_market,
    tickers_from_live_state,
)
from deepsignal.crypto_trading.upbit_broker import UpbitTicker


def test_binance_to_upbit_mapping() -> None:
    assert binance_symbol_to_upbit_market("BTCUSDT") == "KRW-BTC"
    assert binance_symbol_to_upbit_market("EURUSDT") == "KRW-EUR"


def test_tickers_from_live_state_filters_non_upbit(tmp_path) -> None:
    live_dir = tmp_path / "binance_stream"
    live_dir.mkdir(parents=True)
    (live_dir / "live_state.json").write_text(
        '{"orderbooks": {"BTCUSDT": {"bids": [[100.0, 1.0]], "asks": [[101.0, 1.0]]}, '
        '"EURUSDT": {"bids": [[1.0, 1.0]], "asks": [[1.1, 1.0]]}}}',
        encoding="utf-8",
    )
    valid = frozenset({"KRW-BTC", "KRW-ETH"})
    out = tickers_from_live_state(tmp_path, valid_upbit_markets=valid)
    assert "KRW-BTC" in out
    assert "KRW-EUR" not in out
    assert isinstance(out["KRW-BTC"], UpbitTicker)
