from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from deepsignal.crypto_trading.broker.bithumb.broker import BithumbBroker
from deepsignal.crypto_trading.broker.bithumb.config import BithumbConfig
from deepsignal.crypto_trading.crypto_recommendation import build_daily_crypto_recommendation
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS
from main import cmd_crypto_daily_plan


def _force_bithumb_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BITHUMB_API_KEY", "demo-key")
    monkeypatch.setenv("BITHUMB_SECRET_KEY", "demo-secret")
    monkeypatch.setenv("CRYPTO_PAPER_MODE", "true")
    monkeypatch.setenv("BITHUMB_DRY_RUN", "true")


def _daily_plan_args(tmp_path: Path, *, broker: str = "bithumb") -> argparse.Namespace:
    c = DEFAULT_ANALYSIS_CONDITIONS.crypto
    return argparse.Namespace(
        broker=broker,
        output_dir=str(tmp_path),
        max_order_value=10_000.0,
        take_profit_pct=c.take_profit_pct,
        stop_loss_pct=c.stop_loss_pct,
        take_profit_buffer_pct=0.05,
        stop_loss_buffer_pct=0.05,
        min_volume_ratio=c.min_volume_ratio,
        network=False,
        debug_holdings=False,
        debug_quality=False,
        crypto_markets="KRW-BTC",
        crypto_universe="core",
        max_scan_markets=c.max_buy_scan_markets,
        min_acc_trade_24h=c.min_acc_trade_price_24h,
        ticker_batch_size=c.ticker_batch_size,
    )


def test_cmd_crypto_daily_plan_bithumb_demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _force_bithumb_demo(monkeypatch)
    rc = cmd_crypto_daily_plan(_daily_plan_args(tmp_path, broker="bithumb"))
    assert rc == 0
    assert (tmp_path / "CRYPTO_ORDER_PLAN.json").is_file()
    assert (tmp_path / "CRYPTO_DAILY_TRADE_PLAN.md").is_file()


def test_build_daily_crypto_recommendation_bithumb_demo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRYPTO_ML_BUY_GATE", "false")
    monkeypatch.setenv("CRYPTO_ML_ENSEMBLE", "false")
    br = BithumbBroker(BithumbConfig(api_key="demo-key", secret_key="demo-secret", dry_run=True))
    rec = build_daily_crypto_recommendation(
        br,
        markets=("KRW-BTC",),
        max_order_value=10_000,
        output_dir=tmp_path,
        macro_db_path=None,
    )
    assert rec is None or rec.market == "KRW-BTC"


def test_cmd_crypto_daily_plan_broker_flag_in_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _force_bithumb_demo(monkeypatch)
    rc = cmd_crypto_daily_plan(_daily_plan_args(tmp_path, broker="bithumb"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "broker=Bithumb" in out
    assert "CRYPTO_PLAN_NO_RECOMMENDATION" in out or '"plan_json"' in out
    start = out.find('{\n  "status"')
    if start >= 0:
        doc = json.loads(out[start:])
        assert doc.get("status") == "CRYPTO_PLAN_NO_RECOMMENDATION" or doc.get("plan_json")
