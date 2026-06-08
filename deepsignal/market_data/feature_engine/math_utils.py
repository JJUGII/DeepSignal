"""Small numeric helpers (no pandas dependency)."""

from __future__ import annotations

import math
from collections import deque
from typing import Sequence

import numpy as np


def safe_return(current: float, past: float) -> float:
    if past is None or past <= 0 or current <= 0:
        return float("nan")
    return (current / past) - 1.0


def trend_sign(closes: Sequence[float], lookback: int = 2) -> int:
    if len(closes) < lookback + 1:
        return 0
    a = closes[-1]
    b = closes[-1 - lookback]
    if a > b * 1.0001:
        return 1
    if a < b * 0.9999:
        return -1
    return 0


def sma(values: Sequence[float], period: int) -> float:
    if len(values) < period or period <= 0:
        return float("nan")
    chunk = list(values)[-period:]
    return float(sum(chunk) / len(chunk))


def stddev(values: Sequence[float], period: int) -> float:
    if len(values) < period or period < 2:
        return float("nan")
    chunk = list(values)[-period:]
    m = sum(chunk) / len(chunk)
    var = sum((x - m) ** 2 for x in chunk) / (len(chunk) - 1)
    return math.sqrt(max(0.0, var))


def atr_pct(bars: Sequence[tuple[float, float, float, float]], period: int = 14) -> float:
    """bars: (high, low, close) sequences; returns ATR as % of last close."""
    if len(bars) < period + 1:
        return float("nan")
    trs: list[float] = []
    prev_close = bars[-period - 1][2]
    for i in range(-period, 0):
        h, l, c = bars[i]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    last_close = bars[-1][2]
    if last_close <= 0:
        return float("nan")
    return (sum(trs) / len(trs)) / last_close * 100.0


def bollinger_position(closes: Sequence[float], period: int = 20, k: float = 2.0) -> float:
    if len(closes) < period:
        return float("nan")
    m = sma(closes, period)
    sd = stddev(closes, period)
    if math.isnan(m) or math.isnan(sd) or sd <= 0:
        return float("nan")
    upper = m + k * sd
    lower = m - k * sd
    width = upper - lower
    if width <= 0:
        return float("nan")
    return float(np.clip((closes[-1] - lower) / width, 0.0, 1.0))


def forward_fill_vector(
    current: np.ndarray,
    previous: np.ndarray | None,
    *,
    default: float = 0.0,
) -> np.ndarray:
    out = np.array(current, dtype=np.float64, copy=True)
    if previous is None:
        out[np.isnan(out)] = default
        return out
    prev = np.asarray(previous, dtype=np.float64)
    for i in range(len(out)):
        if np.isnan(out[i]) and i < len(prev) and not np.isnan(prev[i]):
            out[i] = prev[i]
        elif np.isnan(out[i]):
            out[i] = default
    return out


def rsi(closes: Sequence[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return float("nan")
    gains: list[float] = []
    losses: list[float] = []
    chunk = list(closes)[-(period + 1) :]
    for i in range(1, len(chunk)):
        d = chunk[i] - chunk[i - 1]
        if d >= 0:
            gains.append(d)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-d)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def ema(values: Sequence[float], period: int) -> float:
    if len(values) < period or period <= 0:
        return float("nan")
    k = 2.0 / (period + 1)
    ema_val = float(values[-period])
    for v in list(values)[-period + 1 :]:
        ema_val = float(v) * k + ema_val * (1.0 - k)
    return ema_val


def vwap_from_bars(bars: Sequence[tuple[float, float, float, float]]) -> float:
    """(high, low, close, volume) → VWAP."""
    num = 0.0
    den = 0.0
    for h, l, c, v in bars:
        if v <= 0:
            continue
        tp = (h + l + c) / 3.0
        num += tp * v
        den += v
    if den <= 0:
        return float("nan")
    return num / den


class TickReturnBuffer:
    def __init__(self, maxlen: int = 20) -> None:
        self._rets: deque[float] = deque(maxlen=maxlen)
        self._last_px: float | None = None

    def on_price(self, price: float) -> None:
        if price <= 0:
            return
        if self._last_px is not None and self._last_px > 0:
            self._rets.append(math.log(price / self._last_px))
        self._last_px = price

    def realized_vol(self) -> float:
        if len(self._rets) < 2:
            return float("nan")
        return stddev(list(self._rets), len(self._rets)) * 100.0
