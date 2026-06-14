"""Backward-compatible shim. New path: deepsignal.crypto_trading.telegram.menu"""
from deepsignal.crypto_trading.telegram.menu import *  # noqa: F401, F403
from deepsignal.crypto_trading.telegram import menu as _menu

_send_menu_text = _menu._send_menu_text
