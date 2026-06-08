"""Crypto recommendation → outcome tracking (Upbit). KIS/stock paths untouched."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
from deepsignal.crypto_trading.crypto_recommendation import CryptoRecommendation
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitOrderResult
from deepsignal.live_trading.time_utils import now_kst, now_kst_iso

CRYPTO_OUTCOMES_DB_NAME = "crypto_recommendation_outcomes.db"
CRYPTO_PERFORMANCE_REPORT_MD = "CRYPTO_RECOMMENDATION_PERFORMANCE.md"
CRYPTO_PERFORMANCE_REPORT_JSON = "crypto_recommendation_performance_latest.json"

FillOutcome = Literal["done", "partial", "wait", "cancel", "timeout", "skipped"]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crypto_recommendation_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    created_at TEXT NOT NULL,
    market TEXT NOT NULL,
    display_name TEXT,
    side TEXT NOT NULL,
    reason TEXT,
    current_price REAL,
    avg_buy_price REAL,
    pnl_pct REAL,
    order_uuid TEXT,
    executed INTEGER NOT NULL DEFAULT 0,
    fill_price REAL,
    fill_volume REAL,
    fee REAL,
    realized_pnl_pct REAL,
    exit_reason TEXT,
    closed_at TEXT,
    max_profit_pct REAL,
    max_loss_pct REAL
);
CREATE INDEX IF NOT EXISTS idx_crypto_outcomes_created ON crypto_recommendation_outcomes(created_at);
CREATE INDEX IF NOT EXISTS idx_crypto_outcomes_market ON crypto_recommendation_outcomes(market);
CREATE INDEX IF NOT EXISTS idx_crypto_outcomes_uuid ON crypto_recommendation_outcomes(order_uuid);
"""

_EXTRA_OUTCOME_COLUMNS: tuple[tuple[str, str], ...] = (
    ("technical_score", "REAL"),
    ("macro_score", "REAL"),
    ("final_score", "REAL"),
    ("macro_regime", "TEXT"),
    ("validation_gate", "TEXT"),
    ("liquidity_gate", "TEXT"),
    ("score_breakdown_json", "TEXT"),
    ("quality_gates_json", "TEXT"),
    ("model_probability", "REAL"),
    ("features_snapshot_json", "TEXT"),
    ("entry_time", "TEXT"),
    ("paper", "INTEGER NOT NULL DEFAULT 0"),
    # 공격성 다이얼 성과 분석용 (단계 재책정 데이터)
    ("aggression_level", "INTEGER"),
    ("aggression_band", "TEXT"),
    ("signed_change_rate", "REAL"),  # 진입 시 24h 등락률 (추격거래 판별)
    ("rsi_14", "REAL"),              # 진입 시 RSI (과열 판별)
)


def outcome_paper_value() -> int:
    """1 when CRYPTO_PAPER_MODE is active at insert time."""
    from deepsignal.crypto_trading.crypto_paper_mode import crypto_paper_mode_enabled

    return 1 if crypto_paper_mode_enabled() else 0


def _migrate_crypto_outcome_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(crypto_recommendation_outcomes)")}
    for name, typ in _EXTRA_OUTCOME_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE crypto_recommendation_outcomes ADD COLUMN {name} {typ}")


@dataclass
class CryptoPerformanceSummary:
    days: int
    total_rows: int
    buy_count: int
    sell_count: int
    executed_count: int
    closed_count: int
    win_count: int
    avg_realized_pnl_pct: float | None
    total_realized_pnl_pct: float | None


def crypto_outcomes_db_path(output_dir: str | Path) -> Path:
    p = Path(output_dir).expanduser()
    if p.name == CRYPTO_OUTCOMES_DB_NAME or p.suffix == ".db":
        return p.resolve()
    return (p / CRYPTO_OUTCOMES_DB_NAME).resolve()


def init_crypto_outcomes_db(path: str | Path) -> Path:
    resolved = crypto_outcomes_db_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(resolved)) as conn:
        conn.executescript(_SCHEMA_SQL)
        _migrate_crypto_outcome_columns(conn)
        conn.commit()
    return resolved


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _today_kst() -> str:
    return now_kst().date().isoformat()


