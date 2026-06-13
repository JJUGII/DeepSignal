"""장 시작 시각 통합 매매 요약 — 코인·국내주식·해외주식 오늘자 체결을 텔레그램으로 보고.

두 시각에 동일 형식으로 발송:
- 국내장 시작(09:00 KST)
- 해외장 시작(22:30 KST)
각 시각마다 3개 시장(코인/국내/해외)의 '오늘' 체결을 모두 합쳐 요약한다.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))
_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _fmt_krw(v: float) -> str:
    return f"{round(v):,}원"


def _fmt_usd(v: float) -> str:
    return f"${v:,.2f}"


def _summarize(items: list[dict]) -> dict:
    """체결 목록 → 매수/매도 분리 집계."""
    def _side(x: dict) -> str:
        return str(x.get("side") or "").lower()
    buys = [x for x in items if _side(x) == "buy"]
    sells = [x for x in items if _side(x) == "sell"]
    def _amt(rows: list[dict]) -> float:
        return sum(float(x.get("trade_amount") or 0) for x in rows)
    fee = sum(float(x.get("fee") or 0) for x in items)
    return {
        "count": len(items),
        "buy": len(buys), "sell": len(sells),
        "buy_amount": _amt(buys), "sell_amount": _amt(sells),
        "amount": _amt(items), "fee": fee,
    }


def build_market_open_report(label: str = "") -> str:
    """오늘자 3개 시장 통합 매매 요약 텍스트(HTML) 생성."""
    # web_ui의 검증된 체결 조회 함수 재사용
    from deepsignal.web_ui.server import (
        _fetch_crypto_trades,
        _fetch_stock_trades,
        _fetch_overseas_trades,
    )

    today = datetime.now(_KST).date()
    d = today.isoformat()
    wd = _WEEKDAY_KR[today.weekday()]
    is_weekend = today.weekday() >= 5
    if is_weekend:
        # 토/일: 국내·미국장 휴장 — '국내장 시작' 라벨은 오해라 교체
        label = "주말 요약 (국내·미국장 휴장)"

    try:
        crypto = _fetch_crypto_trades(d, d, type_filter="all", symbol="")
    except Exception:
        crypto = []
    try:
        stock = _fetch_stock_trades(d, d, type_filter="all", symbol="")
    except Exception:
        stock = []
    try:
        # 해외 체결의 주문일자는 미국 날짜(KST-1일) — 직전 미국 세션
        # (어젯밤 22:30~오늘 05:00 KST)을 포함하려면 어제~오늘로 조회.
        d_prev = (today - timedelta(days=1)).isoformat()
        overseas = _fetch_overseas_trades(d_prev, d, type_filter="all", symbol="")
    except Exception:
        overseas = []

    sc = _summarize(crypto)
    ss = _summarize(stock)
    so = _summarize(overseas)
    total = sc["count"] + ss["count"] + so["count"]

    header = f"📊 <b>[DeepSignal] 오늘의 매매 요약</b>\n📅 {d} ({wd})"
    if label:
        header += f" · {label}"

    def _block(icon: str, name: str, s: dict, usd: bool = False, closed: bool = False) -> str:
        if s["count"] == 0:
            note = " (휴장)" if closed else ""
            return f"{icon} <b>{name}</b>{note}\n  체결 없음"
        fmt = _fmt_usd if usd else _fmt_krw
        line = (f"{icon} <b>{name}</b>\n"
                f"  🔴 매수 {s['buy']}건 · {fmt(s['buy_amount'])}\n"
                f"  🔵 매도 {s['sell']}건 · {fmt(s['sell_amount'])}")
        if s["fee"] > 0:
            line += f"\n  수수료 {fmt(s['fee'])}"
        return line

    parts = [
        header,
        "",
        _block("🪙", "코인 (Upbit)", sc),
        "",
        _block("🇰🇷", "국내주식 (KIS)", ss, closed=is_weekend),
        "",
        _block("🌎", "해외주식 (KIS)", so, usd=True, closed=is_weekend),
        "",
        f"합계: 총 {total}건",
    ]
    return "\n".join(parts)


def send_market_open_report(label: str = "") -> dict:
    """요약을 생성해 텔레그램으로 발송. {ok, sent, message} 반환."""
    text = build_market_open_report(label)
    token = os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return {"ok": False, "sent": False, "message": "텔레그램 토큰/Chat ID 미설정", "text": text}
    try:
        from deepsignal.live_trading.telegram.approval import telegram_api_post
        telegram_api_post("sendMessage",
                          {"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                          bot_token=token)
        return {"ok": True, "sent": True, "message": "발송 완료", "text": text}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "sent": False, "message": f"발송 실패: {e}", "text": text}
