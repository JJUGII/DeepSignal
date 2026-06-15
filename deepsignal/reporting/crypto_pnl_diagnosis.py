"""코인 실현손익 진단 — 매도 트리거(exit_reason)·보유시간별 분해.

'어디서 돈이 새는가'를 데이터로 보여 전략 튜닝·공격성 단계 재책정 근거를 만든다.
crypto_trades.db(청산 완료 거래: entry/exit_time, actual_return, exit_reason)를 집계.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _hold_bucket(minutes: float) -> str:
    if minutes < 5:
        return "0-5분"
    if minutes < 30:
        return "5-30분"
    if minutes < 120:
        return "30분-2시간"
    if minutes < 1440:
        return "2시간-1일"
    return "1일+"


_BUCKET_ORDER = ["0-5분", "5-30분", "30분-2시간", "2시간-1일", "1일+"]


def _agg(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"count": 0, "win_rate": 0.0, "avg_return_pct": 0.0, "total_return_pct": 0.0}
    rets = [float(r["actual_return"]) * 100 for r in rows]
    wins = sum(1 for x in rets if x > 0)
    return {
        "count": n,
        "win_rate": round(wins / n * 100, 1),
        "avg_return_pct": round(sum(rets) / n, 3),
        "total_return_pct": round(sum(rets), 2),
    }


def diagnose_crypto_pnl(output_dir: str | Path = "outputs", *, days: int = 30) -> dict[str, Any]:
    """exit_reason별 + 보유시간별 실현손익 분해."""
    db = Path(output_dir) / "crypto_trades.db"
    if not db.exists():
        return {"error": "crypto_trades.db 없음", "by_exit_reason": {}, "by_hold_time": {}}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT symbol, entry_time, exit_time, actual_return, exit_reason "
            "FROM crypto_trades WHERE paper=0 AND exit_price>0 AND actual_return IS NOT NULL"
        )]
    finally:
        conn.close()

    by_reason: dict[str, list[dict]] = {}
    by_hold: dict[str, list[dict]] = {}
    for r in rows:
        reason = str(r.get("exit_reason") or "미상")
        by_reason.setdefault(reason, []).append(r)
        et, xt = _parse(r.get("entry_time")), _parse(r.get("exit_time"))
        if et and xt:
            mins = max(0.0, (xt - et).total_seconds() / 60.0)
            by_hold.setdefault(_hold_bucket(mins), []).append(r)

    reason_stats = {k: _agg(v) for k, v in by_reason.items()}
    hold_stats = {k: _agg(by_hold.get(k, [])) for k in _BUCKET_ORDER if by_hold.get(k)}
    return {
        "total_trades": len(rows),
        "overall": _agg(rows),
        "by_exit_reason": dict(sorted(reason_stats.items(), key=lambda x: x[1]["total_return_pct"])),
        "by_hold_time": hold_stats,
    }


def format_diagnosis_text(d: dict[str, Any]) -> str:
    if d.get("error"):
        return f"진단 불가: {d['error']}"
    lines = [f"📊 코인 실현손익 진단 (청산 {d['total_trades']}건)"]
    o = d["overall"]
    lines.append(f"전체: {o['count']}건 · 승률 {o['win_rate']}% · 평균 {o['avg_return_pct']:+.3f}% · 합계 {o['total_return_pct']:+.2f}%")
    lines.append("")
    lines.append("■ 매도 트리거별 (손실 큰 순):")
    for reason, s in d["by_exit_reason"].items():
        flag = "🔴" if s["total_return_pct"] < 0 else "🟢"
        lines.append(f"  {flag} {reason}: {s['count']}건 승률{s['win_rate']}% 평균{s['avg_return_pct']:+.3f}% 합계{s['total_return_pct']:+.2f}%")
    lines.append("")
    lines.append("■ 보유시간별:")
    for bucket, s in d["by_hold_time"].items():
        flag = "🔴" if s["total_return_pct"] < 0 else "🟢"
        lines.append(f"  {flag} {bucket}: {s['count']}건 승률{s['win_rate']}% 평균{s['avg_return_pct']:+.3f}% 합계{s['total_return_pct']:+.2f}%")
    return "\n".join(lines)
