"""CRYPTO_PAPER_MODE guards."""

from __future__ import annotations

import os

import pytest

from deepsignal.crypto_trading.crypto_paper_mode import (
    CryptoPaperModeError,
    crypto_paper_mode_enabled,
    effective_dry_run,
    require_live_trading_allowed,
)
from deepsignal.crypto_trading.upbit_broker import UpbitBroker
from deepsignal.crypto_trading.upbit_config import UpbitConfig, load_upbit_config_from_env


@pytest.fixture(autouse=True)
def _clear_paper_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("CRYPTO_PAPER_MODE", "DEEPSIGNAL_CRYPTO_PAPER_MODE", "UPBIT_DRY_RUN"):
        monkeypatch.delenv(key, raising=False)


def test_paper_mode_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    assert crypto_paper_mode_enabled() is True
    assert effective_dry_run(requested_dry_run=False) is True


def test_paper_mode_explicit_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_PAPER_MODE", "false")
    assert crypto_paper_mode_enabled() is False
    assert effective_dry_run(requested_dry_run=False) is False


def test_require_live_blocked_when_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_PAPER_MODE", "true")
    with pytest.raises(CryptoPaperModeError):
        require_live_trading_allowed()


def test_load_upbit_forces_dry_run_under_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_PAPER_MODE", "true")
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "test-access-key-12345")
    monkeypatch.setenv("UPBIT_SECRET_KEY", "test-secret-key-12345")
    monkeypatch.setenv("UPBIT_DRY_RUN", "false")
    cfg = load_upbit_config_from_env(dry_run=False)
    assert cfg.paper_mode is True
    assert cfg.dry_run is True


def test_broker_buy_blocked_in_paper_mode() -> None:
    cfg = UpbitConfig(
        access_key="dry-run-key",
        secret_key="dry-run-secret",
        dry_run=True,
        paper_mode=True,
    )
    br = UpbitBroker(cfg)
    res = br.place_limit_buy(market="KRW-BTC", krw_amount=10_000, price=50_000_000, execute=True)
    assert res.dry_run is True
    assert res.status == "CRYPTO_PAPER_MODE_BLOCKED"
