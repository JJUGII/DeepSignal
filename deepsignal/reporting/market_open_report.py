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
    """체결 목록 → {count, buy, sell, amount, fee}."""
    buy = sum(1 for x in items if str(x.get("side")).lower() == "buy")
    sell = sum(1 for x in items if str(x.get("side")).lower() == "sell")
    amount = sum(float(x.get("trade_amount") or 0) for x in items)
    fee = sum(float(x.get("fee") or 0) for x in items)
    return {"count": len(items), "buy": buy, "sell": sell, "amount": amount, "fee": fee}


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

    try:
        crypto = _fetch_crypto_trades(d, d, type_filter="all", symbol="")
    except Exception:
        crypto = []
    try:
        stock = _fetch_stock_trades(d, d, type_filter="all", symbol="")
    except Exception:
        stock = []
    try:
        overseas = _fetch_overseas_trades(d, d, type_filter="all", symbol="")
    except Exception:
        overseas = []

    sc = _summarize(crypto)
    ss = _summarize(stock)
    so = _summarize(overseas)
    total = sc["count"] + ss["count"] + so["count"]

    header = f"📊 <b>[DeepSignal] 오늘의 매매 요약</b>\n📅 {d} ({wd})"
    if label:
        header += f" · {label}"

    def _block(icon: str, name: str, s: dict, usd: bool = False) -> str:
        if s["count"] == 0:
            return f"{icon} <b>{name}</b>\n  체결 없음"
        amt = _fmt_usd(s["amount"]) if usd else _fmt_krw(s["amount"])
        line = (f"{icon} <b>{name}</b>\n"
                f"  체결 {s['count']}건 (매수 {s['buy']} / 매도 {s['sell']})\n"
                f"  거래금액 {amt}")
        if s["fee"] > 0:
            line += f" · 수수료 {_fmt_krw(s['fee']) if not usd else _fmt_usd(s['fee'])}"
        return line

    parts = [
        header,
        "",
        _block("🪙", "코인 (Upbit)", sc),
        "",
        _block("🇰🇷", "국내주식 (KIS)", ss),
        "",
        _block("🌎", "해외주식 (KIS)", so, usd=True),
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
