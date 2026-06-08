"""Format Upbit crypto holdings for CLI output."""

from __future__ import annotations

from typing import Any

from deepsignal.crypto_trading.crypto_recommendation import MARKET_DISPLAY_KO
from deepsignal.crypto_trading.upbit_broker import CryptoHolding


def holding_to_dict(h: CryptoHolding) -> dict[str, Any]:
    return {
        "market": h.market,
        "currency": h.currency,
        "balance": h.balance,
        "locked": h.locked,
        "total_quantity": h.total_quantity,
        "available": h.available,
        "avg_buy_price": h.avg_buy_price,
        "current_price": h.current_price,
        "valuation_krw": h.valuation_krw,
        "pnl_pct": h.pnl_pct,
        "pnl_krw": h.pnl_krw,
    }


def format_holdings_console(holdings: list[CryptoHolding]) -> list[str]:
    lines = ["Holdings:"]
    if not holdings:
        lines.append("- (보유 코인 없음)")
        return lines
    for h in holdings:
        name = MARKET_DISPLAY_KO.get(h.market, h.currency)
        qty = h.total_quantity
        lines.append(
            f"- {h.currency} {name}: {qty:.8f}개 "
            f"/ 평균 {h.avg_buy_price:,.0f}원 "
            f"/ 현재 {h.current_price:,.0f}원 "
            f"/ 평가 {h.valuation_krw:,.0f}원 "
            f"/ 수익률 {h.pnl_pct:+.2f}%"
        )
        if h.locked > 0:
            lines.append(f"  (가용 {h.available:.8f} / 주문중 {h.locked:.8f})")
    return lines
