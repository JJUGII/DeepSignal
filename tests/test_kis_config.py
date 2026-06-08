"""KIS 환경 설정."""

from __future__ import annotations

import os

import pytest

from deepsignal.live_trading.kis_config import (
    KISConfig,
    KisConfigError,
    load_kis_config_from_env,
    validate_kis_config,
)


def test_load_kis_config_from_env_ok(monkeypatch) -> None:
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    monkeypatch.delenv("KIS_ACCOUNT_NO", raising=False)
    monkeypatch.delenv("KIS_ACCOUNT_PRODUCT_CODE", raising=False)
    monkeypatch.delenv("KIS_HTS_ID", raising=False)
    monkeypatch.delenv("KIS_ENV", raising=False)
    monkeypatch.setenv("KIS_APP_KEY", "dummy-app-key")
    monkeypatch.setenv("KIS_APP_SECRET", "dummy-app-secret")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_ENV", "paper")
    cfg = load_kis_config_from_env(load_dotenv_file=False)
    assert cfg.env == "paper"
    assert cfg.hts_id is None
    assert "openapivts" in cfg.base_url


def test_load_kis_config_missing_raises(monkeypatch) -> None:
    for k in (
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "KIS_ACCOUNT_NO",
        "KIS_ACCOUNT_PRODUCT_CODE",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KIS_APP_KEY", "x")
    monkeypatch.setenv("KIS_APP_SECRET", "y")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.delenv("KIS_ACCOUNT_PRODUCT_CODE", raising=False)
    with pytest.raises(KisConfigError, match="KIS_ACCOUNT_PRODUCT_CODE"):
        load_kis_config_from_env(load_dotenv_file=False)


def test_validate_kis_config_live_warning() -> None:
    cfg = KISConfig(
        app_key="k",
        app_secret="s",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="live",
    )
    errs, warns = validate_kis_config(cfg)
    assert not errs
    assert any("live" in w.lower() for w in warns)


def test_validate_kis_config_invalid_env() -> None:
    cfg = KISConfig(
        app_key="k",
        app_secret="s",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="sandbox",
    )
    errs, _ = validate_kis_config(cfg)
    assert errs


def test_base_url_live() -> None:
    cfg = KISConfig(
        app_key="k",
        app_secret="s",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="live",
    )
    assert "openapi.koreainvestment.com" in cfg.base_url


def test_hts_id_optional(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "a")
    monkeypatch.setenv("KIS_APP_SECRET", "b")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("KIS_ACCOUNT_PRODUCT_CODE", "01")
    monkeypatch.setenv("KIS_HTS_ID", "  myhts  ")
    cfg = load_kis_config_from_env(load_dotenv_file=False)
    assert cfg.hts_id == "myhts"