def record_crypto_recommendation(
    plan: CryptoOrderPlan,
    *,
    outcomes_db: str | Path,
    rec: CryptoRecommendation | None = None,
    run_id: str | None = None,
) -> int:
    """Insert recommendation row when plan is created. Returns row id."""
    path = init_crypto_outcomes_db(outcomes_db)
    created = plan.created_at or now_kst_iso()
    rid = run_id or created
    side = str(plan.side or "buy").lower()
    pnl = _float(plan.pnl_pct)
    if rec is not None and pnl is None:
        pnl = _float(rec.pnl_pct)
    avg_buy = _float(plan.avg_buy_price)
    if rec is not None and (avg_buy is None or avg_buy <= 0):
        avg_buy = _float(rec.avg_buy_price)

    tech = macro = final = None
    macro_regime = ""
    val_gate = liq_gate = ""
    bd_json = gates_json = None
    model_prob: float | None = None
    features_snap_json: str | None = None
    if rec is not None:
        tech = _float(rec.technical_score)
        macro = _float(rec.macro_score)
        final = _float(rec.final_score)
        macro_regime = str(rec.macro_regime or "")
        gates = rec.quality_gates if isinstance(rec.quality_gates, dict) else {}
        val_gate = str(gates.get("validation") or "")
        liq_gate = str(gates.get("liquidity") or "")
        if rec.score_breakdown:
            bd = rec.score_breakdown if isinstance(rec.score_breakdown, dict) else {}
            bd_json = json.dumps(bd, ensure_ascii=False)
            try:
                wp = bd.get("win_probability")
                if wp is not None:
                    model_prob = float(wp)
            except (TypeError, ValueError):
                pass
            snap = bd.get("features_snapshot")
            if isinstance(snap, dict) and snap:
                features_snap_json = json.dumps(snap, ensure_ascii=False)
        if gates:
            gates_json = json.dumps(gates, ensure_ascii=False)
            if model_prob is None and gates.get("win_probability"):
                try:
                    model_prob = float(str(gates["win_probability"]))
                except (TypeError, ValueError):
                    pass

    # ── 공격성 단계 + 진입 등락률/RSI 스탬프 (단계 재책정 분석용) ──
    agg_level: int | None = None
    agg_band: str | None = None
    try:
        from deepsignal.risk.aggression import current_level, resolve
        _ap = resolve(current_level())
        agg_level, agg_band = int(_ap.level), str(_ap.band)
    except Exception:
        pass
    chg_rate: float | None = None
    rsi14: float | None = None
    if rec is not None:
        chg_rate = _float(getattr(rec, "signed_change_rate", None))
        bd = rec.score_breakdown if isinstance(getattr(rec, "score_breakdown", None), dict) else {}
        snap = bd.get("features_snapshot") if isinstance(bd, dict) else None
        for _src in (bd, snap):
            if isinstance(_src, dict):
                for _k in ("rsi_14", "rsi", "rsi14"):
                    if _src.get(_k) is not None:
                        rsi14 = _float(_src.get(_k))
                        break
            if rsi14 is not None:
                break

    with sqlite3.connect(str(path)) as conn:
        _migrate_crypto_outcome_columns(conn)
        cur = conn.execute(
            """
            INSERT INTO crypto_recommendation_outcomes (
                run_id, created_at, market, display_name, side, reason,
                current_price, avg_buy_price, pnl_pct, order_uuid, executed,
                fill_price, fill_volume, fee, realized_pnl_pct, exit_reason, closed_at,
                max_profit_pct, max_loss_pct,
                technical_score, macro_score, final_score, macro_regime,
                validation_gate, liquidity_gate, score_breakdown_json, quality_gates_json,
                model_probability, features_snapshot_json, entry_time, paper,
                aggression_level, aggression_band, signed_change_rate, rsi_14
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rid,
                created,
                str(plan.market),
                str(plan.display_name or plan.market),
                side,
                str(plan.reason or ""),
                _float(plan.limit_price),
                avg_buy,
                pnl,
                None,
                0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                tech,
                macro,
                final,
                macro_regime,
                val_gate,
                liq_gate,
                bd_json,
                gates_json,
                model_prob,
                features_snap_json,
                created if side == "buy" else None,
                outcome_paper_value(),
                agg_level,
                agg_band,
                chg_rate,
                rsi14,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def _resolve_outcome_id(
    conn: sqlite3.Connection,
    *,
    market: str,
    side: str,
    order_uuid: str | None = None,
    outcome_id: int | None = None,
) -> int | None:
    if outcome_id is not None:
        return int(outcome_id)
    if order_uuid:
        row = conn.execute(
            "SELECT id FROM crypto_recommendation_outcomes WHERE order_uuid = ? ORDER BY id DESC LIMIT 1",
            (order_uuid,),
        ).fetchone()
        if row:
            return int(row[0])
    row = conn.execute(
        """
        SELECT id FROM crypto_recommendation_outcomes
        WHERE market = ? AND side = ? AND closed_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (market, side.lower()),
    ).fetchone()
    return int(row[0]) if row else None


