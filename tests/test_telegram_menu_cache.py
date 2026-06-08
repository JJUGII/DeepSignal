"""Telegram menu cache fast path."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.telegram_menu_cache import format_kis_recommendation_from_cache


def test_kis_cache_reads_recent_daily_plan(tmp_path: Path) -> None:
    plan = {
        "generated_at": "2026-05-26T13:10:44+09:00",
        "status": "AI_DAILY_TRADE_PLAN_NO_ORDERS",
        "recommendation_count": 7,
        "order_count": 0,
        "total_order_value": 0.0,
        "latest_order_plan_json": "live_order_plan_ai_latest.json",
        "diagnostic_console": "=== Plan Orders Diagnosis ===\nPlan orders: 0",
    }
    p = tmp_path / "ai_daily_trade_plan_20260526_131044.json"
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    out = format_kis_recommendation_from_cache(tmp_path, max_age_minutes=9999.0)
    assert out is not None
    assert "캐시" in out["body"]
    assert out["order_count"] == 0
