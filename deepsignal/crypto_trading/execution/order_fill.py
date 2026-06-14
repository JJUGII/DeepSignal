"""Order fill polling and Telegram follow-up reports (Upbit/Bithumb)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from deepsignal.crypto_trading.broker.interface import CryptoBroker, CryptoOrderResult
from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
from deepsignal.live_trading.time_utils import now_kst

CRYPTO_ORDER_STATUS_PREFIX = "crypto_order_status_"

FillOutcome = Literal["done", "partial", "wait", "cancel", "timeout", "skipped"]


def market_currency(market: str) -> str:
    parts = market.upper().split("-")
    return parts[-1] if parts else market


def _float_field(raw: dict[str, Any], key: str) -> float:
    try:
        return float(raw.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_order_status(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize Upbit/Bithumb order payloads to a shared shape."""
    state = str(raw.get("state") or raw.get("status") or "").lower()
    if state in ("filled", "complete", "completed"):
        state = "done"
    return {
        "uuid": str(raw.get("uuid") or raw.get("order_id") or ""),
        "market": str(raw.get("market") or ""),
        "state": state,
        "side": str(raw.get("side") or ""),
        "price": _float_field(raw, "price") or _float_field(raw, "avg_price"),
        "volume": _float_field(raw, "volume"),
        "executed_volume": _float_field(raw, "executed_volume"),
        "remaining_volume": _float_field(raw, "remaining_volume"),
        "paid_fee": _float_field(raw, "paid_fee"),
        "remaining_fee": _float_field(raw, "remaining_fee"),
        "trades_count": int(raw.get("trades_count", 0) or 0),
        "raw": raw,
    }


def is_partial_fill(status: dict[str, Any]) -> bool:
    executed = float(status.get("executed_volume", 0) or 0)
    remaining = float(status.get("remaining_volume", 0) or 0)
    state = str(status.get("state", ""))
    return executed > 0 and remaining > 0 and state not in ("done", "cancel")


def classify_fill_outcome(status: dict[str, Any], *, timed_out: bool) -> FillOutcome:
    state = str(status.get("state", ""))
    if state == "done":
        return "done"
    if state == "cancel":
        return "cancel"
    if is_partial_fill(status):
        return "partial"
    if timed_out:
        return "wait" if float(status.get("executed_volume", 0) or 0) <= 0 else "partial"
    return "wait"


def _coin_symbol(market: str) -> str:
    """'KRW-MEGA' → 'MEGA'"""
    parts = market.upper().split("-")
    return parts[-1] if len(parts) >= 2 else market


# 주요 코인 한국어 이름
_COIN_KR_NAME: dict[str, str] = {
    "BTC": "비트코인", "ETH": "이더리움", "XRP": "리플", "SOL": "솔라나",
    "ADA": "에이다", "DOGE": "도지코인", "AVAX": "아발란체", "DOT": "폴카닷",
    "LINK": "체인링크", "MATIC": "폴리곤", "TRX": "트론", "SHIB": "시바이누",
    "LTC": "라이트코인", "BCH": "비트코인캐시", "ATOM": "코스모스",
    "NEAR": "니어프로토콜", "UNI": "유니스왑", "USDT": "테더", "USDC": "USD코인",
    "XLM": "스텔라루멘", "ALGO": "알고랜드", "FIL": "파일코인", "ICP": "인터넷컴퓨터",
    "ETC": "이더리움클래식", "HBAR": "헤데라", "SAND": "더샌드박스", "MANA": "디센트럴랜드",
    "APT": "앱토스", "ARB": "아비트럼", "OP": "옵티미즘", "SUI": "수이",
    "SAHARA": "사하라", "MEGA": "메가", "TRUMP": "트럼프",
}


def _coin_name_kr(market: str) -> str:
    sym = _coin_symbol(market)
    return _COIN_KR_NAME.get(sym, sym)


def _now_kst_iso() -> str:
    return now_kst().isoformat(timespec="seconds")


def format_fill_message_done(plan: CryptoOrderPlan, status: dict[str, Any]) -> str:
    cur    = _coin_symbol(plan.market)
    side   = str(getattr(plan, "side", "buy")).lower()
    is_buy = side != "sell"
    icon   = "📈" if is_buy else "📉"
    side_ko = "매수" if is_buy else "매도"
    price  = float(status.get("price", 0) or plan.limit_price)
    volume = float(status.get("executed_volume", 0) or 0)
    fee    = float(status.get("paid_fee", 0) or 0)
    amount = volume * price
    kr_name = _coin_name_kr(plan.market)
    lines = [
        f"{icon} <b>[코인] {side_ko} 체결</b>",
        f"종목: {plan.market} ({kr_name})",
        f"수량: {volume:g} {cur} × {price:,.0f}원",
        f"금액: {amount:,.0f}원  수수료 {fee:,.2f}원",
        f"시각: {_now_kst_iso()}",
    ]
    return "\n".join(lines)


