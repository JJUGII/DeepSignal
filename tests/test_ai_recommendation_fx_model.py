from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.ai_recommendation.fx_model import (
    FXConfig,
    FXModel,
    load_fx_rates,
    load_symbol_currency_map,
    parse_fallback_rates,
)


def test_symbol_currency_map_loads(tmp_path: Path) -> None:
    path = tmp_path / "symbol_currency_map.json"
    path.write_text(json.dumps({"aapl": "usd", "005930": "krw"}), encoding="utf-8")

    assert load_symbol_currency_map(str(path)) == {"AAPL": "USD", "005930": "KRW"}


def test_fx_rates_load(tmp_path: Path) -> None:
    path = tmp_path / "fx_rates.json"
    path.write_text(json.dumps({"base_currency": "KRW", "rates": {"2026-01-01": {"USD": 1350, "KRW": 1}}}), encoding="utf-8")

    base, rates = load_fx_rates(str(path))

    assert base == "KRW"
    assert rates["2026-01-01"]["USD"] == 1350.0


def test_parse_fallback_fx() -> None:
    assert parse_fallback_rates("USD=1350,KRW=1") == {"USD": 1350.0, "KRW": 1.0}


def test_usd_to_krw_conversion_with_file(tmp_path: Path) -> None:
    path = tmp_path / "fx_rates.json"
    path.write_text(json.dumps({"base_currency": "KRW", "rates": {"2026-01-01": {"USD": 1350.0, "KRW": 1.0}}}), encoding="utf-8")
    model = FXModel(FXConfig(base_currency="KRW", default_symbol_currency="USD", fx_rates_path=str(path)))

    result = model.convert(10.0, "USD", "2026-01-01")

    assert result.rate == 1350.0
    assert result.converted_amount == 13_500.0
    assert result.warning == ""


def test_missing_rate_uses_fallback_warning() -> None:
    model = FXModel(FXConfig(base_currency="KRW", default_symbol_currency="USD", fallback_rates={"USD": 1350.0}))

    result = model.convert(2.0, "USD", "2026-01-01")

    assert result.converted_amount == 2700.0
    assert result.warning.startswith("FX_FALLBACK_USED")
    assert model.warnings


def test_missing_rate_without_fallback_warns_and_uses_one() -> None:
    model = FXModel(FXConfig(base_currency="KRW", default_symbol_currency="USD"))

    result = model.convert(2.0, "USD", "2026-01-01")

    assert result.rate == 1.0
    assert result.converted_amount == 2.0
    assert result.warning.startswith("FX_RATE_UNAVAILABLE")
