"""Round-trip crypto trades for ML feedback (entry/exit, features, probs)."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
from deepsignal.crypto_trading.crypto_paper_mode import crypto_paper_mode_enabled
from deepsignal.live_trading.time_utils import now_kst_iso
from deepsignal.market_data.feature_engine.spec import FEATURE_COUNT, FEATURE_NAMES

CRYPTO_TRADES_DB_NAME = "crypto_trades.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crypto_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_time TEXT,
    exit_price REAL,
    position_size REAL,
    features_snapshot TEXT,
    lgbm_prob REAL,
    lstm_prob REAL,
    ensemble_prob REAL,
    actual_return REAL,
    exit_reason TEXT,
    gate_mode TEXT,
    paper INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_crypto_trades_symbol ON crypto_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_crypto_trades_entry ON crypto_trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_crypto_trades_open ON crypto_trades(symbol, exit_time);
-- 중복 진입 방지: 동일 (심볼·진입시각·진입가) 거래는 1건만 (INSERT OR IGNORE)
CREATE UNIQUE INDEX IF NOT EXISTS uq_crypto_trades_entry
    ON crypto_trades(symbol, entry_time, entry_price);
"""


def crypto_trades_db_path(output_dir: str | Path) -> Path:
    p = Path(output_dir).expanduser()
    if p.name == CRYPTO_TRADES_DB_NAME or (p.suffix == ".db" and "trades" in p.name.lower()):
        return p.resolve()
    return (p / CRYPTO_TRADES_DB_NAME).resolve()


def init_crypto_trades_db(path: str | Path) -> Path:
    resolved = crypto_trades_db_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(resolved)) as conn:
        conn.executescript(_SCHEMA_SQL)
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


def features_snapshot_json(
    snapshot: dict[str, float] | list[float] | Any | None,
) -> str | None:
    """Serialize feature vector; failures return None (must not block fills)."""
    if snapshot is None:
        return None
    try:
        if isinstance(snapshot, dict):
            vec = [float(snapshot.get(name, 0.0)) for name in FEATURE_NAMES]
            return json.dumps(vec)
        if hasattr(snapshot, "tolist"):
            vec = list(snapshot.tolist())  # type: ignore[union-attr]
            return json.dumps(vec)
        if isinstance(snapshot, (list, tuple)):
            vec = [float(x) for x in snapshot]
            if len(vec) != FEATURE_COUNT and len(vec) > 0:
                return json.dumps(vec)
            if len(vec) == FEATURE_COUNT:
                return json.dumps(vec)
        return None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def extract_ml_probs_from_plan(plan: CryptoOrderPlan) -> tuple[float | None, float | None, float | None]:
    bd = plan.score_breakdown if isinstance(plan.score_breakdown, dict) else {}
    ens = bd.get("ml_ensemble") if isinstance(bd.get("ml_ensemble"), dict) else {}
    lgbm = _float(ens.get("lgbm_p")) or _float(bd.get("win_probability"))
    lstm = _float(ens.get("seq_p"))
    blend = _float(ens.get("blended_p"))
    gates = plan.quality_gates if isinstance(plan.quality_gates, dict) else {}
    if lgbm is None and gates.get("win_probability"):
        try:
            lgbm = float(str(gates["win_probability"]))
        except (TypeError, ValueError):
            pass
    return lgbm, lstm, blend


def normalize_exit_reason(plan: CryptoOrderPlan) -> str:
    trig = str(plan.sell_trigger or "").strip().lower()
    mapping = {
        "ai_stop": "model",
        "trailing_stop": "trailing",
        "time_stop": "time",
        "partial_take_profit": "tp",
        "take_profit": "tp",
        "near_take_profit": "tp",
        "stop_loss": "sl",
        "near_stop_loss": "sl",
        "overweight_reduce": "manual",
    }
    if trig in mapping:
        return mapping[trig]
    reason = str(plan.reason or "").lower()
    if "trailing" in reason:
        return "trailing"
    if "time" in reason and "stop" in reason:
        return "time"
    if "ai" in reason and "p(win)" in reason:
        return "model"
    if "익절" in reason or "take_profit" in reason or "+1.2%" in reason:
        return "tp"
    if "손절" in reason or "stop" in reason:
        return "sl"
    return "manual"


def compute_actual_return_net(
    *,
    entry_price: float,
    exit_price: float,
    entry_fee: float = 0.0,
    exit_fee: float = 0.0,
    position_size: float = 1.0,
) -> float | None:
    if entry_price <= 0 or exit_price <= 0:
        return None
    gross = (exit_price / entry_price) - 1.0
    notional = entry_price * max(position_size, 1e-12)
    fee_frac = (float(entry_fee) + float(exit_fee)) / notional
    return gross - fee_frac


@dataclass
class CryptoTradeRow:
    id: int
    symbol: str
    entry_time: str
    entry_price: float
    exit_time: str | None
    exit_price: float | None
    position_size: float | None
    features_snapshot: str | None
    lgbm_prob: float | None
    lstm_prob: float | None
    ensemble_prob: float | None
    actual_return: float | None
    exit_reason: str | None
    gate_mode: str | None
    paper: bool


