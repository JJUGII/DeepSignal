"""Crypto exchange broker factory."""

from __future__ import annotations

from deepsignal.crypto_trading.broker.interface import CryptoBroker
from deepsignal.crypto_trading.broker.selection import normalize_crypto_broker_name


def load_crypto_broker_from_env(*, dry_run: bool | None = None) -> CryptoBroker:
    return load_crypto_broker(normalize_crypto_broker_name(), dry_run=dry_run)


def load_crypto_broker(
    name: str,
    *,
    dry_run: bool | None = None,
) -> CryptoBroker:
    broker = str(name or "upbit").strip().lower()
    if broker == "upbit":
        from deepsignal.crypto_trading.broker.broker import UpbitBroker
        from deepsignal.crypto_trading.broker.config import load_upbit_config_from_env

        return UpbitBroker(load_upbit_config_from_env(dry_run=dry_run))
    if broker == "bithumb":
        from deepsignal.crypto_trading.broker.bithumb.broker import BithumbBroker
        from deepsignal.crypto_trading.broker.bithumb.config import load_bithumb_config_from_env

        return BithumbBroker(load_bithumb_config_from_env(dry_run=dry_run))
    raise ValueError(f"unsupported crypto broker: {name!r}")