def attach_crypto_order_uuid(
    *,
    outcomes_db: str | Path,
    market: str,
    side: str,
    order_uuid: str,
    outcome_id: int | None = None,
) -> bool:
    if not order_uuid:
        return False
    path = init_crypto_outcomes_db(outcomes_db)
    with sqlite3.connect(str(path)) as conn:
        oid = _resolve_outcome_id(
            conn, market=market, side=side, order_uuid=order_uuid, outcome_id=outcome_id
        )
        if oid is None:
            return False
        conn.execute(
            "UPDATE crypto_recommendation_outcomes SET order_uuid = ? WHERE id = ?",
            (str(order_uuid), oid),
        )
        conn.commit()
        return True


def apply_crypto_fill_update(
    plan: CryptoOrderPlan,
    result: UpbitOrderResult,
    status: dict[str, Any],
    fill_outcome: FillOutcome,
    *,
    outcomes_db: str | Path,
    outcome_id: int | None = None,
) -> dict[str, Any]:
    """Update outcome after order placement / fill poll."""
    path = init_crypto_outcomes_db(outcomes_db)
    market = str(plan.market)
    side = str(plan.side or "buy").lower()
    uuid = str(result.uuid or status.get("uuid") or "")
    executed_vol = _float(status.get("executed_volume")) or 0.0
    fill_px = _float(status.get("price")) or _float(result.price) or _float(plan.limit_price)
    fee = _float(status.get("paid_fee")) or 0.0
    stats: dict[str, Any] = {"updated": False, "outcome_id": None, "fill_outcome": fill_outcome}

    with sqlite3.connect(str(path)) as conn:
        oid = _resolve_outcome_id(
            conn, market=market, side=side, order_uuid=uuid or None, outcome_id=outcome_id
        )
        if oid is None:
            oid = record_crypto_recommendation(plan, outcomes_db=path)
        stats["outcome_id"] = oid

        if uuid:
            conn.execute(
                "UPDATE crypto_recommendation_outcomes SET order_uuid = ? WHERE id = ?",
                (uuid, oid),
            )

        if fill_outcome in ("done", "partial") and executed_vol > 0 and fill_px and fill_px > 0:
            executed_flag = 1 if fill_outcome == "done" else 0
            realized: float | None = None
            exit_reason: str | None = None
            closed_at: str | None = None
            row = conn.execute(
                "SELECT avg_buy_price, side FROM crypto_recommendation_outcomes WHERE id = ?",
                (oid,),
            ).fetchone()
            avg_buy = _float(row[0]) if row else _float(plan.avg_buy_price)
            row_side = str(row[1] if row else side).lower()

            if row_side == "sell" and avg_buy and avg_buy > 0 and fill_px:
                realized = (fill_px - avg_buy) / avg_buy * 100.0
                exit_reason = str(plan.reason or "sell_fill")
                if fill_outcome == "done":
                    closed_at = now_kst_iso()

            conn.execute(
                """
                UPDATE crypto_recommendation_outcomes SET
                    executed = ?,
                    fill_price = ?,
                    fill_volume = ?,
                    fee = ?,
                    realized_pnl_pct = COALESCE(?, realized_pnl_pct),
                    exit_reason = COALESCE(?, exit_reason),
                    closed_at = COALESCE(?, closed_at),
                    max_profit_pct = COALESCE(max_profit_pct, 0),
                    max_loss_pct = COALESCE(max_loss_pct, 0)
                WHERE id = ?
                """,
                (
                    executed_flag,
                    fill_px,
                    executed_vol,
                    fee,
                    realized,
                    exit_reason,
                    closed_at,
                    oid,
                ),
            )
            stats["updated"] = True
            stats["executed"] = executed_flag
            stats["fill_price"] = fill_px
            stats["realized_pnl_pct"] = realized
            try:
                from deepsignal.crypto_trading.crypto_trades import (
                    record_crypto_trade_entry,
                    record_crypto_trade_exit,
                )

                trades_root = Path(path).parent
                if row_side == "buy":
                    tid = record_crypto_trade_entry(
                        plan,
                        fill_price=float(fill_px),
                        fill_volume=float(executed_vol),
                        trades_db=trades_root,
                    )
                    if tid is not None:
                        stats["crypto_trade_id"] = tid
                elif row_side == "sell":
                    stats["crypto_trade_exit"] = record_crypto_trade_exit(
                        plan,
                        fill_price=float(fill_px),
                        fill_volume=float(executed_vol),
                        fee=float(fee or 0),
                        trades_db=trades_root,
                        fill_complete=fill_outcome == "done",
                    )
            except Exception:
                pass
        elif uuid:
            conn.execute(
                "UPDATE crypto_recommendation_outcomes SET order_uuid = ? WHERE id = ?",
                (uuid, oid),
            )

        conn.commit()
    return stats


