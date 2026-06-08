"""Recommendation → outcome tracking for the recommend/result/learn loop."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from deepsignal.live_trading.ai_recommendation.recommendation_model import RecommendationResult, RecommendationRunResult

OUTCOMES_DB_NAME = "recommendation_outcomes.db"
PERFORMANCE_REPORT_MD = "RECOMMENDATION_PERFORMANCE.md"
PERFORMANCE_REPORT_JSON = "recommendation_performance_latest.json"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recommendation_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    final_score REAL,
    technical_score REAL,
    news_score REAL,
    macro_score REAL,
    liquidity_gate TEXT,
    risk_gate TEXT,
    validation_threshold REAL,
    allowed_for_plan INTEGER NOT NULL DEFAULT 0,
    executed INTEGER NOT NULL DEFAULT 0,
    entry_price REAL,
    exit_price REAL,
    max_profit_pct REAL,
    max_loss_pct REAL,
    realized_pnl_pct REAL,
    holding_hours REAL,
    exit_reason TEXT,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rec_outcomes_created ON recommendation_outcomes(created_at);
CREATE INDEX IF NOT EXISTS idx_rec_outcomes_symbol ON recommendation_outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_rec_outcomes_run ON recommendation_outcomes(run_id);
"""


@dataclass
class RecommendationPerformanceSummary:
    days: int
    total_rows: int
    allowed_count: int
    executed_count: int
    closed_count: int
    win_count: int
    avg_realized_pnl_pct: float | None
    avg_max_profit_pct: float | None
    blocked_by_score_count: int


def outcomes_db_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / OUTCOMES_DB_NAME


def init_outcomes_db(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(resolved)) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    return resolved


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _parse_validation_threshold(quality_gates: dict[str, str]) -> float | None:
    raw = str(quality_gates.get("min_final_score") or "").strip()
    if not raw:
        return None
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)", raw)
    return float(match.group(1)) if match else None


def _score_from_breakdown(breakdown: dict[str, Any], key: str) -> float | None:
    if key in breakdown and breakdown[key] is not None:
        return _float(breakdown[key])
    display = breakdown.get("display") if isinstance(breakdown.get("display"), dict) else {}
    raw = display.get(key.replace("_score", ""))
    if raw is None:
        return None
    try:
        return float(str(raw).replace("+", ""))
    except ValueError:
        return None


