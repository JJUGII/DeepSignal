"""KIS WebSocket 메시지 파서.

KIS WS 메시지는 두 종류:
  1. JSON 문자열 → 제어 메시지 (구독 응답, PINGPONG)
  2. 파이프 구분 텍스트 → 실시간 데이터
     형식: "{암호화여부}|{TR_ID}|{건수}|{^구분 데이터}"
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from deepsignal.market_data.kis_stream.models import (
    KisOrderBookLevel,
    KisOrderBookSnapshot,
    KisTradeTick,
)

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc

# H0STCNT0 필드 인덱스 (KIS 문서 기준)
_F_SYMBOL = 0
_F_HOUR = 1        # HHMMSS
_F_PRICE = 2       # 체결가
_F_PRDY_SIGN = 3   # 전일대비부호
_F_PRDY = 4        # 전일대비
_F_PRDY_RATE = 5   # 전일대비율
_F_WGHN_AVG = 6    # 가중평균주가
_F_OPEN = 7        # 시가
_F_HIGH = 8        # 고가
_F_LOW = 9         # 저가
_F_ASK1 = 10       # 매도1호가
_F_BID1 = 11       # 매수1호가
_F_VOL = 12        # 체결거래량
_F_ACML_VOL = 13   # 누적거래량
_F_ACML_VAL = 14   # 누적거래대금
_F_SELL_CNT = 15   # 매도체결건수
_F_BUY_CNT = 16    # 매수체결건수
_F_NET_CNT = 17    # 순매수건수
_F_STRENGTH = 18   # 체결강도
_F_SELL_TOT = 19   # 총매도수량
_F_BUY_TOT = 20    # 총매수수량
_F_CCLS_DVSN = 21  # 체결구분 (1=매도, 5=매수) -- 주의: 문서마다 다름

# H0STASP0 필드 인덱스
_OB_SYMBOL = 0
_OB_HOUR = 1
_OB_HOUR_CLS = 2
# 매도호가 3~7 (ask1~ask5)
_OB_ASK_START = 3
# 매수호가 8~12 (bid1~bid5)
_OB_BID_START = 8
# 매도잔량 13~17
_OB_ASK_QTY_START = 13
# 매수잔량 18~22
_OB_BID_QTY_START = 18
# 총 매도/매수 잔량
_OB_TOTAL_ASK_QTY = 23
_OB_TOTAL_BID_QTY = 24


def _today_ts_ms(hhmmss: str) -> int:
    """오늘 날짜 + HHMMSS → epoch ms (KST)."""
    try:
        now_kst = datetime.now(KST)
        h = int(hhmmss[0:2])
        m = int(hhmmss[2:4])
        s = int(hhmmss[4:6])
        dt = now_kst.replace(hour=h, minute=m, second=s, microsecond=0)
        return int(dt.timestamp() * 1000)
    except Exception:
        return int(time.time() * 1000)


def _safe_int(v: str, default: int = 0) -> int:
    try:
        return int(v.replace(",", "").replace("+", "").replace("-", "").strip() or default)
    except (ValueError, AttributeError):
        return default


def _safe_float(v: str, default: float = 0.0) -> float:
    try:
        return float(v.replace(",", "").strip() or default)
    except (ValueError, AttributeError):
        return default


def parse_message(raw: str) -> tuple[str | None, dict | KisTradeTick | KisOrderBookSnapshot | None]:
    """KIS WS 원시 메시지 파싱.

    Returns:
        (msg_type, payload)
        msg_type: "control" | "trade" | "orderbook" | "unknown"
        payload: JSON dict for control, KisTradeTick/KisOrderBookSnapshot for data
    """
    raw = raw.strip()
    if not raw:
        return None, None

    # JSON 제어 메시지
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            return "control", data
        except json.JSONDecodeError:
            return "unknown", None

    # 파이프 구분 데이터
    parts = raw.split("|", 3)
    if len(parts) < 4:
        return "unknown", None

    encrypted, tr_id, _cnt, data_str = parts[0], parts[1], parts[2], parts[3]

    if encrypted == "1":
        # AES 암호화된 메시지는 현재 미지원 (실전 일부 TR)
        logger.debug("암호화 메시지 수신 (TR=%s) — 스킵", tr_id)
        return "encrypted", None

    fields = data_str.split("^")

    if tr_id == "H0STCNT0":
        tick = _parse_trade_tick(fields)
        if tick:
            return "trade", tick
        return "unknown", None

    if tr_id == "H0STASP0":
        ob = _parse_orderbook(fields)
        if ob:
            return "orderbook", ob
        return "unknown", None

    if tr_id == "H0UPCNT0":
        # 업종지수 체결 — field[0]=코드, field[1]=HHMMSS, field[2]=현재가(float)
        idx = _parse_index_price(fields)
        if idx:
            return "index", idx
        return "unknown", None

    return "unknown", None


def _parse_index_price(f: list[str]) -> dict | None:
    """H0UPCNT0 업종지수 현재가 파싱 — 최소 필드만 추출."""
    if len(f) < 3:
        return None
    try:
        symbol = f[0].strip()
        price = _safe_float(f[2])
        if price <= 0:
            return None
        return {"symbol": symbol, "price": price, "ts_ms": _today_ts_ms(f[1])}
    except Exception:
        return None


def _parse_trade_tick(f: list[str]) -> KisTradeTick | None:
    if len(f) < 22:
        return None
    try:
        symbol = f[_F_SYMBOL].strip()
        if not symbol:
            return None
        ts_ms = _today_ts_ms(f[_F_HOUR])
        price = _safe_int(f[_F_PRICE])
        qty = _safe_int(f[_F_VOL])
        if price <= 0:
            return None
        # 체결구분: "1" = 매도체결, "5" = 매수체결 (KIS 기준)
        # 일부 버전은 "2" 매수. 안전하게 "5" 외는 매도로 처리
        ccls = f[_F_CCLS_DVSN].strip() if len(f) > _F_CCLS_DVSN else ""
        is_buyer = ccls in ("5",)
        return KisTradeTick(
            symbol=symbol,
            price=price,
            qty=max(0, qty),
            ts_ms=ts_ms,
            is_buyer=is_buyer,
            ask_price=_safe_int(f[_F_ASK1]),
            bid_price=_safe_int(f[_F_BID1]),
            acml_vol=_safe_int(f[_F_ACML_VOL]),
            acml_val=_safe_int(f[_F_ACML_VAL]),
            open_price=_safe_int(f[_F_OPEN]),
            high_price=_safe_int(f[_F_HIGH]),
            low_price=_safe_int(f[_F_LOW]),
            strength=_safe_float(f[_F_STRENGTH]),
        )
    except Exception as exc:
        logger.debug("체결 틱 파싱 실패: %s", exc)
        return None


def _parse_orderbook(f: list[str]) -> KisOrderBookSnapshot | None:
    if len(f) < 25:
        return None
    try:
        symbol = f[_OB_SYMBOL].strip()
        if not symbol:
            return None
        ts_ms = _today_ts_ms(f[_OB_HOUR])
        asks = [
            KisOrderBookLevel(
                price=_safe_int(f[_OB_ASK_START + i]),
                qty=_safe_int(f[_OB_ASK_QTY_START + i]),
            )
            for i in range(5)
            if len(f) > _OB_ASK_QTY_START + i and _safe_int(f[_OB_ASK_START + i]) > 0
        ]
        bids = [
            KisOrderBookLevel(
                price=_safe_int(f[_OB_BID_START + i]),
                qty=_safe_int(f[_OB_BID_QTY_START + i]),
            )
            for i in range(5)
            if len(f) > _OB_BID_QTY_START + i and _safe_int(f[_OB_BID_START + i]) > 0
        ]
        return KisOrderBookSnapshot(
            symbol=symbol,
            ts_ms=ts_ms,
            asks=asks,
            bids=bids,
            total_ask_qty=_safe_int(f[_OB_TOTAL_ASK_QTY]) if len(f) > _OB_TOTAL_ASK_QTY else 0,
            total_bid_qty=_safe_int(f[_OB_TOTAL_BID_QTY]) if len(f) > _OB_TOTAL_BID_QTY else 0,
        )
    except Exception as exc:
        logger.debug("호가 파싱 실패: %s", exc)
        return None
