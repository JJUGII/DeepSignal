"""
crypto_order_manager.py — 미체결 지정가 주문 추적 및 관리.

[매도] 주문가 > 현재가 + CHASE_GAP_PCT(1%) → 취소 후 현재가로 재접수 (최대 10회)
[매수] 현재가 > 주문가 + BUY_CANCEL_GAP_PCT(1%) OR 30분 경과 → 취소만, 재매수 없음
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepsignal.crypto_trading.upbit_broker import UpbitBroker

logger = logging.getLogger(__name__)

# ── 매도 관련 상수
CHASE_GAP_PCT = 0.01        # 주문가가 현재가보다 1% 이상 높으면 재접수
MAX_CHASE_COUNT = 10        # 최대 재접수 횟수

# ── 매수 관련 상수
BUY_CANCEL_GAP_PCT = 0.01  # 현재가가 매수 주문가보다 1% 이상 높으면 취소
BUY_CANCEL_MINUTES = 30.0  # 30분 이상 미체결이면 취소

_LOG_FILE = "crypto_order_manager_log.jsonl"


def _load_chase_counts(output_dir: Path) -> dict[str, int]:
    """uuid → 재접수 횟수 추적 파일 로드."""
    p = output_dir / "crypto_order_manager_state.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_chase_counts(output_dir: Path, counts: dict[str, int]) -> None:
    p = output_dir / "crypto_order_manager_state.json"
    p.write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_log(output_dir: Path, entry: dict[str, Any]) -> None:
    p = output_dir / _LOG_FILE
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _round_price(price: float, market: str) -> float:
    """업비트 KRW 마켓 호가 단위 반올림."""
    if price >= 2_000_000:
        return round(price / 1000) * 1000
    if price >= 1_000_000:
        return round(price / 500) * 500
    if price >= 500_000:
        return round(price / 100) * 100
    if price >= 100_000:
        return round(price / 50) * 50
    if price >= 10_000:
        return round(price / 10) * 10
    if price >= 1_000:
        return round(price / 5) * 5
    if price >= 100:
        return round(price)
    if price >= 10:
        return round(price * 10) / 10
    return round(price * 100) / 100


def manage_open_sell_orders(
    broker: "UpbitBroker",
    output_dir: str | Path,
    *,
    execute: bool = False,
) -> list[dict[str, Any]]:
    """
    미체결 매도 주문을 점검하고, 가격이 현재가보다 CHASE_GAP_PCT 이상 높으면
    취소 후 현재가로 재접수한다.

    Returns:
        취한 액션 목록 (로그용).
    """
    out = Path(output_dir)
    open_orders = broker.get_open_orders()
    # 업비트: side='ask' = 매도, side='bid' = 매수
    sell_orders = [o for o in open_orders if o.get("side") == "ask"]

    if not sell_orders:
        return []

    chase_counts = _load_chase_counts(out)
    actions: list[dict[str, Any]] = []

    # 현재가 일괄 조회 (API 호출 최소화)
    markets = list({o["market"] for o in sell_orders})
    try:
        tickers = broker.get_tickers(markets)
    except Exception as e:
        logger.warning("order_manager: ticker fetch failed: %s", e)
        return []

    now_iso = datetime.now().astimezone().isoformat()

    for order in sell_orders:
        uuid = order.get("uuid", "")
        market = order.get("market", "")
        limit_price = float(order.get("price") or 0)
        remaining_volume = float(order.get("remaining_volume") or 0)

        if not uuid or not market or limit_price <= 0 or remaining_volume <= 0:
            continue

        ticker = tickers.get(market)
        if ticker is None:
            continue
        current_price = float(ticker.trade_price or 0)
        if current_price <= 0:
            continue

        gap = (limit_price - current_price) / current_price
        if gap <= CHASE_GAP_PCT:
            # 주문가격이 현재가에 근접 → 체결 대기 중, 개입 불필요
            continue

        chase_count = chase_counts.get(uuid, 0)
        if chase_count >= MAX_CHASE_COUNT:
            logger.warning(
                "order_manager: %s uuid=%s reached max chase count %d, skipping",
                market, uuid[:8], MAX_CHASE_COUNT,
            )
            _append_log(out, {
                "ts": now_iso, "action": "max_chase_reached",
                "market": market, "uuid": uuid, "limit_price": limit_price,
                "current_price": current_price, "gap_pct": round(gap * 100, 2),
            })
            continue

        logger.info(
            "order_manager: %s sell@%.1f >> current %.1f (gap %.1f%%) → cancel+rechase [%d/%d]",
            market, limit_price, current_price, gap * 100, chase_count + 1, MAX_CHASE_COUNT,
        )

        # 1) 기존 주문 취소
        import time as _time
        try:
            broker.cancel_order(uuid)
        except Exception as e:
            logger.warning("order_manager: cancel failed %s: %s", uuid[:8], e)
            _append_log(out, {
                "ts": now_iso, "action": "cancel_failed",
                "market": market, "uuid": uuid, "error": str(e),
            })
            continue

        # 취소 후 업비트 잔고 반영 대기 (즉시 재접수 시 available=0 오류 방지)
        _time.sleep(1.5)

        # 2) 현재가로 재접수
        new_price = _round_price(current_price, market)
        new_uuid: str | None = None
        try:
            result = broker.sell_limit(market, remaining_volume, new_price, execute=execute)
            new_uuid = result.uuid if result else None
        except Exception as e:
            logger.warning("order_manager: rechase place_order failed %s: %s", market, e)
            _append_log(out, {
                "ts": now_iso, "action": "rechase_failed",
                "market": market, "old_uuid": uuid,
                "new_price": new_price, "error": str(e),
            })
            continue

        # 3) 재접수 횟수 업데이트 (새 uuid 기준으로 초기화, 기존 uuid 제거)
        chase_counts.pop(uuid, None)
        if new_uuid:
            chase_counts[new_uuid] = chase_count + 1

        action: dict[str, Any] = {
            "ts": now_iso,
            "action": "rechased",
            "market": market,
            "old_uuid": uuid,
            "new_uuid": new_uuid,
            "old_price": limit_price,
            "new_price": new_price,
            "volume": remaining_volume,
            "gap_pct": round(gap * 100, 2),
            "chase_count": chase_count + 1,
            "execute": execute,
        }
        actions.append(action)
        _append_log(out, action)
        logger.info(
            "order_manager: rechased %s %.4f @ %.1f (was %.1f, gap %.1f%%)",
            market, remaining_volume, new_price, limit_price, gap * 100,
        )

    _save_chase_counts(out, chase_counts)
    return actions


def manage_open_buy_orders(
    broker: "UpbitBroker",
    output_dir: str | Path,
    *,
    execute: bool = False,
) -> list[dict[str, Any]]:
    """
    미체결 매수 주문 점검.

    아래 조건 중 하나라도 충족하면 취소 (재매수 없음 — 분석 엔진에 위임):
      1) 현재가 > 주문가 × (1 + BUY_CANCEL_GAP_PCT)  → 시장이 올라가 버려 영원히 안 채결
      2) 주문 경과 시간 ≥ BUY_CANCEL_MINUTES          → 너무 오래된 판단 기준

    Returns:
        취소된 주문 정보 목록 (로그용).
    """
    out = Path(output_dir)
    open_orders = broker.get_open_orders()
    buy_orders = [o for o in open_orders if o.get("side") == "bid"]

    if not buy_orders:
        return []

    markets = list({o["market"] for o in buy_orders})
    try:
        tickers = broker.get_tickers(markets)
    except Exception as e:
        logger.warning("order_manager(buy): ticker fetch failed: %s", e)
        return []

    now_dt = datetime.now(tz=timezone.utc)
    now_iso = now_dt.astimezone().isoformat()
    actions: list[dict[str, Any]] = []

    for order in buy_orders:
        uuid = order.get("uuid", "")
        market = order.get("market", "")
        limit_price = float(order.get("price") or 0)
        remaining_volume = float(order.get("remaining_volume") or 0)

        if not uuid or not market or limit_price <= 0 or remaining_volume <= 0:
            continue

        # 주문 경과 시간 계산
        created_raw = order.get("created_at") or ""
        elapsed_minutes = 0.0
        if created_raw:
            try:
                created_dt = datetime.fromisoformat(str(created_raw))
                elapsed_minutes = (now_dt - created_dt.astimezone(timezone.utc)).total_seconds() / 60.0
            except Exception:
                elapsed_minutes = 0.0

        ticker = tickers.get(market)
        if ticker is None:
            continue
        current_price = float(ticker.trade_price or 0)
        if current_price <= 0:
            continue

        # 취소 조건 판단
        price_gap = (current_price - limit_price) / limit_price  # 양수 = 시장이 주문가 위
        cancel_reason: str | None = None
        if price_gap >= BUY_CANCEL_GAP_PCT:
            cancel_reason = f"price_above_limit ({price_gap * 100:.1f}% gap)"
        elif elapsed_minutes >= BUY_CANCEL_MINUTES:
            cancel_reason = f"timeout ({elapsed_minutes:.0f}min)"

        if cancel_reason is None:
            continue

        logger.info(
            "order_manager(buy): %s buy@%.1f current %.1f → cancel [%s]",
            market, limit_price, current_price, cancel_reason,
        )

        if execute:
            try:
                broker.cancel_order(uuid)
            except Exception as e:
                logger.warning("order_manager(buy): cancel failed %s: %s", uuid[:8], e)
                _append_log(out, {
                    "ts": now_iso, "action": "buy_cancel_failed",
                    "market": market, "uuid": uuid,
                    "reason": cancel_reason, "error": str(e),
                })
                continue

        action: dict[str, Any] = {
            "ts": now_iso,
            "action": "buy_cancelled",
            "market": market,
            "uuid": uuid,
            "limit_price": limit_price,
            "current_price": current_price,
            "gap_pct": round(price_gap * 100, 2),
            "elapsed_minutes": round(elapsed_minutes, 1),
            "cancel_reason": cancel_reason,
            "execute": execute,
        }
        actions.append(action)
        _append_log(out, action)
        logger.info(
            "order_manager(buy): cancelled %s buy@%.1f (reason: %s)",
            market, limit_price, cancel_reason,
        )

    return actions
