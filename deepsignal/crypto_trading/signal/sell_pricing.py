"""Limit prices for crypto SELL orders (TP at target %, SL at stop %)."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_sell_triggers import SellTrigger
from deepsignal.crypto_trading.upbit_broker import CryptoHolding


def round_crypto_limit_price(price: float) -> float:
    """Upbit KRW 호가단위 반올림 (2023.10 개편 — 1,000원 미만 세분화 반영).

    구버전 테이블은 100~1,000원 구간을 1원 단위로 깎아 저가 알트에서
    최대 ~1% 가격 오차(매수 미체결·매도 헐값)를 만들었다.
    1,000원 이상 구간은 기존(실거래 검증됨) 유지.
    """
    px = float(price)
    if px <= 0:
        return 0.0
    if px >= 1_000_000:
        return float(int(round(px / 1000.0)) * 1000)
    if px >= 1_000:
        return float(int(round(px)))                   # 1k~1M: 1원
    if px >= 100:
        return round(round(px * 10.0) / 10.0, 1)       # 100~1k: 0.1원
    if px >= 10:
        return round(round(px * 100.0) / 100.0, 2)     # 10~100: 0.01원
    if px >= 1:
        return round(round(px * 1000.0) / 1000.0, 3)   # 1~10: 0.001원
    return round(round(px * 10000.0) / 10000.0, 4)     # <1: 0.0001원


def compute_sell_limit_price(
    holding: CryptoHolding,
    trigger: SellTrigger | str,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> float:
    """Map sell trigger to limit price (not always current trade_price)."""
    avg = float(holding.avg_buy_price or 0)
    cur = float(holding.current_price or 0)
    if avg <= 0:
        return round_crypto_limit_price(cur)

    trig = str(trigger or "").lower()
    tp = float(take_profit_pct)
    sl = float(stop_loss_pct)
    pnl = float(holding.pnl_pct or 0)

    if trig in ("take_profit", "near_take_profit"):
        target = avg * (1.0 + tp / 100.0)
        if trig == "take_profit" or pnl >= tp:
            return round_crypto_limit_price(max(cur, target))
        return round_crypto_limit_price(target)

    if trig in ("stop_loss", "near_stop_loss"):
        target = avg * (1.0 + sl / 100.0)
        if trig == "stop_loss" or pnl <= sl:
            return round_crypto_limit_price(min(cur, target) if target > 0 else cur)
        return round_crypto_limit_price(target)

    return round_crypto_limit_price(cur)
