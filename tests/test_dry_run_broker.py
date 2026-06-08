"""DryRunBroker: 가짜 주문 결과만 생성 (네트워크 없음)."""

from __future__ import annotations

import inspect

from deepsignal.live_trading.broker_interface import BrokerOrderRequest
from deepsignal.live_trading import dry_run_broker as drb_mod
from deepsignal.live_trading.dry_run_broker import DryRunBroker


def test_dry_run_place_order_returns_dry_run_accepted() -> None:
    b = DryRunBroker()
    r = b.place_order(
        BrokerOrderRequest(
            symbol="AAPL",
            side="BUY",
            quantity=2,
            order_type="LIMIT",
            limit_price=190.2,
            estimated_value=380.4,
            client_order_id="cid1",
            source_plan_id="live_order_plan_20260515",
        )
    )
    assert r.status == "DRY_RUN_ACCEPTED"
    assert r.broker_order_id is not None
    assert str(r.broker_order_id).startswith("dryrun_")
    assert r.symbol == "AAPL"
    assert r.quantity == 2
    assert r.submitted_price == 190.2
    assert "request" in r.raw
    assert r.raw.get("dry_run") is True


def test_dry_run_broker_module_has_no_network_imports_in_source() -> None:
    src = inspect.getsource(drb_mod).lower()
    for needle in ("urllib", "requests", "httpx", "aiohttp", "socket."):
        assert needle not in src, f"unexpected network-related token: {needle}"
