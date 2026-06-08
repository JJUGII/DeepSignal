"""Buy-quality filters for crypto recommendations (RSI, volume, volatility)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitTicker
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto


@dataclass
class CryptoBuyQualityConfig:
    max_rsi: float = _CRYPTO.max_rsi
    min_volume_ratio: float = _CRYPTO.min_volume_ratio
    max_atr_pct: float = _CRYPTO.max_atr_pct
    high_volatility_size_multiplier: float = _CRYPTO.high_volatility_size_multiplier
    enabled: bool = True


def _latest_rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's smoothing RSI (standard TradingView definition)."""
    if len(closes) < period + 1:
        return None
    deltas = [float(closes[i]) - float(closes[i - 1]) for i in range(1, len(closes))]
    # Seed with simple average of first window
    avg_gain = sum(max(d, 0.0) for d in deltas[:period]) / period
    avg_loss = sum(max(-d, 0.0) for d in deltas[:period]) / period
    # Wilder's EMA over remaining changes
    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_atr_pct_from_candles(
    candles: list[dict[str, Any]],
    period: int | None = None,
) -> float | None:
    """Public ATR%% helper (average true range / close * 100)."""
    return _atr_pct(candles, period=int(period or _CRYPTO.atr_period))


def _atr_pct(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    prev_close = float(candles[0].get("trade_price") or 0)
    for row in candles[1:]:
        high = float(row.get("high_price") or row.get("trade_price") or 0)
        low = float(row.get("low_price") or row.get("trade_price") or 0)
        close = float(row.get("trade_price") or 0)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        if close > 0:
            trs.append(tr / close * 100.0)
        prev_close = close
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _ema_series(prices: list[float], period: int) -> list[float]:
    k = 2.0 / (period + 1)
    vals = [prices[0]]
    for p in prices[1:]:
        vals.append(p * k + vals[-1] * (1.0 - k))
    return vals


_GC_MIN_CANDLES = 60  # EMA50 수렴 최소치


def golden_cross_status_1d(closes: list[float], lookback: int = 5) -> str | None:
    """일봉 EMA50/200 골든크로스 상태.

    Returns:
        "golden_cross"  — 최근 lookback봉 이내 EMA50이 EMA200 상향 돌파
        "above"         — EMA50 > EMA200 유지 중
        "dead_cross"    — 최근 lookback봉 이내 EMA50이 EMA200 하향 이탈
        "below"         — EMA50 < EMA200 유지 중
        None            — 데이터 부족 (< 60봉)
    """
    if len(closes) < _GC_MIN_CANDLES:
        return None
    e50 = _ema_series(closes, 50)
    e200 = _ema_series(closes, 200)
    n = len(closes)
    for i in range(max(1, n - lookback), n):
        if e50[i - 1] < e200[i - 1] and e50[i] >= e200[i]:
            return "golden_cross"
        if e50[i - 1] >= e200[i - 1] and e50[i] < e200[i]:
            return "dead_cross"
    return "above" if e50[-1] >= e200[-1] else "below"


def _volume_ratio(candles: list[dict[str, Any]], ticker: UpbitTicker) -> float | None:
    values: list[float] = []
    for c in candles:
        vol = float(c.get("candle_acc_trade_volume") or 0)
        px = float(c.get("trade_price") or 0)
        if vol > 0 and px > 0:
            values.append(vol * px)
    if not values:
        return None
    avg = sum(values) / len(values)
    current = float(ticker.acc_trade_price_24h or 0)
    if avg <= 0 or current <= 0:
        return None
    return current / avg


def evaluate_crypto_buy_quality(
    broker: UpbitBroker,
    market: str,
    ticker: UpbitTicker,
    *,
    cfg: CryptoBuyQualityConfig | None = None,
) -> tuple[bool, str, float, dict[str, Any]]:
    """Return ok, reason, krw_amount_multiplier, diagnostics."""
    cfg = cfg or CryptoBuyQualityConfig()
    diag: dict[str, Any] = {"market": market}
    if not cfg.enabled:
        return True, "quality_filters_disabled", 1.0, diag

    try:
        candles = broker.get_daily_candles(market, count=200)
    except Exception:
        diag["candles_error"] = "upbit_market_unavailable"
        return False, "Upbit 미상장/조회불가 종목", 0.0, diag
    if not candles:
        diag["candles_error"] = "no_candles"
        return False, "Upbit 일봉 없음", 0.0, diag
    closes = [float(c.get("trade_price") or 0) for c in candles if float(c.get("trade_price") or 0) > 0]
    rsi = _latest_rsi(closes)
    diag["rsi_14"] = rsi
    # 과열 RSI 캡: 공격성 다이얼이 올리면 급등주(고RSI)도 허용
    _max_rsi = float(cfg.max_rsi)
    try:
        import os as _os_rsi
        _ov_rsi = _os_rsi.environ.get("CRYPTO_MAX_RSI", "").strip()
        if _ov_rsi:
            _max_rsi = max(_max_rsi, float(_ov_rsi))
    except ValueError:
        pass
    if rsi is not None and rsi > _max_rsi:
        return False, f"RSI 과열 ({rsi:.1f} > {_max_rsi})", 0.0, diag

    vol_ratio = _volume_ratio(candles, ticker)
    diag["volume_ratio"] = vol_ratio
    if vol_ratio is not None and vol_ratio < float(cfg.min_volume_ratio):
        return False, f"거래량 부족 (ratio {vol_ratio:.2f} < {cfg.min_volume_ratio})", 0.0, diag

    atr_pct = _atr_pct(candles)
    diag["atr_pct"] = atr_pct
    mult = 1.0
    if atr_pct is not None and atr_pct > float(cfg.max_atr_pct):
        mult = float(cfg.high_volatility_size_multiplier)
        diag["volatility_note"] = f"ATR {atr_pct:.2f}% > {cfg.max_atr_pct}% — 주문금액 {mult:.0%}"

    diag["gc_1d"] = golden_cross_status_1d(closes)
    diag["gc_1d_candles"] = len(closes)

    return True, "quality_ok", mult, diag
