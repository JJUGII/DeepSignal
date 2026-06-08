from __future__ import annotations

from deepsignal.live_trading.ai_recommendation.liquidity_model import (
    LiquidityConfig,
    average_volume_for_symbol,
    check_liquidity,
)


def test_average_volume_calculation() -> None:
    volumes = {f"2026-01-{i + 1:02d}": {"AAPL": float(100 + i)} for i in range(5)}

    avg = average_volume_for_symbol(volumes_by_day=volumes, symbol="AAPL", day="2026-01-05", lookback_days=3)

    assert avg == (102 + 103 + 104) / 3


def test_liquidity_limit_adjusts_quantity() -> None:
    prices = {"2026-01-01": {"AAPL": 100.0}, "2026-01-02": {"AAPL": 100.0}}
    volumes = {"2026-01-01": {"AAPL": 1000.0}, "2026-01-02": {"AAPL": 1000.0}}

    result = check_liquidity(
        symbol="AAPL",
        day="2026-01-02",
        price=100.0,
        requested_quantity=100,
        prices_by_day=prices,
        volumes_by_day=volumes,
        config=LiquidityConfig(liquidity_limit_pct=0.01, volume_lookback_days=2),
    )

    assert result.allowed_quantity == 10
    assert result.adjusted_quantity == 10
    assert result.skipped is False


def test_min_daily_volume_skips() -> None:
    result = check_liquidity(
        symbol="AAPL",
        day="2026-01-01",
        price=100.0,
        requested_quantity=1,
        prices_by_day={"2026-01-01": {"AAPL": 100.0}},
        volumes_by_day={"2026-01-01": {"AAPL": 10.0}},
        config=LiquidityConfig(min_daily_volume=100.0),
    )

    assert result.skipped is True
    assert result.skip_reason == "SKIP_LOW_VOLUME"


def test_min_daily_value_skips() -> None:
    result = check_liquidity(
        symbol="AAPL",
        day="2026-01-01",
        price=100.0,
        requested_quantity=1,
        prices_by_day={"2026-01-01": {"AAPL": 100.0}},
        volumes_by_day={"2026-01-01": {"AAPL": 10.0}},
        config=LiquidityConfig(min_daily_value=10_000.0),
    )

    assert result.skipped is True
    assert result.skip_reason == "SKIP_LOW_DAILY_VALUE"


def test_volume_unavailable_warns_without_skip() -> None:
    result = check_liquidity(
        symbol="AAPL",
        day="2026-01-01",
        price=100.0,
        requested_quantity=5,
        prices_by_day={"2026-01-01": {"AAPL": 100.0}},
        volumes_by_day={"2026-01-01": {"AAPL": 0.0}},
        config=LiquidityConfig(liquidity_limit_pct=0.01),
    )

    assert result.skipped is False
    assert result.adjusted_quantity == 5
    assert "LIQUIDITY_VOLUME_UNAVAILABLE" in result.warnings


def test_disabled_config_keeps_quantity() -> None:
    result = check_liquidity(
        symbol="AAPL",
        day="2026-01-01",
        price=100.0,
        requested_quantity=5,
        prices_by_day={"2026-01-01": {"AAPL": 100.0}},
        volumes_by_day={"2026-01-01": {"AAPL": 1.0}},
        config=LiquidityConfig(),
    )

    assert result.adjusted_quantity == 5
    assert result.skipped is False
