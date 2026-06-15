"""Limit prices for crypto SELL orders (TP at target %, SL at stop %)."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_sell_triggers import SellTrigger
from deepsignal.crypto_trading.upbit_broker import CryptoHolding


def round_crypto_limit_price(price: float) -> float:
    """Upbit KRW 호가단위 격자로 스냅. 안 맞으면 invalid_price_ask로 거부된다.

    버그(실측): 1,000원 이상을 1원 단위로 반올림 → 10만~50만원대(호가 100원)
    AAVE 112,737 등이 무효가격으로 거부되어 매도가 안 됨. 정식 테이블로 교정.
    각 구간 tick은 실제 tick의 배수(coarser)라도 항상 유효하므로 보수적으로 잡음.
    """
    px = float(price)
    if px <= 0:
        return 0.0
    if px >= 1_000_000:
        tick = 1000.0
    elif px >= 500_000:
        tick = 500.0
    elif px >= 100_000:
        tick = 100.0
    elif px >= 50_000:
        tick = 50.0
    elif px >= 10_000:
        tick = 10.0
    elif px >= 1_000:
        tick = 5.0
    elif px >= 100:
        tick = 1.0
    elif px >= 10:
        tick = 0.1
    elif px >= 1:
        tick = 0.01
    elif px >= 0.1:
        tick = 0.001
    else:
        tick = 0.0001
    snapped = round(px / tick) * tick
    # 부동소수 잔차 제거 (tick 소수자릿수에 맞춰 반올림)
    import math as _m
    decimals = max(0, -int(_m.floor(_m.log10(tick)))) if tick < 1 else 0
    return round(snapped, decimals)


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
