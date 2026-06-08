"""실계좌 포지션 고점가(peak_price) 자동 추적 — risk-check 고점 DD용."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _migrate_peaks_table(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS position_price_peaks (
            broker TEXT NOT NULL,
            symbol TEXT NOT NULL,
            peak_price REAL NOT NULL,
            peak_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (broker, symbol)
        )
        """
    )


def load_peak_price(
    db_path: str,
    *,
    broker: str,
    symbol: str,
) -> float | None:
    """저장된 peak_price. 없으면 None."""
    import sqlite3
    from pathlib import Path

    from deepsignal.config.settings import load_settings

    resolved = Path(db_path or load_settings().db_path).expanduser().resolve()
    if not resolved.exists():
        return None
    sym = str(symbol).strip()
    if sym.isdigit():
        sym = sym.zfill(6)
    with sqlite3.connect(str(resolved)) as conn:
        _migrate_peaks_table(conn)
        row = conn.execute(
            "SELECT peak_price FROM position_price_peaks WHERE broker = ? AND symbol = ?",
            (broker, sym),
        ).fetchone()
    if not row:
        return None
    return _float_or_none(row[0])


def update_position_peaks(
    db_path: str,
    broker: str,
    positions: list[dict[str, Any]],
    *,
    snapshot_time: str | None = None,
) -> dict[str, float]:
    """포지션별 peak 갱신 후 symbol→peak_price 맵 반환. positions[].raw에 peak_price 주입."""
    import sqlite3
    from pathlib import Path

    from deepsignal.config.settings import load_settings

    resolved = Path(db_path or load_settings().db_path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    ts = snapshot_time or _now_iso()
    peaks: dict[str, float] = {}

    with sqlite3.connect(str(resolved)) as conn:
        _migrate_peaks_table(conn)
        for p in positions:
            sym = str(p.get("symbol") or "").strip()
            if not sym:
                continue
            if sym.isdigit():
                sym = sym.zfill(6)
            qty = int(p.get("quantity") or 0)
            if qty <= 0:
                continue
            cur = _float_or_none(p.get("current_price"))
            avg = _float_or_none(p.get("avg_price"))
            row = conn.execute(
                "SELECT peak_price FROM position_price_peaks WHERE broker = ? AND symbol = ?",
                (broker, sym),
            ).fetchone()
            prev_peak = _float_or_none(row[0]) if row else None
            candidates = [x for x in (prev_peak, cur, avg) if x is not None]
            if not candidates:
                continue
            new_peak = max(candidates)
            conn.execute(
                """
                INSERT INTO position_price_peaks (broker, symbol, peak_price, peak_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(broker, symbol) DO UPDATE SET
                    peak_price = excluded.peak_price,
                    peak_at = CASE
                        WHEN excluded.peak_price > position_price_peaks.peak_price
                        THEN excluded.peak_at
                        ELSE position_price_peaks.peak_at
                    END,
                    updated_at = excluded.updated_at
                """,
                (broker, sym, new_peak, ts, ts),
            )
            peaks[sym] = new_peak
            raw = p.get("raw")
            if not isinstance(raw, dict):
                raw = {}
            raw["peak_price"] = new_peak
            if prev_peak is not None:
                raw["peak_price_prev"] = prev_peak
            p["raw"] = raw
        conn.commit()
    return peaks


def enrich_positions_with_peaks(
    db_path: str,
    broker: str,
    positions: list[Mapping[str, Any] | dict[str, Any]],
) -> list[dict[str, Any]]:
    """DB peak만 반영(동기화 없이 조회 경로). 저장은 하지 않음."""
    out: list[dict[str, Any]] = []
    for pos in positions:
        d = dict(pos) if isinstance(pos, Mapping) else dict(pos)
        sym = str(d.get("symbol") or "").strip()
        if sym.isdigit():
            sym = sym.zfill(6)
        peak = load_peak_price(db_path, broker=broker, symbol=sym)
        if peak is not None:
            raw = d.get("raw")
            if not isinstance(raw, dict):
                raw = {}
            raw["peak_price"] = peak
            d["raw"] = raw
        out.append(d)
    return out
