"""Short, user-facing Telegram text (no file paths or debug dumps)."""

from __future__ import annotations

import os
import re
from typing import Any

from deepsignal.crypto_trading.crypto_recommendation_diagnostics import CryptoRecommendationDiagnostics


def menu_verbose_logging() -> bool:
    return str(os.environ.get("TELEGRAM_MENU_VERBOSE_LOG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def menu_scan_progress_enabled() -> bool:
    """Extra 'scanning…' Telegram ping on menu (default off)."""
    return str(os.environ.get("TELEGRAM_MENU_SCAN_PROGRESS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def simplify_telegram_hint(text: str) -> str:
    """Strip engineer-facing detail from a diagnostic line."""
    s = str(text or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s*\(buffer\s+[\d.]+%p:[^)]+\)", "", s, flags=re.IGNORECASE)
    s = re.sub(
        r"스프레드 추정\s+[\d.]+\s*bps\s*>\s*한도\s+[\d.]+\s*bps",
        "스프레드 과다",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"수수료·슬리피지 반영\s+R:R\s+[\d.]+\s*<\s*최소\s+[\d.]+",
        "수익비 부족",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\s*\(목표[^)]+\)", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


_SKIP_HINT_SUBSTRINGS = (
    "내부 로직",
    "선정 로직",
    "확인 필요",
    "선정 경로",
    "세션·과매매",
    "시세 조회 오류",
    "매수 후보 시세 조회 실패",
)


def format_crypto_no_recommendation_telegram(
    diag: CryptoRecommendationDiagnostics,
    *,
    max_hints: int = 2,
) -> list[str]:
    """User-readable lines when there is no BUY/SELL recommendation."""
    lines: list[str] = ["현재 매수·매도 추천 없음"]

    near_tp = [
        s
        for s in diag.sell_candidates
        if s.sell_trigger == "near_take_profit" and s.min_order_krw_pass
    ]
    if near_tp:
        parts = ", ".join(f"{s.display_name} {s.pnl_pct:+.1f}%" for s in near_tp[:2])
        lines.append(f"보유 익절 근접: {parts}")

    gate_pool = [b for b in diag.buy_candidates if b.gate_passed]
    if gate_pool:
        lines.append(f"매수 후보 {len(gate_pool)}건 — 스프레드·수익비 조건 미충족")

    hints: list[str] = []
    for raw in diag.final_summary_bullets:
        if any(skip in raw for skip in _SKIP_HINT_SUBSTRINGS):
            continue
        if "점수·게이트 통과" in raw and gate_pool:
            continue
        short = simplify_telegram_hint(raw)
        if short and short not in hints:
            hints.append(short)

    for h in hints[:max_hints]:
        if h.startswith("-"):
            lines.append(h)
        else:
            lines.append(f"· {h}")

    if len(lines) == 1:
        reason = str(diag.final_no_recommendation_reason or "").strip()
        if reason:
            first = simplify_telegram_hint(reason.split(";")[0])
            if first:
                lines.append(f"· {first[:200]}")

    return lines


def format_crypto_recommendation_telegram(
    *,
    display_name: str,
    market: str,
    side: str,
    reason: str,
    pnl_pct: float,
    current_price: float,
    sell_trigger: str | None = None,
    approval_sent: bool = False,
    max_order_krw: float | None = None,
    max_orders_per_day: int | None = None,
) -> list[str]:
    lines = [
        "[DeepSignal 코인]",
        f"{display_name} ({market}) — {side.upper()}",
        f"수익률 {pnl_pct:+.2f}% · 가격 {current_price:,.0f}원",
        f"이유: {reason}",
    ]
    if sell_trigger:
        lines.insert(2, f"조건: {sell_trigger}")
    if max_order_krw and max_order_krw > 0:
        ord_cap = f"주문 한도 {max_order_krw:,.0f}원"
        if max_orders_per_day and max_orders_per_day > 0:
            ord_cap += f" · 일 {max_orders_per_day}회"
        lines.append(ord_cap)
    if approval_sent:
        lines.append("")
        lines.append("승인/거부 버튼 메시지를 확인해 주세요.")
    return lines


def format_kis_recommendation_telegram(
    *,
    status: str,
    recommendation_count: int,
    order_count: int,
    total_order_value: float,
    approval_sent: bool = False,
) -> list[str]:
    lines = [
        "[DeepSignal 국내주식]",
        f"상태: {status}",
        f"추천 {recommendation_count}건 · 주문안 {order_count}건 ({total_order_value:,.0f}원)",
    ]
    if order_count > 0 and approval_sent:
        lines.append("")
        lines.append("승인/거부 버튼 메시지를 확인해 주세요.")
    elif order_count == 0:
        lines.append("")
        lines.append("현재 주문 제안 없음")
    return lines


def format_holdings_telegram_brief(
    *,
    summary_lines: list[str],
    kis_lines: list[str],
    crypto_lines: list[str],
    upbit_krw: float,
) -> str:
    """Single-screen holdings without duplicate per-coin blocks."""
    out: list[str] = ["[DeepSignal — 현재 자산]", ""]
    out.extend(summary_lines)
    out.append("")
    out.extend(kis_lines)
    out.append("")
    out.extend(crypto_lines)
    out.append("")
    out.append(f"Upbit KRW 가용: {upbit_krw:,.0f}원")
    return "\n".join(out)[:4000]