def apply_crypto_trade_pipeline(
    plan: CryptoOrderPlan,
    result: UpbitOrderResult,
    *,
    outcomes_db: str | Path,
    fill_status: dict[str, Any] | None = None,
    fill_outcome: FillOutcome | None = None,
    outcome_id: int | None = None,
) -> dict[str, Any]:
    """Order placed (+ optional fill). Used by telegram / inactive / auto-runner."""
    out: dict[str, Any] = {"order_uuid": result.uuid}
    if result.uuid:
        attach_crypto_order_uuid(
            outcomes_db=outcomes_db,
            market=plan.market,
            side=plan.side,
            order_uuid=str(result.uuid),
            outcome_id=outcome_id,
        )
    if fill_status is not None and fill_outcome is not None:
        out["fill"] = apply_crypto_fill_update(
            plan,
            result,
            fill_status,
            fill_outcome,
            outcomes_db=outcomes_db,
            outcome_id=outcome_id,
        )
    return out


def refresh_crypto_outcomes(
    broker: UpbitBroker,
    outcomes_db: str | Path,
    *,
    lookback_days: int = 90,
) -> dict[str, int]:
    """Refresh open rows: mark fills from Upbit API, update unrealized on open BUY."""
    path = init_crypto_outcomes_db(outcomes_db)
    cutoff = (date.today() - timedelta(days=int(lookback_days))).isoformat()
    stats = {"rows_checked": 0, "fills_synced": 0, "metrics_updated": 0, "closed": 0}

    holdings = {h.market: h for h in broker.get_crypto_holdings()}

    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM crypto_recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? AND closed_at IS NULL",
            (cutoff,),
        ).fetchall()
        stats["rows_checked"] = len(rows)

        for row in rows:
            row_id = int(row["id"])
            market = str(row["market"])
            side = str(row["side"]).lower()
            uuid = str(row["order_uuid"] or "")
            fill_px = _float(row["fill_price"])
            executed = int(row["executed"] or 0)

            if uuid and (not executed or fill_px is None):
                try:
                    raw = broker.get_order(uuid)
                    from deepsignal.crypto_trading.crypto_order_fill import (
                        classify_fill_outcome,
                        normalize_order_status,
                    )

                    status = normalize_order_status(raw)
                    outcome = classify_fill_outcome(status, timed_out=False)
                    if outcome in ("done", "partial") and float(status.get("executed_volume", 0) or 0) > 0:
                        plan = CryptoOrderPlan(
                            market=market,
                            side=side,
                            limit_price=_float(row["current_price"]) or 0.0,
                            avg_buy_price=_float(row["avg_buy_price"]) or 0.0,
                            reason=str(row["reason"] or ""),
                            display_name=str(row["display_name"] or market),
                        )
                        result = UpbitOrderResult(
                            market=market,
                            side="ask" if side == "sell" else "bid",
                            order_type="limit",
                            price=_float(status.get("price")) or 0.0,
                            volume=float(status.get("executed_volume", 0) or 0),
                            krw_amount=0.0,
                            status=str(status.get("state", "")),
                            uuid=uuid,
                            dry_run=broker.config.dry_run,
                        )
                        apply_crypto_fill_update(
                            plan,
                            result,
                            status,
                            outcome,
                            outcomes_db=path,
                            outcome_id=row_id,
                        )
                        stats["fills_synced"] += 1
                        continue
                except Exception:
                    pass

            if side == "buy" and executed and fill_px and fill_px > 0:
                h = holdings.get(market)
                cur = h.current_price if h else None
                if cur and cur > 0:
                    pnl = (cur - fill_px) / fill_px * 100.0
                    conn.execute(
                        """
                        UPDATE crypto_recommendation_outcomes SET
                            max_profit_pct = MAX(COALESCE(max_profit_pct, pnl), ?),
                            max_loss_pct = MIN(COALESCE(max_loss_pct, pnl), ?)
                        WHERE id = ?
                        """,
                        (pnl, pnl, row_id),
                    )
                    stats["metrics_updated"] += 1
            elif side == "buy" and not executed:
                h = holdings.get(market)
                if h and h.pnl_pct is not None:
                    conn.execute(
                        "UPDATE crypto_recommendation_outcomes SET pnl_pct = ? WHERE id = ?",
                        (float(h.pnl_pct), row_id),
                    )
                    stats["metrics_updated"] += 1

        conn.commit()
    return stats


