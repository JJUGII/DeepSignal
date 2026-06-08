"""Order book derived features."""

from __future__ import annotations

import math

from deepsignal.market_data.binance_stream.models import OrderBookSnapshot


def _level_imbalance(book: OrderBookSnapshot, levels: int) -> float:
    bids = book.bids[:levels]
    asks = book.asks[:levels]
    bid_qty = sum(q for _, q in bids)
    ask_qty = sum(q for _, q in asks)
    denom = bid_qty + ask_qty
    if denom <= 0:
        return float("nan")
    return (bid_qty - ask_qty) / denom


def _bid_slope(book: OrderBookSnapshot, *, max_levels: int = 10) -> float:
    """Linear slope of bid quantity vs level index, normalized by mean qty.

    Returns a unit-free ratio: positive = bids thin out away from touch,
    negative = bids get thicker away from touch (unusual / large wall deeper).
    Normalization by y_mean prevents raw-quantity units from exploding
    (e.g. PEPE bids in billions would otherwise give slope ≈ ±3 GHz).
    """
    if len(book.bids) < 2:
        return float("nan")
    levels = min(max_levels, len(book.bids))
    xs = list(range(levels))
    ys = [float(book.bids[i][1]) for i in range(levels)]
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    if y_mean <= 0:
        return float("nan")
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    if den <= 0:
        return float("nan")
    # 정규화: slope / y_mean → 단위 독립적인 비율 (-∞~+∞ 이론, 실제 ±수십 범위)
    return num / den / y_mean


def orderbook_features(book: OrderBookSnapshot | None, *, depth_pct: float = 0.01) -> dict[str, float]:
    nan = float("nan")
    if book is None or not book.bids or not book.asks:
        return {
            "ob_imbalance": nan,
            "ob_spread_frac": nan,
            "ob_depth_1pct": nan,
            "ob_bid_wall_dist_bps": nan,
            "ob_ask_wall_dist_bps": nan,
            "ob_imbalance_l1": nan,
            "ob_imbalance_l5": nan,
            "spread_bps": nan,
            "bid_wall_distance": nan,
            "ask_wall_distance": nan,
            "ob_slope_bid": nan,
            "bid_ask_depth_ratio": nan,
        }

    bid_qty = sum(q for _, q in book.bids)
    ask_qty = sum(q for _, q in book.asks)
    denom = bid_qty + ask_qty
    imbalance = (bid_qty - ask_qty) / denom if denom > 0 else nan

    bb, ba = book.best_bid, book.best_ask
    mid = book.mid_price
    if bb is None or ba is None or mid is None or mid <= 0:
        spread_frac = nan
        spread_bps = nan
    else:
        spread_frac = (ba - bb) / mid
        spread_bps = float(book.spread_bps) if book.spread_bps is not None else spread_frac * 10_000.0

    lo = mid * (1.0 - depth_pct)
    hi = mid * (1.0 + depth_pct)
    depth_bid = sum(q for p, q in book.bids if p >= lo)
    depth_ask = sum(q for p, q in book.asks if p <= hi)
    depth_1pct = (depth_bid + depth_ask) / denom if denom > 0 else nan
    depth_sum_1pct = depth_bid + depth_ask
    bid_ask_depth_ratio = depth_bid / depth_sum_1pct if depth_sum_1pct > 0 else nan

    max_bid = max(book.bids, key=lambda x: x[1])
    max_ask = max(book.asks, key=lambda x: x[1])
    bid_wall_bps = (mid - max_bid[0]) / mid * 10_000.0 if mid and mid > 0 else nan
    ask_wall_bps = (max_ask[0] - mid) / mid * 10_000.0 if mid and mid > 0 else nan
    bid_wall_dist = (mid - max_bid[0]) / mid if mid and mid > 0 else nan
    ask_wall_dist = (max_ask[0] - mid) / mid if mid and mid > 0 else nan

    return {
        "ob_imbalance": float(imbalance) if not math.isnan(imbalance) else nan,
        "ob_spread_frac": float(spread_frac) if not math.isnan(spread_frac) else nan,
        "ob_depth_1pct": float(depth_1pct) if not math.isnan(depth_1pct) else nan,
        "ob_bid_wall_dist_bps": float(bid_wall_bps),
        "ob_ask_wall_dist_bps": float(ask_wall_bps),
        "ob_imbalance_l1": _level_imbalance(book, 1),
        "ob_imbalance_l5": _level_imbalance(book, 5),
        "spread_bps": float(spread_bps) if not math.isnan(spread_bps) else nan,
        "bid_wall_distance": float(bid_wall_dist),
        "ask_wall_distance": float(ask_wall_dist),
        "ob_slope_bid": _bid_slope(book),
        "bid_ask_depth_ratio": float(bid_ask_depth_ratio) if not math.isnan(bid_ask_depth_ratio) else nan,
    }
