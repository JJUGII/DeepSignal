from __future__ import annotations

from unittest.mock import MagicMock

from deepsignal.crypto_trading.broker.bithumb.broker import BithumbBroker
from deepsignal.crypto_trading.broker.bithumb.config import BithumbConfig
from deepsignal.crypto_trading.signal.universe import list_bithumb_krw_markets, list_krw_markets, resolve_crypto_markets


def test_list_bithumb_krw_markets_demo() -> None:
    br = BithumbBroker(BithumbConfig(api_key="demo-key", secret_key="demo-secret", dry_run=True))
    markets, names = list_bithumb_krw_markets(br)
    assert "KRW-BTC" in markets
    assert names["KRW-BTC"]


def test_list_krw_markets_routes_bithumb() -> None:
    br = BithumbBroker(BithumbConfig(api_key="demo-key", secret_key="demo-secret", dry_run=True))
    markets, _ = list_krw_markets(br)
    assert any(m.startswith("KRW-") for m in markets)


def test_resolve_crypto_markets_core_bithumb() -> None:
    br = BithumbBroker(BithumbConfig(api_key="demo-key", secret_key="demo-secret", dry_run=True))
    from deepsignal.crypto_trading.signal.universe import CryptoUniverseConfig

    result = resolve_crypto_markets(br, config=CryptoUniverseConfig(universe="core"))
    assert result.markets
    assert result.markets[0].startswith("KRW-")


def test_bithumb_market_all_mock() -> None:
    def fake_request(method, url, **kwargs):
        resp = MagicMock(status_code=200, text="[]")
        resp.json.return_value = [
            {"market": "KRW-BTC", "korean_name": "비트코인"},
            {"market": "KRW-ETH", "korean_name": "이더리움"},
        ]
        return resp

    br = BithumbBroker(
        BithumbConfig(api_key="real-key-abcdef", secret_key="real-secret-abcdef", dry_run=False),
        request_fn=fake_request,
    )
    markets, names = list_bithumb_krw_markets(br)
    assert markets == ["KRW-BTC", "KRW-ETH"]
    assert names["KRW-BTC"] == "비트코인"
