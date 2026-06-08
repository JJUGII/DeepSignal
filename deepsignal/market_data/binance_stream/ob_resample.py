"""Resample 10s order-book snapshots into 1-minute aggregates."""

from __future__ import annotations

import math
from typing import Any


def _imbalance(bids: list[list[float]], asks: list[list[float]], *, levels: int | None = None) -> float:
    if not bids or not asks:
        return float("nan")
    b = bids[:levels] if levels else bids
    a = asks[:levels] if levels else asks
    bid_qty = sum(float(x[1]) for x in b)
    ask_qty = sum(float(x[1]) for x in a)
    denom = bid_qty + ask_qty
    if denom <= 0:
        return float("nan")
    return (bid_qty - ask_qty) / denom


def _spread_bps(bids: list[list[float]], asks: list[list[float]]) -> float:
    if not bids or not asks:
        return float("nan")
    bb, ba = float(bids[0][0]), float(asks[0][0])
    mid = (bb + ba) / 2.0
    if mid <= 0:
        return float("nan")
    return (ba - bb) / mid * 10_000.0


def _wall_prices(bids: list[list[float]], asks: list[list[float]]) -> tuple[float, float]:
    if not bids or not asks:
        return float("nan"), float("nan")
    max_bid = max(bids, key=lambda x: float(x[1]))
    max_ask = max(asks, key=lambda x: float(x[1]))
    return float(max_bid[0]), float(max_ask[0])


def snapshot_metrics(row: dict[str, Any]) -> dict[str, float]:
    bids = row.get("bids") or []
    asks = row.get("asks") or []
    if not isinstance(bids, list) or not isinstance(asks, list):
        return {}
    imb = _imbalance(bids, asks)
    sp = _spread_bps(bids, asks)
    wb, wa = _wall_prices(bids, asks)
    return {
        "ob_imbalance": imb,
        "spread_bps": sp,
        "wall_bid_price": wb,
        "wall_ask_price": wa,
        "ob_imbalance_l1": _imbalance(bids, asks, levels=1),
        "ob_imbalance_l5": _imbalance(bids, asks, levels=5),
    }


def resample_ob_to_1m_buckets(
    rows: list[dict[str, Any]],
) -> dict[int, dict[str, float]]:
    """
    Group snapshots by minute (ts // 60 * 60).
    Returns {minute_ts_sec: aggregated metrics}.
    """
    buckets: dict[int, list[dict[str, float]]] = {}
    for row in rows:
        ts = int(row.get("ts") or row.get("ts_ms", 0) // 1000)
        if ts <= 0:
            continue
        minute = (ts // 60) * 60
        m = snapshot_metrics(row)
        if m:
            buckets.setdefault(minute, []).append(m)

    out: dict[int, dict[str, float]] = {}
    for minute, samples in buckets.items():
        imbs = [s["ob_imbalance"] for s in samples if not math.isnan(s.get("ob_imbalance", float("nan")))]
        spreads = [s["spread_bps"] for s in samples if not math.isnan(s.get("spread_bps", float("nan")))]
        walls_b = [s["wall_bid_price"] for s in samples if not math.isnan(s.get("wall_bid_price", float("nan")))]
        walls_a = [s["wall_ask_price"] for s in samples if not math.isnan(s.get("wall_ask_price", float("nan")))]
        if not imbs:
            continue
        out[minute] = {
            "ob_imbalance_1m_mean": sum(imbs) / len(imbs),
            "spread_1m_mean": sum(spreads) / len(spreads) if spreads else float("nan"),
            "wall_bid_price_1m": sum(walls_b) / len(walls_b) if walls_b else float("nan"),
            "wall_ask_price_1m": sum(walls_a) / len(walls_a) if walls_a else float("nan"),
        }
    return out
