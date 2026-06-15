"""Backward-compatible shim. New path: deepsignal.crypto_trading.telegram.flow"""
from deepsignal.crypto_trading.telegram.flow import *  # noqa: F401, F403
from deepsignal.crypto_trading.telegram import flow as _flow

_plan_hash = _flow._plan_hash
_active_exchange_label = _flow._active_exchange_label
