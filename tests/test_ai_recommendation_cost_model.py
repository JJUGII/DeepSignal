from __future__ import annotations

import pytest

from deepsignal.live_trading.ai_recommendation.cost_model import CostModel


def test_buy_cost_calculation() -> None:
    model = CostModel(commission_rate=0.001, slippage_bps=5, min_order_value=0)

    cost = model.estimate_buy_cost(100.0, 10)

    assert cost["adjusted_price"] == pytest.approx(100.05)
    assert cost["value"] == pytest.approx(1000.5)
    assert cost["commission"] == pytest.approx(1.0005)
    assert cost["slippage_cost"] == pytest.approx(0.5)
    assert cost["total_cost"] == pytest.approx(1.5005)


def test_sell_cost_calculation() -> None:
    model = CostModel(commission_rate=0.001, tax_rate=0.002, slippage_bps=5, min_order_value=0)

    cost = model.estimate_sell_proceeds(100.0, 10)

    assert cost["adjusted_price"] == pytest.approx(99.95)
    assert cost["value"] == pytest.approx(999.5)
    assert cost["commission"] == pytest.approx(0.9995)
    assert cost["tax"] == pytest.approx(1.999)
    assert cost["slippage_cost"] == pytest.approx(0.5)
    assert cost["cash_delta"] == pytest.approx(996.5015)


def test_min_and_max_order_skip() -> None:
    model = CostModel(min_order_value=1000.0, max_order_value=5000.0)

    assert model.should_skip_order(999.0) == "SKIP_COST_MIN_ORDER"
    assert model.should_skip_order(5001.0) == "SKIP_COST_MAX_ORDER"
    assert model.should_skip_order(3000.0) is None


def test_no_costs_model() -> None:
    model = CostModel.no_costs(currency="USD")

    assert model.enabled is False
    assert model.adjusted_buy_price(100.0) == pytest.approx(100.0)
    assert model.estimate_buy_cost(100.0, 1)["total_cost"] == pytest.approx(0.0)
    assert model.should_skip_order(1.0) is None