def _row_from_sql(row: sqlite3.Row) -> CryptoTradeRow:
    return CryptoTradeRow(
        id=int(row["id"]),
        symbol=str(row["symbol"]),
        entry_time=str(row["entry_time"]),
        entry_price=float(row["entry_price"]),
        exit_time=row["exit_time"],
        exit_price=_float(row["exit_price"]),
        position_size=_float(row["position_size"]),
        features_snapshot=row["features_snapshot"],
        lgbm_prob=_float(row["lgbm_prob"]),
        lstm_prob=_float(row["lstm_prob"]),
        ensemble_prob=_float(row["ensemble_prob"]),
        actual_return=_float(row["actual_return"]),
        exit_reason=row["exit_reason"],
        gate_mode=row["gate_mode"],
        paper=bool(int(row["paper"] or 0)),
    )


def find_open_trade_id(conn: sqlite3.Connection, symbol: str) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM crypto_trades
        WHERE symbol = ? AND exit_time IS NULL
        ORDER BY entry_time DESC LIMIT 1
        """,
        (symbol.upper(),),
    ).fetchone()
    return int(row[0]) if row else None


def record_crypto_trade_entry(
    plan: CryptoOrderPlan,
    *,
    fill_price: float,
    fill_volume: float,
    trades_db: str | Path,
    entry_time: str | None = None,
) -> int | None:
    """INSERT on buy fill. Returns trade id or None on failure (never raises)."""
    try:
        path = init_crypto_trades_db(trades_db)
        symbol = str(plan.market).upper()
        px = float(fill_price)
        vol = float(fill_volume)
        if px <= 0 or vol <= 0:
            return None

        from deepsignal.crypto_trading.crypto_gate_config import crypto_gate_mode

        snap_raw = None
        bd = plan.score_breakdown if isinstance(plan.score_breakdown, dict) else {}
        snap_raw = bd.get("features_snapshot")
        snap_json = features_snapshot_json(snap_raw)
        lgbm, lstm, blend = extract_ml_probs_from_plan(plan)

        entry_time_val = entry_time or plan.created_at or now_kst_iso()
        with sqlite3.connect(str(path)) as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO crypto_trades (
                    symbol, entry_time, entry_price, position_size,
                    features_snapshot, lgbm_prob, lstm_prob, ensemble_prob,
                    gate_mode, paper
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    symbol, entry_time_val, px, vol, snap_json,
                    lgbm, lstm, blend, crypto_gate_mode(),
                    1 if crypto_paper_mode_enabled() else 0,
                ),
            )
            conn.commit()
            if cur.rowcount and cur.lastrowid:
                return int(cur.lastrowid)
            # 중복으로 무시됨 → 기존 거래 id 반환 (outcome 추적 연속성)
            row = conn.execute(
                "SELECT id FROM crypto_trades WHERE symbol=? AND entry_time=? AND entry_price=?",
                (symbol, entry_time_val, px),
            ).fetchone()
            return int(row[0]) if row else None
    except Exception:
        return None


def record_crypto_trade_exit(
    plan: CryptoOrderPlan,
    *,
    fill_price: float,
    fill_volume: float,
    fee: float = 0.0,
    trades_db: str | Path,
    exit_time: str | None = None,
    fill_complete: bool = True,
) -> dict[str, Any]:
    """UPDATE open trade on sell fill. Partial fills skip close until fill_complete."""
    stats: dict[str, Any] = {"updated": False, "trade_id": None}
    if not fill_complete:
        return stats
    try:
        path = init_crypto_trades_db(trades_db)
        symbol = str(plan.market).upper()
        exit_px = float(fill_price)
        vol = float(fill_volume)
        if exit_px <= 0:
            return stats

        with sqlite3.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            tid = find_open_trade_id(conn, symbol)
            if tid is None:
                return stats
            row = conn.execute("SELECT * FROM crypto_trades WHERE id = ?", (tid,)).fetchone()
            if row is None:
                return stats
            entry_px = float(row["entry_price"])
            pos = float(row["position_size"] or vol)
            entry_fee = 0.0
            actual = compute_actual_return_net(
                entry_price=entry_px,
                exit_price=exit_px,
                entry_fee=entry_fee,
                exit_fee=float(fee or 0),
                position_size=pos,
            )
            conn.execute(
                """
                UPDATE crypto_trades SET
                    exit_time = ?,
                    exit_price = ?,
                    actual_return = ?,
                    exit_reason = ?
                WHERE id = ?
                """,
                (
                    exit_time or now_kst_iso(),
                    exit_px,
                    actual,
                    normalize_exit_reason(plan),
                    tid,
                ),
            )
            conn.commit()
            stats["updated"] = True
            stats["trade_id"] = tid
            stats["actual_return"] = actual
    except Exception:
        pass
    return stats


def load_closed_trades(
    trades_db: str | Path,
    *,
    lookback_days: int = 14,
    paper: bool | None = None,
) -> list[CryptoTradeRow]:
    path = init_crypto_trades_db(trades_db)
    cutoff = (datetime.now() - timedelta(days=int(lookback_days))).isoformat(timespec="seconds")
    rows: list[CryptoTradeRow] = []
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        q = """
            SELECT * FROM crypto_trades
            WHERE exit_time IS NOT NULL AND actual_return IS NOT NULL
              AND entry_time >= ?
        """
        params: list[Any] = [cutoff]
        if paper is not None:
            q += " AND paper = ?"
            params.append(1 if paper else 0)
        q += " ORDER BY entry_time ASC"
        for row in conn.execute(q, params):
            rows.append(_row_from_sql(row))
    return rows


def count_closed_trades(trades_db: str | Path, *, lookback_days: int = 14) -> int:
    return len(load_closed_trades(trades_db, lookback_days=lookback_days))
