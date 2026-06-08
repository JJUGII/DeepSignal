"""Paper trading period counter (outputs/CRYPTO_PAPER_STATE.json)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_paper_mode import crypto_paper_mode_enabled
from deepsignal.crypto_trading.crypto_recommendation_outcomes import crypto_outcomes_db_path, init_crypto_outcomes_db
from deepsignal.live_trading.time_utils import now_kst, now_kst_iso

CRYPTO_PAPER_STATE_FILENAME = "CRYPTO_PAPER_STATE.json"
DEFAULT_UNLOCK_DAYS = 14
DEFAULT_UNLOCK_CONDITION = "elapsed_days >= 14"


@dataclass
class CryptoPaperState:
    start_date: str
    elapsed_days: int
    unlock_condition: str
    required_days: int
    last_tick_date: str
    paper_mode: bool
    updated_at: str

    @property
    def remaining_days(self) -> int:
        return max(0, int(self.required_days) - int(self.elapsed_days))

    @property
    def unlocked(self) -> bool:
        return int(self.elapsed_days) >= int(self.required_days)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date,
            "elapsed_days": self.elapsed_days,
            "unlock_condition": self.unlock_condition,
            "required_days": self.required_days,
            "remaining_days": self.remaining_days,
            "unlocked": self.unlocked,
            "last_tick_date": self.last_tick_date,
            "paper_mode": self.paper_mode,
            "updated_at": self.updated_at,
        }


@dataclass
class CryptoPaperPerformanceSummary:
    lookback_days: int
    closed_trades: int
    win_count: int
    avg_realized_pnl_pct: float | None
    total_realized_pnl_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lookback_days": self.lookback_days,
            "closed_trades": self.closed_trades,
            "win_count": self.win_count,
            "avg_realized_pnl_pct": self.avg_realized_pnl_pct,
            "total_realized_pnl_pct": self.total_realized_pnl_pct,
        }


def paper_state_path(output_dir: str | Path) -> Path:
    return Path(output_dir).expanduser().resolve() / CRYPTO_PAPER_STATE_FILENAME


def _today() -> str:
    return now_kst().date().isoformat()


def _parse_required_days(unlock_condition: str, *, default: int = DEFAULT_UNLOCK_DAYS) -> int:
    text = (unlock_condition or "").strip()
    for token in text.replace(">=", " ").split():
        if token.isdigit():
            return int(token)
    return int(default)


def load_paper_state(output_dir: str | Path) -> CryptoPaperState | None:
    path = paper_state_path(output_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    unlock = str(data.get("unlock_condition") or DEFAULT_UNLOCK_CONDITION)
    required = int(data.get("required_days") or _parse_required_days(unlock))
    return CryptoPaperState(
        start_date=str(data.get("start_date") or _today()),
        elapsed_days=int(data.get("elapsed_days") or 0),
        unlock_condition=unlock,
        required_days=required,
        last_tick_date=str(data.get("last_tick_date") or ""),
        paper_mode=bool(data.get("paper_mode", True)),
        updated_at=str(data.get("updated_at") or ""),
    )


def save_paper_state(output_dir: str | Path, state: CryptoPaperState) -> Path:
    path = paper_state_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = state.to_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def touch_paper_state(output_dir: str | Path) -> CryptoPaperState | None:
    """Create or bump elapsed_days once per KST calendar day while paper mode is on."""
    if not crypto_paper_mode_enabled():
        return load_paper_state(output_dir)

    today = _today()
    existing = load_paper_state(output_dir)
    unlock = DEFAULT_UNLOCK_CONDITION
    required = DEFAULT_UNLOCK_DAYS

    if existing is None:
        state = CryptoPaperState(
            start_date=today,
            elapsed_days=1,
            unlock_condition=unlock,
            required_days=required,
            last_tick_date=today,
            paper_mode=True,
            updated_at=now_kst_iso(),
        )
        save_paper_state(output_dir, state)
        return state

    elapsed = int(existing.elapsed_days)
    if existing.last_tick_date != today:
        elapsed += 1
    state = CryptoPaperState(
        start_date=existing.start_date,
        elapsed_days=elapsed,
        unlock_condition=existing.unlock_condition,
        required_days=int(existing.required_days),
        last_tick_date=today,
        paper_mode=True,
        updated_at=now_kst_iso(),
    )
    save_paper_state(output_dir, state)
    return state


def summarize_paper_outcomes(
    output_dir: str | Path,
    *,
    lookback_days: int = 14,
) -> CryptoPaperPerformanceSummary:
    """Closed SELL rows with paper=1 since paper start (or lookback window)."""
    init_crypto_outcomes_db(output_dir)
    db = crypto_outcomes_db_path(output_dir)
    state = load_paper_state(output_dir)
    since = (date.today() - timedelta(days=max(1, int(lookback_days)))).isoformat()
    if state and state.start_date:
        since = max(since, state.start_date)

    with sqlite3.connect(str(db)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(crypto_recommendation_outcomes)")}
        paper_clause = " AND paper = 1" if "paper" in cols else ""
        closed = int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM crypto_recommendation_outcomes
                WHERE side = 'sell' AND closed_at IS NOT NULL
                  AND substr(created_at, 1, 10) >= ?{paper_clause}
                """,
                (since,),
            ).fetchone()[0]
        )
        wins = int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM crypto_recommendation_outcomes
                WHERE side = 'sell' AND closed_at IS NOT NULL AND realized_pnl_pct > 0
                  AND substr(created_at, 1, 10) >= ?{paper_clause}
                """,
                (since,),
            ).fetchone()[0]
        )
        avg_row = conn.execute(
            f"""
            SELECT AVG(realized_pnl_pct) FROM crypto_recommendation_outcomes
            WHERE side = 'sell' AND closed_at IS NOT NULL
              AND substr(created_at, 1, 10) >= ?{paper_clause}
            """,
            (since,),
        ).fetchone()
        sum_row = conn.execute(
            f"""
            SELECT SUM(realized_pnl_pct) FROM crypto_recommendation_outcomes
            WHERE side = 'sell' AND closed_at IS NOT NULL
              AND substr(created_at, 1, 10) >= ?{paper_clause}
            """,
            (since,),
        ).fetchone()

    def _f(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return CryptoPaperPerformanceSummary(
        lookback_days=int(lookback_days),
        closed_trades=closed,
        win_count=wins,
        avg_realized_pnl_pct=_f(avg_row[0]) if avg_row else None,
        total_realized_pnl_pct=_f(sum_row[0]) if sum_row else None,
    )


def format_paper_status_report(output_dir: str | Path) -> str:
    paper_on = crypto_paper_mode_enabled()
    state = load_paper_state(output_dir) if paper_on else load_paper_state(output_dir)
    perf = summarize_paper_outcomes(output_dir)

    lines = [
        "DeepSignal crypto paper status",
        f"CRYPTO_PAPER_MODE: {'true (orders blocked)' if paper_on else 'false (live allowed if execute + UPBIT_DRY_RUN=false)'}",
    ]
    if state:
        lines.extend(
            [
                f"start_date: {state.start_date}",
                f"elapsed_days: {state.elapsed_days} / {state.required_days}",
                f"remaining_days: {state.remaining_days}",
                f"unlock_condition: {state.unlock_condition}",
                f"unlocked: {state.unlocked}",
            ]
        )
    elif paper_on:
        lines.append("paper_state: not initialized (run crypto-auto-runner once)")
    else:
        lines.append("paper_state: inactive (live mode)")

    lines.extend(
        [
            "--- paper outcomes (closed sells, paper=1) ---",
            f"closed_trades: {perf.closed_trades}",
            f"wins: {perf.win_count}",
            f"avg_realized_pnl_pct: {perf.avg_realized_pnl_pct}",
            f"total_realized_pnl_pct: {perf.total_realized_pnl_pct}",
        ]
    )
    if paper_on and state and not state.unlocked:
        lines.append(
            f"To go live after paper period: wait {state.remaining_days} day(s), then set CRYPTO_PAPER_MODE=false"
        )
    return "\n".join(lines)
