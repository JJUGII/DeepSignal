"""Backward-compatible shim. New path: deepsignal.crypto_trading.data.stream_stale_alert"""
from deepsignal.crypto_trading.crypto_telegram_flow import telegram_send_plain
from deepsignal.crypto_trading.data.stream_stale_alert import *  # noqa: F401, F403
