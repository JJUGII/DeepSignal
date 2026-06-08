"""crypto_universe — KRW market list and liquidity-ranked scan set."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_universe import (
    CORE_CRYPTO_MARKETS,
    CryptoUniverseConfig,
    UNIVERSE_ALL_KRW,
    UNIVERSE_CORE,
    fetch_tickers_batched,
    list_upbit_krw_markets,
    resolve_crypto_markets,
    select_markets_for_buy_scan,
)
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitConfig, UpbitTicker


def _br() -> UpbitBroker:
    return UpbitBroker(UpbitConfig(access_key="dry-run-key", secret_key="dry-run-secret", dry_run=True))


def test_list_mock_krw_markets_expanded() -> None:
    markets, names = list_upbit_krw_markets(_br())
    assert len(markets) > len(CORE_CRYPTO_MARKETS)
    assert "KRW-BTC" in markets
    assert "KRW-SOL" in markets
    assert names["KRW-BTC"]


def test_select_top_by_volume() -> None:
    tickers = {
        "KRW-BTC": UpbitTicker("KRW-BTC", 100.0, 0.01, 1_000_000_000_000.0),
        "KRW-ETH": UpbitTicker("KRW-ETH", 50.0, 0.01, 500_000_000_000.0),
        "KRW-XRP": UpbitTicker("KRW-XRP", 1.0, 0.01, 10_000_000_000.0),
        "KRW-DOGE": UpbitTicker("KRW-DOGE", 0.1, 0.01, 600_000_000_000.0),
    }
    selected = select_markets_for_buy_scan(
        tickers,
        min_acc_trade_price_24h=500_000_000.0,
        max_markets=3,
        always_include=("KRW-XRP",),
    )
    assert selected[0] == "KRW-XRP"
    assert "KRW-BTC" in selected
    assert "KRW-DOGE" in selected
    assert "KRW-ETH" not in selected


def test_resolve_all_krw_dry_run() -> None:
    meta = resolve_crypto_markets(_br(), config=CryptoUniverseConfig(universe=UNIVERSE_ALL_KRW, max_buy_scan_markets=5))
    assert meta.universe == UNIVERSE_ALL_KRW
    assert meta.total_krw_markets >= 10
    assert len(meta.markets) <= 5


def test_resolve_core_unchanged() -> None:
    meta = resolve_crypto_markets(_br(), config=CryptoUniverseConfig(universe=UNIVERSE_CORE))
    assert meta.markets == CORE_CRYPTO_MARKETS
