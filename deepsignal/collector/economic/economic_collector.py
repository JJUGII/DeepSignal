"""거시 지표 수집 (yfinance, API 키 불필요)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import yfinance as yf

logger = logging.getLogger(__name__)

# (yfinance 티커, DB·스코어용 표준 이름)
_MACRO_SERIES: tuple[tuple[str, str], ...] = (
    ("^VIX", "VIX"),
    ("DX-Y.NYB", "DXY"),
    ("^TNX", "TNX"),
)


@dataclass
class EconomicIndicator:
    """단일 거시 지표 스냅샷."""

    indicator_name: str
    indicator_date: str
    value: float | None
    source: str = "yfinance"
    raw: dict[str, Any] = field(default_factory=dict)


class EconomicCollector:
    """yfinance 기반 미국 거시 시리즈 수집."""

    def __init__(
        self,
        *,
        period: str = "10d",
        interval: str = "1d",
    ) -> None:
        self._period = period
        self._interval = interval

    def collect_series(self, series_id: str) -> None:
        """추후 FRED/한은 등 연동."""
        raise NotImplementedError

    def collect_macro_indicators(self) -> list[EconomicIndicator]:
        """VIX·DXY·미국채 10Y 등 최신 값 수집. 개별 실패 시 로그만 남기고 계속한다."""
        out: list[EconomicIndicator] = []
        for yf_ticker, name in _MACRO_SERIES:
            row = self._fetch_last_bar(yf_ticker, name)
            if row is not None:
                out.append(row)
        return out

    def _fetch_last_bar(self, yf_ticker: str, indicator_name: str) -> EconomicIndicator | None:
        try:
            hist = yf.Ticker(yf_ticker).history(
                period=self._period,
                interval=self._interval,
                auto_adjust=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "macro yfinance history failed ticker=%s name=%s err=%s",
                yf_ticker,
                indicator_name,
                exc,
            )
            return None

        if hist is None or hist.empty:
            logger.warning("macro empty history ticker=%s name=%s", yf_ticker, indicator_name)
            return None

        try:
            last = hist.iloc[-1]
            idx = hist.index[-1]
            trade_date = _index_to_date_str(idx)
            try:
                val = float(last["Close"])
            except (KeyError, TypeError, ValueError):
                val = None
            if val is not None and _is_nan(val):
                val = None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "macro parse failed ticker=%s name=%s err=%s",
                yf_ticker,
                indicator_name,
                exc,
            )
            return None

        raw = {
            "yf_ticker": yf_ticker,
            "period": self._period,
            "interval": self._interval,
            "close": val,
        }
        return EconomicIndicator(
            indicator_name=indicator_name,
            indicator_date=trade_date,
            value=val,
            source="yfinance",
            raw=raw,
        )


def _is_nan(x: Any) -> bool:
    try:
        import math

        return isinstance(x, float) and math.isnan(x)
    except Exception:
        return False


def _index_to_date_str(idx: Any) -> str:
    """DatetimeIndex / Timestamp → YYYY-MM-DD."""
    try:
        ts = idx
        if hasattr(ts, "strftime"):
            return str(ts.strftime("%Y-%m-%d"))
        s = str(ts)
        return s[:10] if len(s) >= 10 else s
    except Exception:
        return str(idx)[:10]
