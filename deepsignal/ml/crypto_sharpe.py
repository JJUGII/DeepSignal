"""Sharpe-style metrics from crypto outcome trades."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import numpy as np


def sharpe_from_fraction_returns(
    returns: list[float] | np.ndarray,
    *,
    annualize_trades_per_day: float = 48.0,
) -> float:
    """returns: decimal fractions (0.01 = 1%)."""
    import numpy as np

    arr = np.asarray(returns, dtype=np.float64)
    if arr.size < 3:
        return 0.0
    rets = [float(x) for x in arr if math.isfinite(float(x))]
    if len(rets) < 3:
        return 0.0
    mean_r = sum(rets) / len(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / max(1, len(rets) - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return 0.0
    scale = math.sqrt(float(annualize_trades_per_day) * 365.0)
    return (mean_r / std) * scale


def sharpe_from_returns(returns_pct: list[float], *, annualize_trades_per_day: float = 48.0) -> float:
    """returns_pct: list of realized PnL in percent points."""
    if len(returns_pct) < 3:
        return 0.0
    rets = [float(r) / 100.0 for r in returns_pct]
    mean_r = sum(rets) / len(rets)
    var = sum((r - mean_r) ** 2 for r in rets) / max(1, len(rets) - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return 0.0
    scale = math.sqrt(float(annualize_trades_per_day) * 365.0)
    return (mean_r / std) * scale


def sharpe_from_outcomes_db(
    outcomes_db: str | Path,
    *,
    lookback_days: int = 14,
    side: str = "sell",
) -> tuple[float, int]:
    path = Path(outcomes_db)
    if path.suffix == ".db" and path.is_file():
        db = path
    elif (path / "crypto_recommendation_outcomes.db").is_file():
        db = path / "crypto_recommendation_outcomes.db"
    elif path.is_file():
        db = path
    else:
        db = path / "crypto_recommendation_outcomes.db"
    if not db.is_file():
        return 0.0, 0

    with sqlite3.connect(str(db)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(crypto_recommendation_outcomes)")}
        if "realized_pnl_pct" not in cols:
            return 0.0, 0
        rows = conn.execute(
            """
            SELECT realized_pnl_pct FROM crypto_recommendation_outcomes
            WHERE side = ? AND realized_pnl_pct IS NOT NULL AND closed_at IS NOT NULL
              AND datetime(closed_at) >= datetime('now', ?)
            """,
            (side.lower(), f"-{int(lookback_days)} days"),
        ).fetchall()
    pnls = [float(r[0]) for r in rows if r[0] is not None]
    return sharpe_from_returns(pnls), len(pnls)
