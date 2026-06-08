"""일봉 OHLCV 기반 기술지표(RSI, EMA) 계산."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd

from deepsignal.storage.database import fetch_market_prices


def _num_or_nan(x: Any) -> float:
    if x is None:
        return float("nan")
    try:
        v = float(x)
        if math.isnan(v):
            return float("nan")
        return v
    except (TypeError, ValueError):
        return float("nan")


def _float_series(values: Sequence[float | None]) -> pd.Series:
    return pd.Series([_num_or_nan(v) for v in values], dtype="float64")


@dataclass
class TechnicalIndicator:
    """단일 거래일 기술지표 스냅샷."""

    symbol: str
    trade_date: str
    close: float | None
    ema_12: float | None
    ema_26: float | None
    rsi_14: float | None
    trend_score: float | None
    raw: dict[str, Any] = field(default_factory=dict)


def _compute_trend_score(
    close: float | None,
    ema_12: float | None,
    ema_26: float | None,
) -> float | None:
    if close is None or ema_12 is None or ema_26 is None:
        return None
    if math.isnan(close) or math.isnan(ema_12) or math.isnan(ema_26):
        return None

    if close > ema_12 > ema_26:
        return 1.0
    if close < ema_12 < ema_26:
        return -1.0
    if ema_12 > ema_26:
        return 0.5
    if ema_12 < ema_26:
        return -0.5
    return 0.0


class TechnicalAnalyzer:
    """RSI·EMA·단순 trend_score 계산 (매매 신호 아님)."""

    @staticmethod
    def calculate_ema(values: Sequence[float | None], period: int) -> list[float | None]:
        """입력과 동일 길이의 EMA 시퀀스. `min_periods=period` 미만 구간은 None."""
        if period <= 0:
            raise ValueError("period must be positive")
        s = _float_series(values)
        ema = s.ewm(span=period, adjust=False, min_periods=period).mean()
        out: list[float | None] = []
        for x in ema:
            out.append(None if pd.isna(x) else float(x))
        return out

    @staticmethod
    def calculate_rsi(values: Sequence[float | None], period: int = 14) -> list[float | None]:
        """Wilder 스타일 RSI 근사. 입력과 동일 길이. 초기 구간은 None."""
        if period <= 0:
            raise ValueError("period must be positive")
        s = _float_series(values)
        delta = s.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        roll_up = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        roll_down = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        rs = roll_up / roll_down.replace(0.0, float("nan"))
        rsi = 100.0 - (100.0 / (1.0 + rs))
        # 무손실 구간(roll_down==0, roll_up>0): rs=NaN으로 RSI가 None 누락되던 버그.
        # 워밍업(둘 다 NaN)은 그대로 두고, 손실 없는 강추세만 RSI=100으로 보정한다.
        rsi = rsi.mask((roll_down == 0.0) & (roll_up > 0.0), 100.0)
        out: list[float | None] = []
        for x in rsi:
            if pd.isna(x):
                out.append(None)
                continue
            xf = float(x)
            out.append(None if not math.isfinite(xf) else xf)
        return out

    def analyze_prices(self, symbol: str, rows: Sequence[dict[str, Any]]) -> list[TechnicalIndicator]:
        """DB/외부에서 가져온 행(dict) 리스트를 분석한다. `bar_time` 또는 `trade_date` 키 사용."""
        sym = symbol.strip().upper()
        sorted_rows = sorted(
            rows,
            key=lambda r: str(r.get("bar_time") or r.get("trade_date") or ""),
        )
        closes: list[float | None] = []
        dates: list[str] = []
        for r in sorted_rows:
            dt = r.get("bar_time") or r.get("trade_date") or ""
            dates.append(str(dt))
            c = r.get("close")
            if c is None:
                closes.append(None)
            else:
                try:
                    closes.append(float(c))
                except (TypeError, ValueError):
                    closes.append(None)

        ema12 = self.calculate_ema(closes, 12)
        ema26 = self.calculate_ema(closes, 26)
        rsi14 = self.calculate_rsi(closes, 14)

        out: list[TechnicalIndicator] = []
        for i in range(len(sorted_rows)):
            c = closes[i]
            e12 = ema12[i]
            e26 = ema26[i]
            r14 = rsi14[i]
            trend = _compute_trend_score(c, e12, e26)
            raw = {
                "index": i,
                "open": sorted_rows[i].get("open"),
                "high": sorted_rows[i].get("high"),
                "low": sorted_rows[i].get("low"),
                "volume": sorted_rows[i].get("volume"),
            }
            out.append(
                TechnicalIndicator(
                    symbol=sym,
                    trade_date=dates[i],
                    close=c,
                    ema_12=e12,
                    ema_26=e26,
                    rsi_14=r14,
                    trend_score=trend,
                    raw=raw,
                )
            )
        return out

    def analyze_symbol_from_db(
        self,
        db_path: str | None,
        symbol: str,
        source: str = "yfinance",
        limit: int = 120,
    ) -> list[TechnicalIndicator]:
        rows = fetch_market_prices(
            db_path,
            symbol,
            source=source,
            limit=limit,
            timeframe="1d",
        )
        return self.analyze_prices(symbol, rows)