def _query_summary(conn: sqlite3.Connection, *, since_day: str) -> CryptoPerformanceSummary:
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM crypto_recommendation_outcomes WHERE substr(created_at, 1, 10) >= ?",
            (since_day,),
        ).fetchone()[0]
    )
    buys = int(
        conn.execute(
            "SELECT COUNT(*) FROM crypto_recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? AND side = 'buy'",
            (since_day,),
        ).fetchone()[0]
    )
    sells = int(
        conn.execute(
            "SELECT COUNT(*) FROM crypto_recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? AND side = 'sell'",
            (since_day,),
        ).fetchone()[0]
    )
    executed = int(
        conn.execute(
            "SELECT COUNT(*) FROM crypto_recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? AND executed = 1",
            (since_day,),
        ).fetchone()[0]
    )
    closed = int(
        conn.execute(
            "SELECT COUNT(*) FROM crypto_recommendation_outcomes WHERE substr(created_at, 1, 10) >= ? AND closed_at IS NOT NULL",
            (since_day,),
        ).fetchone()[0]
    )
    wins = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM crypto_recommendation_outcomes
            WHERE substr(created_at, 1, 10) >= ? AND closed_at IS NOT NULL AND realized_pnl_pct > 0
            """,
            (since_day,),
        ).fetchone()[0]
    )
    avg_row = conn.execute(
        """
        SELECT AVG(realized_pnl_pct) FROM crypto_recommendation_outcomes
        WHERE substr(created_at, 1, 10) >= ? AND closed_at IS NOT NULL
        """,
        (since_day,),
    ).fetchone()
    sum_row = conn.execute(
        """
        SELECT SUM(realized_pnl_pct) FROM crypto_recommendation_outcomes
        WHERE substr(created_at, 1, 10) >= ? AND closed_at IS NOT NULL
        """,
        (since_day,),
    ).fetchone()
    return CryptoPerformanceSummary(
        days=0,
        total_rows=total,
        buy_count=buys,
        sell_count=sells,
        executed_count=executed,
        closed_count=closed,
        win_count=wins,
        avg_realized_pnl_pct=_float(avg_row[0]) if avg_row else None,
        total_realized_pnl_pct=_float(sum_row[0]) if sum_row else None,
    )


def generate_crypto_performance_report(
    outcomes_db: str | Path,
    *,
    output_dir: str | Path = "outputs",
    days: int = 7,
) -> tuple[Path, Path, CryptoPerformanceSummary]:
    path = init_crypto_outcomes_db(outcomes_db)
    since_day = (date.today() - timedelta(days=max(1, int(days)))).isoformat()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")

    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        summary = _query_summary(conn, since_day=since_day)
        summary.days = int(days)
        by_market = conn.execute(
            """
            SELECT market, side, COUNT(*) AS n, SUM(executed) AS ex,
                   AVG(realized_pnl_pct) AS pnl, AVG(pnl_pct) AS open_pnl
            FROM crypto_recommendation_outcomes WHERE substr(created_at, 1, 10) >= ?
            GROUP BY market, side ORDER BY n DESC LIMIT 20
            """,
            (since_day,),
        ).fetchall()

    win_rate = (summary.win_count / summary.closed_count) if summary.closed_count else None
    body: dict[str, Any] = {
        "generated_at": now,
        "period_days": days,
        "since_date": since_day,
        "summary": {
            "total_rows": summary.total_rows,
            "buy_count": summary.buy_count,
            "sell_count": summary.sell_count,
            "executed_count": summary.executed_count,
            "closed_count": summary.closed_count,
            "win_count": summary.win_count,
            "win_rate": win_rate,
            "avg_realized_pnl_pct": summary.avg_realized_pnl_pct,
            "total_realized_pnl_pct": summary.total_realized_pnl_pct,
        },
        "by_market": [dict(r) for r in by_market],
        "outcomes_db": str(path.resolve()),
    }

    jp = root / CRYPTO_PERFORMANCE_REPORT_JSON
    mp = root / CRYPTO_PERFORMANCE_REPORT_MD
    jp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# DeepSignal — 코인 추천 성능 리포트",
        "",
        f"- Generated: {now}",
        f"- Period: 최근 {days}일 (since {since_day})",
        f"- Outcomes DB: `{path.as_posix()}`",
        "",
        "## 요약",
        "",
        f"- 추천 기록: **{summary.total_rows}** (매수 {summary.buy_count} / 매도 {summary.sell_count})",
        f"- 체결 완료 (`executed`): **{summary.executed_count}**",
        f"- 청산 완료 (`closed`): **{summary.closed_count}**",
    ]
    if win_rate is not None:
        lines.append(f"- 청산 승률: **{win_rate * 100:.1f}%**")
    if summary.avg_realized_pnl_pct is not None:
        lines.append(f"- 평균 실현 수익률: **{summary.avg_realized_pnl_pct:+.2f}%**")
    if summary.total_realized_pnl_pct is not None:
        lines.append(f"- 실현 수익률 합계(청산 건): **{summary.total_realized_pnl_pct:+.2f}%**")
    lines.extend(["", "## 마켓별", "", "| 마켓 | side | 건수 | 체결 | 평균 실현% |", "|------|------|------|------|------------|"])
    for row in by_market:
        lines.append(
            f"| {row['market']} | {row['side']} | {row['n']} | {row['ex']} | "
            f"{row['pnl'] if row['pnl'] is not None else 'n/a'} |"
        )
    lines.append("")
    lines.append("Note: read-only. Upbit only.")
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp, summary


def build_crypto_daily_telegram_summary(
    broker: UpbitBroker,
    *,
    outcomes_db: str | Path,
) -> str:
    """오늘 추천·체결·보유·실현 손익 요약 (한국어)."""
    path = init_crypto_outcomes_db(outcomes_db)
    today = _today_kst()
    lines = ["[DeepSignal 코인 일일 요약]", f"• 기준일: {today}", ""]

    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        today_rows = conn.execute(
            """
            SELECT market, display_name, side, executed, fill_price, fill_volume,
                   realized_pnl_pct, pnl_pct, order_uuid, exit_reason
            FROM crypto_recommendation_outcomes WHERE substr(created_at, 1, 10) = ?
            ORDER BY id ASC
            """,
            (today,),
        ).fetchall()

    buys = [r for r in today_rows if str(r["side"]).lower() == "buy"]
    sells = [r for r in today_rows if str(r["side"]).lower() == "sell"]
    lines.append(f"## 오늘 추천 ({len(today_rows)}건)")
    if not today_rows:
        lines.append("- 오늘 기록된 추천 없음")
    else:
        for r in today_rows:
            ex = "체결" if int(r["executed"] or 0) else "미체결"
            name = r["display_name"] or r["market"]
            side_ko = "매수" if str(r["side"]).lower() == "buy" else "매도"
            lines.append(f"- {side_ko} {name} ({r['market']}) — {ex}")

    filled = [r for r in today_rows if int(r["executed"] or 0)]
    lines.append("")
    lines.append(f"## 오늘 체결 ({len(filled)}건)")
    if not filled:
        lines.append("- 체결 완료 건 없음")
    else:
        for r in filled:
            fp = _float(r["fill_price"])
            fv = _float(r["fill_volume"])
            lines.append(
                f"- {r['display_name']} 체결가 {fp:,.0f}원, 수량 {fv}, uuid {r['order_uuid'] or 'n/a'}"
            )

    holdings = broker.get_crypto_holdings()
    lines.append("")
    lines.append(f"## 현재 보유 ({len(holdings)}종)")
    if not holdings:
        lines.append("- 보유 없음")
    else:
        for h in holdings:
            lines.append(
                f"- {h.market}: 수익률 {h.pnl_pct:+.2f}%, 평가 {h.valuation_krw:,.0f}원"
            )

    realized_today = [
        r for r in today_rows if r["realized_pnl_pct"] is not None and str(r["side"]).lower() == "sell"
    ]
    lines.append("")
    lines.append("## 오늘 실현 손익 (매도 체결)")
    if not realized_today:
        lines.append("- 실현 손익 기록 없음")
    else:
        total = sum(float(r["realized_pnl_pct"]) for r in realized_today)
        for r in realized_today:
            lines.append(f"- {r['display_name']}: {float(r['realized_pnl_pct']):+.2f}% ({r['exit_reason'] or ''})")
        lines.append(f"- 합계(단순 합산 %): {total:+.2f}%")

    return "\n".join(lines)


CRYPTO_DAILY_SUMMARY_HOUR_KST = 21


def is_crypto_daily_summary_time(now: datetime | None = None) -> bool:
    """True during the 21:00 KST hour (first runner tick in that hour sends once per day)."""
    current = now or now_kst()
    return int(current.hour) == CRYPTO_DAILY_SUMMARY_HOUR_KST


def maybe_send_crypto_daily_summary(
    broker: UpbitBroker,
    cfg: Any,
    *,
    outcomes_db: str | Path,
    runner_state: dict[str, Any],
) -> dict[str, Any] | None:
    """하루 1회 Telegram 일일 요약 (21:00 KST 구간)."""
    if not getattr(cfg, "bot_token", None) or not getattr(cfg, "send", False):
        return None
    if not is_crypto_daily_summary_time():
        return None
    today = _today_kst()
    if runner_state.get("last_daily_summary_date") == today:
        return None
    from deepsignal.crypto_trading.crypto_telegram_flow import telegram_send_plain

    text = build_crypto_daily_telegram_summary(broker, outcomes_db=outcomes_db)
    result = telegram_send_plain(cfg, text)
    runner_state["last_daily_summary_date"] = today
    return {"telegram": result, "summary_sent": True}
