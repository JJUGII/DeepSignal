"""Fast Telegram menu responses from recent on-disk plans (no full re-analysis)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deepsignal.live_trading.time_utils import parse_datetime_with_default_tz


def _file_age_minutes(path: Path) -> float | None:
    if not path.is_file():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    from deepsignal.live_trading.time_utils import now_kst

    now_ts = now_kst().timestamp()
    return max(0.0, (now_ts - mtime) / 60.0)


def _json_generated_age_minutes(data: dict[str, Any], path: Path) -> float | None:
    raw = data.get("generated_at")
    if raw:
        try:
            dt = parse_datetime_with_default_tz(str(raw))
            from deepsignal.live_trading.time_utils import now_kst

            return max(0.0, (now_kst() - dt).total_seconds() / 60.0)
        except (TypeError, ValueError):
            pass
    return _file_age_minutes(path)


def _latest_glob(root: Path, pattern: str) -> Path | None:
    files = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True)
    return files[0] if files else None


def format_kis_recommendation_from_cache(
    output_dir: str | Path,
    *,
    max_age_minutes: float = 120.0,
) -> dict[str, Any] | None:
    """Return {body, order_count, age_minutes} from latest daily AI plan if fresh enough."""
    root = Path(output_dir)
    plan_path = _latest_glob(root, "ai_daily_trade_plan_*.json")
    if plan_path is None:
        plan_path = root / "live_order_plan_ai_latest.json"
        if not plan_path.is_file():
            return None
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        age = _file_age_minutes(plan_path)
        if age is None or age > max_age_minutes:
            return None
        rec_path = _latest_glob(root, "ai_live_trade_recommendation_*.json")
        rec_count = 0
        status = str(plan_data.get("status") or "UNKNOWN")
        if rec_path and rec_path.is_file():
            try:
                rec_data = json.loads(rec_path.read_text(encoding="utf-8"))
                rec_count = len(rec_data.get("recommendations") or [])
            except (OSError, json.JSONDecodeError):
                pass
        order_count = len(plan_data.get("orders") or [])
        total_val = sum(float(o.get("estimated_order_value") or 0) for o in (plan_data.get("orders") or []))
        lines = [
            "[DeepSignal 국내주식]",
            f"⚡ {age:.0f}분 전 캐시",
            f"상태: {status} · 추천 {rec_count}건 · 주문안 {order_count}건 ({total_val:,.0f}원)",
        ]
        if order_count == 0:
            lines.append("현재 주문 제안 없음")
        return {
            "body": "\n".join(lines)[:4000],
            "order_count": order_count,
            "age_minutes": age,
        }

    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    age = _json_generated_age_minutes(data, plan_path)
    if age is None or age > max_age_minutes:
        return None

    order_count = int(data.get("order_count") or 0)
    lines = [
        "[DeepSignal 국내주식]",
        f"⚡ {age:.0f}분 전 캐시",
        f"상태: {data.get('status')} · 추천 {int(data.get('recommendation_count') or 0)}건 · "
        f"주문안 {order_count}건 ({float(data.get('total_order_value') or 0):,.0f}원)",
    ]
    if order_count == 0:
        lines.append("현재 주문 제안 없음")
    else:
        lines.append("주문안 있음 — 승인 메시지를 확인하세요")
    return {
        "body": "\n".join(lines)[:4000],
        "order_count": int(data.get("order_count") or 0),
        "age_minutes": age,
    }


def format_crypto_recommendation_from_cache(
    output_dir: str | Path,
    *,
    max_age_minutes: float = 15.0,
) -> dict[str, Any] | None:
    """Return Telegram body from CRYPTO_ORDER_PLAN.json if fresh."""
    from deepsignal.crypto_trading.crypto_order_plan import CRYPTO_PLAN_JSON, load_crypto_plan

    root = Path(output_dir)
    plan_path = root / CRYPTO_PLAN_JSON
    if not plan_path.is_file():
        return None
    age = _file_age_minutes(plan_path)
    if age is None or age > max_age_minutes:
        return None
    try:
        plan = load_crypto_plan(plan_path)
    except Exception:
        return None
    if not plan.market:
        return None

    market = str(plan.market).strip().upper()
    label = str(plan.display_name or "").strip()
    if label.upper() in ("", market) or label.upper().startswith("KRW-"):
        symbol_line = f"• {market} — {plan.side.upper()}"
    else:
        symbol_line = f"• {label} ({market}) — {plan.side.upper()}"

    lines = [
        "[DeepSignal 코인]",
        f"⚡ {age:.0f}분 전 캐시",
        symbol_line,
        f"이유: {plan.reason or '—'}",
    ]
    if plan.side.lower() == "buy":
        lines.append(f"주문금액: {float(plan.krw_amount or 0):,.0f}원")
    elif plan.sell_trigger:
        lines.append(f"조건: {plan.sell_trigger}")
    lines.append("(15분 후 버튼을 다시 누르면 재분석)")
    return {"body": "\n".join(lines)[:4000], "age_minutes": age}
