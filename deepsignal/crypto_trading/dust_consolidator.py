"""먼지 합병 — 거래소 최소(5천원) 미만이라 못 파는 잔량을 추가매수로 합쳐 전량매도.

1,000~5,000원 잔량은 업비트가 매도를 거부(최소주문 5천원)해 자본이 묶인다.
약 5천원어치를 추가매수해 합계를 5천원 위로 올린 뒤 전량매도해 현금화한다.
매수→매도 왕복(수수료·스프레드 ~1%)이라 1,000원 미만은 회수가치 < 비용 → 제외.

기본 OFF. CRYPTO_DUST_CONSOLIDATE=true 일 때만 동작. 실주문은 execute=True에서만.
"""

from __future__ import annotations

import os
import time
from typing import Any

from deepsignal.crypto_trading.crypto_sell_pricing import round_crypto_limit_price


def consolidate_enabled() -> bool:
    return os.environ.get("CRYPTO_DUST_CONSOLIDATE", "false").strip().lower() in ("1", "true", "yes", "on")


def _floor_krw() -> float:
    try:
        return float(os.environ.get("CRYPTO_DUST_CONSOLIDATE_FLOOR_KRW", "1000") or 1000)
    except ValueError:
        return 1000.0


def _topup_krw() -> float:
    """추가매수 금액 — 거래소 최소(5천)보다 약간 위로(체결·합계 보장)."""
    try:
        return max(5100.0, float(os.environ.get("CRYPTO_DUST_TOPUP_KRW", "5100") or 5100))
    except ValueError:
        return 5100.0


def consolidate_crypto_dust(broker: Any, *, execute: bool = False) -> dict[str, Any]:
    """1,000~5,000원 잔량을 추가매수→전량매도로 현금화. 결과 요약 반환."""
    from deepsignal.crypto_trading.broker.broker import _sell_min_order_krw
    sell_min = float(_sell_min_order_krw())   # 거래소 매도 최소(보통 5,000)
    floor = _floor_krw()
    topup = _topup_krw()
    actions: list[dict[str, Any]] = []

    try:
        holdings = broker.get_crypto_holdings()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"holdings 조회 실패: {exc}", "actions": []}

    for h in holdings:
        val = float(getattr(h, "valuation_krw", 0) or 0)
        if not (floor <= val < sell_min):   # 합병 대상: 회수가치 있고 못 파는 구간만
            continue
        market = str(h.market)
        rec: dict[str, Any] = {"market": market, "value_krw": round(val), "execute": execute}
        try:
            tk = broker.get_ticker(market)
            cur = float(getattr(tk, "trade_price", 0) or 0)
            if cur <= 0:
                rec["status"] = "no_price"; actions.append(rec); continue
            # ① 추가매수 (체결 위해 현재가 +0.5% 공격적 지정가)
            buy_px = round_crypto_limit_price(cur * 1.005)
            if not execute:
                rec["status"] = "dry_run"; rec["plan"] = f"매수 {topup:,.0f}원 @ {buy_px:,.2f} → 합계 후 전량매도"
                actions.append(rec); continue
            buy = broker.place_limit_buy(market=market, krw_amount=topup, price=buy_px, execute=True)
            rec["buy_status"] = getattr(buy, "status", "?")
            # 체결 대기 (잔고 반영)
            time.sleep(2.0)
            # ② 전량매도 (현재가 -0.5% 공격적 지정가)
            h2 = next((x for x in broker.get_crypto_holdings() if x.market == market), None)
            bal = float(getattr(h2, "balance", 0) or 0) if h2 else 0.0
            if bal <= 0:
                rec["status"] = "buy_unfilled"; actions.append(rec); continue
            sell_px = round_crypto_limit_price(cur * 0.995)
            if bal * sell_px < sell_min:   # 추가매수 미체결 등으로 여전히 미달
                rec["status"] = "still_below_min"; actions.append(rec); continue
            sell = broker.place_limit_sell(market=market, volume=bal, price=sell_px, execute=True)
            rec["sell_status"] = getattr(sell, "status", "?")
            rec["status"] = "consolidated"
        except Exception as exc:  # noqa: BLE001
            rec["status"] = f"error: {str(exc)[:60]}"
        actions.append(rec)

    return {
        "enabled": consolidate_enabled(),
        "sell_min_krw": sell_min,
        "floor_krw": floor,
        "topup_krw": topup,
        "candidates": len(actions),
        "actions": actions,
    }