def record_recommendation_run(
    result: RecommendationRunResult,
    *,
    outcomes_db: str | Path,
) -> int:
    """Insert one row per recommendation from a plan run. Returns insert count."""
    path = init_outcomes_db(outcomes_db)
    run_id = str(result.generated_at)
    rows: list[tuple[Any, ...]] = []
    for rec in result.recommendations:
        if rec.action in {"SKIP", "HOLD"}:
            continue
        bd = rec.score_breakdown if isinstance(rec.score_breakdown, dict) else {}
        gates = rec.quality_gates if isinstance(rec.quality_gates, dict) else {}
        rows.append(
            (
                run_id,
                result.generated_at,
                rec.symbol.upper(),
                rec.action,
                _float(rec.source_signal_score) or _score_from_breakdown(bd, "final_score"),
                _score_from_breakdown(bd, "technical_score"),
                _score_from_breakdown(bd, "news_score"),
                _score_from_breakdown(bd, "macro_score"),
                str(gates.get("liquidity") or ""),
                ",".join(
                    p
                    for p in (str(gates.get("portfolio_risk") or ""), str(gates.get("score_threshold") or ""))
                    if p
                ),
                _parse_validation_threshold(gates),
                1 if rec.allowed_for_plan else 0,
                0,
                _float(rec.suggested_limit_price),
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        )
    if not rows:
        return 0
    sql = (
        "INSERT INTO recommendation_outcomes ("
        "run_id, created_at, symbol, action, final_score, technical_score, news_score, macro_score, "
        "liquidity_gate, risk_gate, validation_threshold, allowed_for_plan, executed, entry_price, "
        "exit_price, max_profit_pct, max_loss_pct, realized_pnl_pct, holding_hours, exit_reason, closed_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    with sqlite3.connect(str(path)) as conn:
        conn.executemany(sql, rows)
        conn.commit()
        return len(rows)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")[:26])
    except ValueError:
        return None


def _hours_between(start: str | None, end: str | None) -> float | None:
    a = _parse_ts(start)
    b = _parse_ts(end)
    if not a or not b:
        return None
    return max(0.0, (b - a).total_seconds() / 3600.0)


def _load_price_series(
    main_db: str,
    *,
    symbol: str,
    since_date: str,
) -> list[tuple[str, float]]:
    sql = (
        "SELECT substr(bar_time, 1, 10) AS d, close FROM market_prices "
        "WHERE symbol = ? AND timeframe = '1d' AND substr(bar_time, 1, 10) >= ? "
        "ORDER BY bar_time ASC"
    )
    with sqlite3.connect(str(Path(main_db).expanduser().resolve())) as conn:
        try:
            rows = conn.execute(sql, (symbol.upper(), since_date[:10])).fetchall()
        except sqlite3.Error:
            return []
    out: list[tuple[str, float]] = []
    for day, close in rows:
        px = _float(close)
        if px and px > 0:
            out.append((str(day), px))
    return out


def _first_buy_after(main_db: str, *, symbol: str, since_at: str) -> dict[str, Any] | None:
    sql = (
        "SELECT created_at, side, quantity, limit_price, status, order_id FROM real_order_history "
        "WHERE symbol = ? AND UPPER(side) = 'BUY' AND created_at >= ? ORDER BY created_at ASC LIMIT 1"
    )
    with sqlite3.connect(str(Path(main_db).expanduser().resolve())) as conn:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(sql, (symbol.upper(), since_at)).fetchone()
        except sqlite3.Error:
            return None
    return dict(row) if row else None


def _first_sell_after(main_db: str, *, symbol: str, since_at: str) -> dict[str, Any] | None:
    sql = (
        "SELECT created_at, side, quantity, limit_price, status FROM real_order_history "
        "WHERE symbol = ? AND UPPER(side) = 'SELL' AND created_at >= ? ORDER BY created_at ASC LIMIT 1"
    )
    with sqlite3.connect(str(Path(main_db).expanduser().resolve())) as conn:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(sql, (symbol.upper(), since_at)).fetchone()
        except sqlite3.Error:
            return None
    return dict(row) if row else None


def _fill_price_after_buy(main_db: str, *, symbol: str, since_at: str) -> float | None:
    sql = (
        "SELECT fill_price FROM real_fill_history "
        "WHERE symbol = ? AND UPPER(side) = 'BUY' AND created_at >= ? "
        "ORDER BY created_at ASC LIMIT 1"
    )
    with sqlite3.connect(str(Path(main_db).expanduser().resolve())) as conn:
        try:
            row = conn.execute(sql, (symbol.upper(), since_at)).fetchone()
        except sqlite3.Error:
            return None
    return _float(row[0]) if row else None


def refresh_recommendation_outcomes(
    main_db_path: str,
    outcomes_db: str | Path,
    *,
    lookback_days: int = 90,
) -> dict[str, int]:
    """Update execution and PnL fields from main DB (read-only)."""
    path = init_outcomes_db(outcomes_db)
    cutoff = (date.today() - timedelta(days=int(lookback_days))).isoformat()
    stats = {"rows_checked": 0, "executed_marked": 0, "metrics_updated": 0, "closed": 0}

    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? AND closed_at IS NULL",
            (cutoff,),
        ).fetchall()
        stats["rows_checked"] = len(rows)

        for row in rows:
            row_id = int(row["id"])
            symbol = str(row["symbol"]).upper()
            created_at = str(row["created_at"])
            entry = _float(row["entry_price"])
            executed = int(row["executed"] or 0)
            since_day = created_at[:10]

            if not executed and int(row["allowed_for_plan"] or 0):
                buy = _first_buy_after(main_db_path, symbol=symbol, since_at=created_at)
                if buy:
                    fill_px = _fill_price_after_buy(main_db_path, symbol=symbol, since_at=created_at)
                    entry_px = fill_px or _float(buy.get("limit_price")) or entry
                    conn.execute(
                        "UPDATE recommendation_outcomes SET executed = 1, entry_price = ? WHERE id = ?",
                        (entry_px, row_id),
                    )
                    executed = 1
                    entry = entry_px
                    stats["executed_marked"] += 1

            if not executed or not entry or entry <= 0:
                continue

            prices = _load_price_series(main_db_path, symbol=symbol, since_date=since_day)
            if prices:
                max_px = max(px for _, px in prices)
                min_px = min(px for _, px in prices)
                max_profit = (max_px - entry) / entry * 100.0
                max_loss = (min_px - entry) / entry * 100.0
                conn.execute(
                    "UPDATE recommendation_outcomes SET max_profit_pct = ?, max_loss_pct = ? WHERE id = ?",
                    (max_profit, max_loss, row_id),
                )
                stats["metrics_updated"] += 1

            sell = _first_sell_after(main_db_path, symbol=symbol, since_at=created_at)
            if sell:
                exit_px = _float(sell.get("limit_price")) or (prices[-1][1] if prices else None)
                if exit_px and exit_px > 0:
                    pnl = (exit_px - entry) / entry * 100.0
                    hours = _hours_between(created_at, str(sell.get("created_at")))
                    conn.execute(
                        "UPDATE recommendation_outcomes SET exit_price = ?, realized_pnl_pct = ?, "
                        "holding_hours = ?, exit_reason = ?, closed_at = ? WHERE id = ?",
                        (
                            exit_px,
                            pnl,
                            hours,
                            "sell_order",
                            str(sell.get("created_at") or datetime.now().isoformat(timespec="seconds")),
                            row_id,
                        ),
                    )
                    stats["closed"] += 1

        conn.commit()
    return stats


def _query_summary(conn: sqlite3.Connection, *, since_day: str) -> RecommendationPerformanceSummary:
    cur = conn.execute(
        "SELECT COUNT(*) FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ?",
        (since_day,),
    )
    total = int(cur.fetchone()[0])
    allowed = int(
        conn.execute(
            "SELECT COUNT(*) FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? AND allowed_for_plan = 1",
            (since_day,),
        ).fetchone()[0]
    )
    executed = int(
        conn.execute(
            "SELECT COUNT(*) FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? AND executed = 1",
            (since_day,),
        ).fetchone()[0]
    )
    closed = int(
        conn.execute(
            "SELECT COUNT(*) FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? AND closed_at IS NOT NULL",
            (since_day,),
        ).fetchone()[0]
    )
    wins = int(
        conn.execute(
            "SELECT COUNT(*) FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? "
            "AND closed_at IS NOT NULL AND realized_pnl_pct > 0",
            (since_day,),
        ).fetchone()[0]
    )
    avg_pnl_row = conn.execute(
        "SELECT AVG(realized_pnl_pct) FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? "
        "AND closed_at IS NOT NULL",
        (since_day,),
    ).fetchone()
    avg_max_row = conn.execute(
        "SELECT AVG(max_profit_pct) FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? "
        "AND executed = 1",
        (since_day,),
    ).fetchone()
    blocked = 0
    rows = conn.execute(
        "SELECT final_score, validation_threshold, allowed_for_plan FROM recommendation_outcomes "
        "WHERE substr(created_at, 1, 10) >= ?",
        (since_day,),
    ).fetchall()
    for fs, thr, allowed_flag in rows:
        if allowed_flag:
            continue
        if thr is not None and fs is not None and float(fs) < float(thr):
            blocked += 1

    return RecommendationPerformanceSummary(
        days=0,
        total_rows=total,
        allowed_count=allowed,
        executed_count=executed,
        closed_count=closed,
        win_count=wins,
        avg_realized_pnl_pct=_float(avg_pnl_row[0]) if avg_pnl_row else None,
        avg_max_profit_pct=_float(avg_max_row[0]) if avg_max_row else None,
        blocked_by_score_count=blocked,
    )


def generate_recommendation_performance_report(
    outcomes_db: str | Path,
    *,
    output_dir: str | Path = "outputs",
    days: int = 7,
) -> tuple[Path, Path, RecommendationPerformanceSummary]:
    """Write Markdown + JSON performance report for recent recommendations."""
    path = init_outcomes_db(outcomes_db)
    since_day = (date.today() - timedelta(days=max(1, int(days)))).isoformat()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")

    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        summary = _query_summary(conn, since_day=since_day)
        summary.days = int(days)

        by_symbol = conn.execute(
            "SELECT symbol, COUNT(*) AS n, SUM(executed) AS ex, AVG(realized_pnl_pct) AS pnl "
            "FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? "
            "GROUP BY symbol ORDER BY n DESC LIMIT 15",
            (since_day,),
        ).fetchall()

        score_buckets = conn.execute(
            "SELECT "
            "CASE "
            "WHEN final_score IS NULL THEN 'unknown' "
            "WHEN final_score < 55 THEN '<55' "
            "WHEN final_score < 65 THEN '55-64' "
            "WHEN final_score < 75 THEN '65-74' "
            "ELSE '75+' END AS bucket, "
            "COUNT(*) AS n, SUM(allowed_for_plan) AS allowed, SUM(executed) AS executed, "
            "AVG(realized_pnl_pct) AS avg_pnl "
            "FROM recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? "
            "GROUP BY bucket ORDER BY bucket",
            (since_day,),
        ).fetchall()

    win_rate = (summary.win_count / summary.closed_count) if summary.closed_count else None
    body: dict[str, Any] = {
        "generated_at": now,
        "period_days": days,
        "since_date": since_day,
        "summary": {
            "total_rows": summary.total_rows,
            "allowed_count": summary.allowed_count,
            "executed_count": summary.executed_count,
            "closed_count": summary.closed_count,
            "win_count": summary.win_count,
            "win_rate": win_rate,
            "avg_realized_pnl_pct": summary.avg_realized_pnl_pct,
            "avg_max_profit_pct": summary.avg_max_profit_pct,
            "blocked_by_score_count": summary.blocked_by_score_count,
        },
        "by_symbol": [dict(r) for r in by_symbol],
        "score_buckets": [dict(r) for r in score_buckets],
        "outcomes_db": str(Path(path).resolve()),
    }

    jp = root / PERFORMANCE_REPORT_JSON
    mp = root / PERFORMANCE_REPORT_MD
    jp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# DeepSignal — 최근 추천 성능 리포트",
        "",
        f"- Generated: {now}",
        f"- Period: 최근 {days}일 (since {since_day})",
        f"- Outcomes DB: `{path.as_posix()}`",
        "",
        "## 요약",
        "",
        f"- 추천 기록: **{summary.total_rows}**건",
        f"- plan 허용 (`allowed_for_plan`): **{summary.allowed_count}**건",
        f"- 체결 추적 (`executed`): **{summary.executed_count}**건",
        f"- 청산 완료 (`closed`): **{summary.closed_count}**건",
    ]
    if win_rate is not None:
        lines.append(f"- 청산 승률: **{win_rate * 100:.1f}%** ({summary.win_count}/{summary.closed_count})")
    if summary.avg_realized_pnl_pct is not None:
        lines.append(f"- 평균 실현 수익률: **{summary.avg_realized_pnl_pct:+.2f}%**")
    if summary.avg_max_profit_pct is not None:
        lines.append(f"- 평균 최대 유리 변동 (`max_profit_pct`): **{summary.avg_max_profit_pct:+.2f}%**")
    lines.extend(
        [
            f"- 점수 임계값으로 차단 추정: **{summary.blocked_by_score_count}**건",
            "",
            "## 점수 구간별",
            "",
            "| 구간 | 건수 | 허용 | 체결 | 평균 실현% |",
            "|------|------|------|------|------------|",
        ]
    )
    for row in score_buckets:
        lines.append(
            f"| {row['bucket']} | {row['n']} | {row['allowed']} | {row['executed']} | "
            f"{row['avg_pnl'] if row['avg_pnl'] is not None else 'n/a'} |"
        )
    lines.extend(["", "## 종목별 (상위)", "", "| 종목 | 건수 | 체결 | 평균 실현% |", "|------|------|------|------------|"])
    for row in by_symbol:
        lines.append(
            f"| {row['symbol']} | {row['n']} | {row['ex']} | "
            f"{row['pnl'] if row['pnl'] is not None else 'n/a'} |"
        )
    lines.extend(
        [
            "",
            "## 루프",
            "",
            "1. `daily-ai-trade-plan` → `recommendation_outcomes.db` 기록",
            "2. 체결/가격 → `refresh` (주간 maintenance 포함)",
            "3. 본 리포트 → 임계값·게이트 튜닝 참고 (`validate-ai-recommendation` 병행)",
            "",
            "Note: read-only 집계. 실주문·KIS POST 없음.",
        ]
    )
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp, summary
