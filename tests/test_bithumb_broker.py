from __future__ import annotations

from deepsignal.crypto_trading.broker.bithumb.broker import BithumbBroker
from deepsignal.crypto_trading.broker.bithumb.config import BithumbConfig


def test_bithumb_v2_public_ticker() -> None:
    br = BithumbBroker(BithumbConfig(api_key="demo-key", secret_key="demo-secret", dry_run=True))
    t = br.get_ticker("KRW-BTC")
    assert t.market == "KRW-BTC"
    assert t.trade_price > 0


def test_bithumb_v2_accounts_mock() -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append((method, url))

        class Resp:
            status_code = 200
            text = (
                '[{"currency":"KRW","balance":"12345","locked":"0","avg_buy_price":"0"},'
                '{"currency":"BTC","balance":"0.01","locked":"0","avg_buy_price":"90000000"}]'
            )

            def json(self):
                import json

                return json.loads(self.text)

        return Resp()

    br = BithumbBroker(
        BithumbConfig(api_key="real-key-abcdef", secret_key="real-secret-abcdef", dry_run=False),
        request_fn=fake_request,
    )
    balances = br.get_balances()
    assert any(b.currency == "KRW" and b.balance == 12345 for b in balances)
    assert calls and calls[0][0] == "GET"
    assert "/v1/accounts" in calls[0][1]
