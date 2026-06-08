"""Operator-facing Korean Telegram messages (no debug leakage)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))


def _now_kst_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")

_DEFAULT_SYMBOL_MAP: dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "AAPL": "애플",
    "NVDA": "엔비디아",
}

_DEBUG_SUBSTRINGS = (
    "allow-test",
    "downgraded",
    "safety_audit",
    "blocked_reason",
    "final_score",
    "confidence",
    "generated_by",
    "stale",
    "freshness",
    "mvp:",
    "test-plan",
    "signal_date",
    "allow_test",
    "ignore_safety",
    "plan diagnostic",
    "auto_executed",
    "kis_post",
)

_DEFAULT_JUDGMENT_REASONS = (
    "최근 흐름이 양호합니다",
    "단기 매수 신호가 감지되었습니다",
    "현재 위험도는 낮은 편입니다",
)

_EXEC_ERROR_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("trading session closed", "session closed", "장외", "market closed"), "장이 마감되어 주문할 수 없습니다"),
    (("expired", "만료"), "승인 유효 시간이 지났습니다"),
    (("duplicate", "중복"), "중복 주문이 의심되어 차단되었습니다"),
    (("hash", "plan hash"), "승인한 주문안과 현재 주문안이 달라 실행을 중단했습니다"),
    (("max_single", "max_total", "exceeds", "초과"), "주문 금액 한도를 초과했습니다"),
    (("halt", "중단"), "오늘 거래 중단 상태입니다"),
    (("safety", "audit=blocked", "안전"), "안전 점검 결과로 실행이 보류되었습니다"),
    (("chat_id", "authorized"), "Telegram 승인 계정이 일치하지 않습니다"),
    (("token", "승인"), "승인 정보가 올바르지 않습니다"),
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_symbol_name_map(extra_path: str | Path | None = None) -> dict[str, str]:
    out = dict(_DEFAULT_SYMBOL_MAP)
    candidates = [
        Path(extra_path) if extra_path else None,
        _project_root() / "config" / "symbol_name_map.json",
    ]
    for path in candidates:
        if path is None or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            for key, value in data.items():
                code = str(key).strip().upper()
                name = str(value).strip()
                if code and name:
                    out[code] = name
    return out


def display_symbol_name(symbol: str, name_map: dict[str, str] | None = None) -> str:
    code = str(symbol or "").strip().upper()
    if not code:
        return "-"
    mapping = name_map or load_symbol_name_map()
    return mapping.get(code, code)


def label_side(side: str) -> str:
    key = str(side or "").strip().upper()
    return {"BUY": "매수", "SELL": "매도", "INCREASE": "추가 매수", "REDUCE": "일부 매도"}.get(key, key or "매수")


def label_order_type(order_type: str) -> str:
    key = str(order_type or "LIMIT").strip().upper()
    return {"LIMIT": "지정가", "MARKET": "시장가"}.get(key, "지정가")


def format_krw_friendly(amount: float) -> str:
    value = int(round(float(amount)))
    if value <= 0:
        return "0원"
    if value < 10_000:
        return f"{value:,}원"
    man = value // 10_000
    thousand = (value % 10_000) // 1_000
    if thousand:
        return f"약 {man}만 {thousand}천원"
    return f"약 {man}만원"


def _contains_debug_text(text: str) -> bool:
    low = str(text or "").lower()
    return any(marker in low for marker in _DEBUG_SUBSTRINGS)


def humanize_judgment_reason(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text or _contains_debug_text(text):
        return None
    if re.match(r"^(BUY|INCREASE|SELL|REDUCE|SKIP|HOLD)\s*:", text, flags=re.I):
        text = re.sub(r"^(BUY|INCREASE|SELL|REDUCE|SKIP|HOLD)\s*:\s*", "", text, flags=re.I).strip()
    if re.search(r"final_score|confidence|blocked_reason|safety_audit", text, flags=re.I):
        return None
    if re.fullmatch(r"(BUY|SELL|HOLD|SKIP|INCREASE|REDUCE)(\s+signal)?", text, flags=re.I):
        return None
    low = text.lower()
    if "ai score" in low or re.search(r"\bscore\b", low) or "confidence" in low:
        return None
    if "momentum" in low or "모멘텀" in text:
        return "최근 가격 흐름이 상승하는 모습입니다"
    if "volume" in low or "거래량" in text:
        return "거래량이 늘며 관심이 붙는 구간입니다"
    if "risk_off" in low or "risk off" in text:
        return "시장 위험 요인을 반영해 보수적으로 판단했습니다"
    if "risk" in low or "위험" in text:
        return "현재 위험도는 낮은 편입니다"
    if "신호" in text and "final" not in low:
        return "단기 매수 신호가 감지되었습니다"
    if len(text) > 80:
        return None
    if text.endswith("."):
        return text
    return f"{text}입니다" if text and not text.endswith("다") and not text.endswith("요") else text


def collect_judgment_reasons(order: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    raw_reasons = order.get("ai_reasons")
    if isinstance(raw_reasons, list):
        for item in raw_reasons:
            line = humanize_judgment_reason(str(item))
            if line and line not in seen:
                seen.add(line)
                out.append(line)
    single = humanize_judgment_reason(str(order.get("reason") or ""))
    if single and single not in seen:
        out.append(single)
    if not out:
        out = list(_DEFAULT_JUDGMENT_REASONS)
    return out[:4]


def humanize_execution_error(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return "알 수 없는 오류로 주문이 실행되지 않았습니다"
    low = text.lower()
    for markers, label in _EXEC_ERROR_PATTERNS:
        if any(m in low for m in markers):
            return label
    if _contains_debug_text(text):
        return "주문 조건을 확인한 뒤 다시 승인해 주세요"
    if len(text) > 120:
        return "주문 실행 중 오류가 발생했습니다"
    return text


def humanize_status_label(status: str) -> str:
    key = str(status or "").strip().upper()
    mapping = {
        "KIS_ORDER_SUBMITTED": "접수 완료",
        "TELEGRAM_APPROVAL_AUTO_EXECUTED": "주문 실행 완료",
        "TELEGRAM_APPROVAL_AUTO_EXECUTION_FAILED": "주문 실행 실패",
        "AI_RECOMMENDATION_READY": "분석 완료",
        "AI_RECOMMENDATION_NO_PLAN_ORDERS": "주문 없음",
        "NOT_AVAILABLE": "확인 필요",
        "PENDING": "승인 대기",
        "APPROVED": "승인됨",
        "REJECTED": "거부됨",
        "BLOCKED": "차단됨",
    }
    if key in mapping:
        return mapping[key]
    if "SUBMIT" in key:
        return "접수 완료"
    if "FAIL" in key or "BLOCK" in key:
        return "실패"
    return "확인 필요"


def load_plan_order_context(plan_path: str | Path) -> dict[str, Any]:
    path = Path(plan_path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    orders = data.get("orders") if isinstance(data.get("orders"), list) else []
    first = orders[0] if orders and isinstance(orders[0], dict) else {}
    return {
        "plan_path": path.as_posix(),
        "first_order": first,
        "order_count": len(orders),
    }


def format_operator_approval_request_text(
    request: Any,
    *,
    plan_path: str | Path,
    name_map: dict[str, str] | None = None,
) -> str:
    ctx = load_plan_order_context(plan_path)
    order = ctx.get("first_order") if isinstance(ctx.get("first_order"), dict) else {}
    symbol = display_symbol_name(str(order.get("symbol") or ""), name_map)
    side = label_side(str(order.get("side") or "BUY"))
    qty = int(order.get("estimated_qty") or order.get("quantity") or 0)
    limit_px = float(order.get("limit_price") or order.get("estimated_price") or 0)
    est_val = float(order.get("estimated_order_value") or (limit_px * max(qty, 0)))
    order_type = label_order_type(str(order.get("order_type") or "LIMIT"))
    reasons = collect_judgment_reasons(order)
    reason_lines = "\n".join(f"- {r}" for r in reasons)
    price_line = f"• 주문 가격: {limit_px:,.0f}원 이하" if limit_px > 0 else f"• 주문 방식: {order_type}"
    return "\n".join(
        [
            "[DeepSignal AI 매매 승인]",
            f"{symbol} {side} 추천",
            f"• 수량: {qty}주",
            price_line,
            f"• 예상 금액: {format_krw_friendly(est_val)}",
            "",
            "판단 이유",
            reason_lines,
            "",
            "승인하면 실제 주문이 실행됩니다.",
        ]
    )


def format_operator_execution_success_text(
    *,
    symbol: str,
    quantity: int,
    order_id: str,
    status: str,
    name_map: dict[str, str] | None = None,
    side: str = "BUY",
    price: float | None = None,
) -> str:
    name = display_symbol_name(symbol, name_map)
    is_buy = str(side).upper() not in ("SELL", "REDUCE")
    icon   = "📈" if is_buy else "📉"
    side_ko = "매수" if is_buy else "매도"
    kr_name = name if name != symbol else symbol
    lines = [
        f"{icon} <b>[국내주식] {side_ko} 체결</b>",
        f"종목: {symbol} ({kr_name})" if kr_name != symbol else f"종목: {symbol}",
        f"수량: {quantity:,}주" + (f" × {price:,.0f}원" if price and price > 0 else ""),
        f"금액: {quantity * price:,.0f}원" if price and price > 0 else "",
        f"주문번호: {order_id or '-'}",
        f"시각: {_now_kst_iso()}",
    ]
    return "\n".join(l for l in lines if l)


def format_operator_execution_fail_text(*, reason: str, symbol: str = "", side: str = "BUY") -> str:
    is_buy  = str(side).upper() not in ("SELL", "REDUCE")
    side_ko = "매수" if is_buy else "매도"
    lines = [
        f"⚠️ <b>[국내주식] {side_ko} 실패</b>",
    ]
    if symbol:
        lines.append(f"종목: {symbol}")
    lines += [
        f"사유: {humanize_execution_error(reason)}",
        f"시각: {_now_kst_iso()}",
    ]
    return "\n".join(lines)


def format_operator_execution_result_text(
    *,
    execution: Any,
    plan_context: dict[str, Any],
    name_map: dict[str, str] | None = None,
) -> str:
    order = plan_context.get("first_order") if isinstance(plan_context.get("first_order"), dict) else {}
    symbol = str(order.get("symbol") or getattr(execution, "request_id", ""))
    qty = int(order.get("estimated_qty") or order.get("quantity") or 0)
    if getattr(execution, "success", False):
        payload = getattr(execution, "execution_result", None) or {}
        results = payload.get("results") if isinstance(payload, dict) else []
        row = results[0] if results and isinstance(results[0], dict) else {}
        status = str(row.get("status") or getattr(execution, "status", ""))
        oid = str(row.get("broker_order_id") or "-")
        return format_operator_execution_success_text(
            symbol=symbol,
            quantity=qty,
            order_id=oid,
            status=status,
            name_map=name_map,
        )
    reason = ""
    errors = getattr(execution, "errors", None) or []
    if errors:
        reason = str(errors[0])
    payload = getattr(execution, "execution_result", None) or {}
    if isinstance(payload, dict):
        if payload.get("blocked_reason"):
            reason = str(payload.get("blocked_reason"))
        err_list = payload.get("errors")
        if isinstance(err_list, list) and err_list:
            reason = str(err_list[0])
    if not reason:
        reason = str(getattr(execution, "status", ""))
    return format_operator_execution_fail_text(reason=reason)


def format_operator_no_orders_text() -> str:
    return "\n".join(
        [
            "[DeepSignal]",
            "오늘은 주문하지 않습니다",
            "현재 기준으로 매수·매도할 종목이 없습니다.",
        ]
    )


def format_operator_daily_report_text(report: Any, *, name_map: dict[str, str] | None = None) -> str:
    _ = name_map
    summary = getattr(report, "summary", None) or {}
    if not isinstance(summary, dict):
        summary = {}

    def _yn(submitted: bool) -> str:
        return "있음" if submitted else "없음"

    rec_raw = str(summary.get("ai_recommendation_status", ""))
    rec = humanize_status_label(rec_raw)
    order_submitted = bool(summary.get("order_submitted"))
    # 오늘 주문 자체가 없었는지 (AI가 매수·매도할 종목을 못 찾음)
    no_orders = (not order_submitted) and (
        "NO_PLAN_ORDERS" in rec_raw or "NO_ORDERS" in rec_raw or "NO_PLAN" in rec_raw
    )

    if no_orders:
        # 주문이 없으면 승인/실행/체결은 '실패'가 아니라 '해당 없음'
        approval = execution = fill = "해당 없음"
    else:
        approval = humanize_status_label(
            str(summary.get("telegram_approval_status", summary.get("approval_status", "")))
        )
        execution = humanize_status_label(str(summary.get("execution_status", "")))
        fill = humanize_status_label(str(summary.get("fill_status", "")))
    order_line = "주문 제출됨" if order_submitted else "주문 없음"

    lines = [
        "[DeepSignal]",
        "오늘 매매 요약",
        f"• AI 분석: {rec}",
        f"• Telegram 승인: {approval}",
        f"• 주문 실행: {execution}",
        f"• 체결 확인: {fill}",
        f"• 실제 주문: {order_line}",
    ]
    if no_orders:
        lines.append("ℹ️ 오늘은 조건에 맞는 매수·매도 종목이 없어 주문하지 않았습니다(정상).")
    cash = summary.get("cash")
    if cash is not None:
        try:
            lines.append(f"• 계좌 현금: {format_krw_friendly(float(cash))}")
        except (TypeError, ValueError):
            pass
    ret = summary.get("today_return_pct")
    if ret not in (None, "", "NOT_AVAILABLE"):
        lines.append(f"• 오늘 수익률: {ret}")
    return "\n".join(lines)


def format_operator_plan_blocked_text(status: str) -> str:
    return "\n".join(
        [
            "[DeepSignal]",
            "오늘 주문 요청을 보내지 못했습니다",
            f"사유: {humanize_execution_error(status)}",
        ]
    )