def format_fill_message_wait(plan: CryptoOrderPlan, status: dict[str, Any]) -> str:
    cur       = _coin_symbol(plan.market)
    side      = str(getattr(plan, "side", "buy")).lower()
    is_buy    = side != "sell"
    remaining = float(status.get("remaining_volume", 0) or 0)
    price     = float(status.get("price", 0) or plan.limit_price)
    kr_name   = _coin_name_kr(plan.market)
    lines = [
        f"⏳ <b>[코인] {'매수' if is_buy else '매도'} 미체결</b>",
        f"종목: {plan.market} ({kr_name})",
        f"지정가 {price:,.0f}원  ·  잔여 {remaining:g} {cur}",
        f"시각: {_now_kst_iso()}",
    ]
    return "\n".join(lines)


def format_fill_message_partial(plan: CryptoOrderPlan, status: dict[str, Any]) -> str:
    cur       = _coin_symbol(plan.market)
    side      = str(getattr(plan, "side", "buy")).lower()
    is_buy    = side != "sell"
    executed  = float(status.get("executed_volume", 0) or 0)
    remaining = float(status.get("remaining_volume", 0) or 0)
    price     = float(status.get("price", 0) or plan.limit_price)
    kr_name   = _coin_name_kr(plan.market)
    lines = [
        f"🟡 <b>[코인] {'매수' if is_buy else '매도'} 부분체결</b>",
        f"종목: {plan.market} ({kr_name})",
        f"체결 {executed:g} {cur}  ·  잔여 {remaining:g} {cur}",
        f"지정가 {price:,.0f}원",
        f"시각: {_now_kst_iso()}",
    ]
    return "\n".join(lines)


def format_fill_message_cancel(plan: CryptoOrderPlan, status: dict[str, Any]) -> str:
    side   = str(getattr(plan, "side", "buy")).lower()
    is_buy = side != "sell"
    kr_name = _coin_name_kr(plan.market)
    lines = [
        f"⚪ <b>[코인] {'매수' if is_buy else '매도'} 취소</b>",
        f"종목: {plan.market} ({kr_name})",
        f"시각: {_now_kst_iso()}",
    ]
    return "\n".join(lines)


def format_fill_message_for_outcome(plan: CryptoOrderPlan, status: dict[str, Any], outcome: FillOutcome) -> str:
    if outcome == "done":
        return format_fill_message_done(plan, status)
    if outcome == "cancel":
        return format_fill_message_cancel(plan, status)
    if outcome == "partial":
        return format_fill_message_partial(plan, status)
    return format_fill_message_wait(plan, status)


def write_order_status_audit(output_dir: str | Path, payload: dict[str, Any]) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = now_kst().strftime("%Y%m%d_%H%M%S")
    path = out / f"{CRYPTO_ORDER_STATUS_PREFIX}{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def poll_order_fill(
    broker: CryptoBroker,
    uuid: str,
    *,
    wait_fill_seconds: float,
    fill_poll_interval: float,
) -> tuple[dict[str, Any], FillOutcome]:
    if not uuid or wait_fill_seconds <= 0:
        return {}, "skipped"

    deadline = time.time() + max(wait_fill_seconds, 0.0)
    last: dict[str, Any] = {}
    interval = max(fill_poll_interval, 0.5)

    while time.time() < deadline:
        raw = broker.get_order(uuid)
        last = normalize_order_status(raw)
        state = str(last.get("state", ""))
        if state == "done":
            return last, "done"
        if state == "cancel":
            return last, "cancel"
        if is_partial_fill(last):
            pass
        time.sleep(interval)

    outcome = classify_fill_outcome(last, timed_out=True)
    return last, outcome


def build_fill_audit(
    plan: CryptoOrderPlan,
    result: CryptoOrderResult,
    status: dict[str, Any],
    outcome: FillOutcome,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    if output_dir and outcome in ("done", "partial") and float(status.get("price", 0) or 0) > 0:
        try:
            from deepsignal.crypto_trading.crypto_execution_quality import record_fill_slippage_feedback

            fill_px = float(status.get("price", 0) or plan.limit_price)
            order_krw = float(plan.krw_amount or 0) or fill_px * float(status.get("executed_volume", 0) or 0)
            record_fill_slippage_feedback(
                output_dir,
                market=plan.market,
                side=plan.side,
                limit_price=float(plan.limit_price or fill_px),
                fill_price=fill_px,
                order_krw=order_krw,
            )
        except Exception:
            pass
    return {
        "uuid": status.get("uuid") or result.uuid,
        "market": status.get("market") or plan.market,
        "state": status.get("state"),
        "executed_volume": status.get("executed_volume"),
        "remaining_volume": status.get("remaining_volume"),
        "paid_fee": status.get("paid_fee"),
        "trades_count": status.get("trades_count"),
        "fill_outcome": outcome,
        "plan": plan.to_dict(),
        "order_result": {
            "status": result.status,
            "uuid": result.uuid,
            "krw_amount": result.krw_amount,
            "price": result.price,
            "volume": result.volume,
        },
        "raw": status.get("raw"),
    }
