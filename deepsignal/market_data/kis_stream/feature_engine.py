"""KIS 국내주식 실시간 피처 엔진.

KisOhlcvBar + KisOrderBookSnapshot → KStockFeatures → K-GSQS 채점

Binance FeatureEngine 구조를 참고하여 KIS 데이터 특성에 맞게 구현.
주요 피처:
  - MA5/MA20 (1분봉 기준)
  - VWAP (당일 누적)
  - 단기 수익률 (1m/5m/15m/1d)
  - 거래량 배수 및 매수비율
  - 호가 불균형 (bid/ask ratio, spread_bps)
  - 체결강도 (KIS H0STCNT0에서 직접)
  - ATR(14) 기반 변동성
  - KOSPI 상대 수익률
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from deepsignal.market_data.feature_engine.math_utils import atr_pct, sma
from deepsignal.market_data.kis_stream.models import (
    KisOhlcvBar,
    KisOrderBookSnapshot,
    KisTradeTick,
)
from deepsignal.scoring.kstock_scorer import KStockFeatures


@dataclass
class KisSymbolState:
    """심볼별 실시간 상태."""

    # 1분봉 버퍼 (최근 120개)
    closes_1m: deque[int] = field(default_factory=lambda: deque(maxlen=120))
    volumes_1m: deque[int] = field(default_factory=lambda: deque(maxlen=60))
    highs_1m: deque[int] = field(default_factory=lambda: deque(maxlen=60))
    lows_1m: deque[int] = field(default_factory=lambda: deque(maxlen=60))
    opens_1m: deque[int] = field(default_factory=lambda: deque(maxlen=60))
    buy_counts_1m: deque[int] = field(default_factory=lambda: deque(maxlen=60))
    trade_counts_1m: deque[int] = field(default_factory=lambda: deque(maxlen=60))

    # 5분봉 버퍼 (최근 40개)
    closes_5m: deque[int] = field(default_factory=lambda: deque(maxlen=40))
    volumes_5m: deque[int] = field(default_factory=lambda: deque(maxlen=40))

    # 15분봉 버퍼 (최근 20개)
    closes_15m: deque[int] = field(default_factory=lambda: deque(maxlen=20))

    # 일봉 (워밍업 데이터)
    closes_1d: deque[int] = field(default_factory=lambda: deque(maxlen=30))
    prev_close: int = 0  # 전일 종가

    # 당일 VWAP 누적
    today_price_vol_sum: float = 0.0
    today_vol_sum: int = 0
    today_open: int = 0

    # 호가 스냅샷
    last_ob: KisOrderBookSnapshot | None = None

    # 마지막 체결 틱
    last_tick: KisTradeTick | None = None

    # 최근 5분 체결강도 추적
    strength_buf: deque[float] = field(default_factory=lambda: deque(maxlen=10))

    # ATR을 위한 (h, l, c) 버퍼
    hlc_1m: deque[tuple[int, int, int]] = field(default_factory=lambda: deque(maxlen=30))


class StockFeatureEngine:
    """KIS 실시간 데이터로 K-GSQS 피처 벡터를 계산."""

    def __init__(self, kospi_symbol: str = "KOSPI") -> None:
        self.kospi_symbol = kospi_symbol
        self._states: dict[str, KisSymbolState] = {}
        # KOSPI 지수 수익률 추적
        self._kospi_closes_5m: deque[float] = deque(maxlen=40)
        self._sector_closes_5m: dict[str, deque[float]] = {}

    def _state(self, symbol: str) -> KisSymbolState:
        if symbol not in self._states:
            self._states[symbol] = KisSymbolState()
        return self._states[symbol]

    def on_bar(self, bar: KisOhlcvBar, is_historical: bool = False) -> None:
        """완성된 봉 처리."""
        sym = bar.symbol
        st = self._state(sym)

        if bar.timeframe == "1m":
            st.closes_1m.append(bar.close)
            st.volumes_1m.append(bar.volume)
            st.highs_1m.append(bar.high)
            st.lows_1m.append(bar.low)
            st.opens_1m.append(bar.open)
            st.hlc_1m.append((bar.high, bar.low, bar.close))
            st.trade_counts_1m.append(bar.trade_count)
            # buy_count 근사: buy_ratio * trade_count
            buy_count = int(bar.buy_ratio * bar.trade_count)
            st.buy_counts_1m.append(buy_count)

            if not is_historical:
                # 당일 VWAP 업데이트
                st.today_price_vol_sum += bar.close * bar.volume
                st.today_vol_sum += bar.volume
                if st.today_open == 0 and bar.open > 0:
                    st.today_open = bar.open

        elif bar.timeframe == "5m":
            st.closes_5m.append(bar.close)
            st.volumes_5m.append(bar.volume)

        elif bar.timeframe == "15m":
            st.closes_15m.append(bar.close)

        elif bar.timeframe == "1d":
            if not is_historical:
                pass
            else:
                st.closes_1d.append(bar.close)
                # pykrx는 장 중 오늘 데이터를 포함하지 않으므로
                # closes_1d[-1]이 항상 가장 최근 완성된 일봉 종가 (= 전일 종가)
                st.prev_close = int(st.closes_1d[-1])

    def on_tick(self, tick: KisTradeTick) -> None:
        """체결 틱 처리."""
        st = self._state(tick.symbol)
        st.last_tick = tick
        if tick.strength > 0:
            st.strength_buf.append(tick.strength)

    def on_orderbook(self, ob: KisOrderBookSnapshot) -> None:
        """호가 스냅샷 업데이트."""
        st = self._state(ob.symbol)
        st.last_ob = ob

    def set_prev_close(self, symbol: str, prev_close: int) -> None:
        """전일 종가 직접 설정 (워밍업 후)."""
        self._state(symbol).prev_close = prev_close

    def set_kospi_price(self, price: float, timeframe: str = "5m") -> None:
        """KOSPI 지수 가격 업데이트."""
        if timeframe == "5m":
            self._kospi_closes_5m.append(price)

    def build_features(self, symbol: str) -> KStockFeatures | None:
        """현재 상태로 KStockFeatures 계산.

        최소 데이터 조건 미충족 시 None 반환.
        """
        st = self._state(symbol)
        tick = st.last_tick
        ob = st.last_ob

        if tick is None:
            return None
        if tick.price <= 0:
            return None

        price = tick.price
        ts_ms = tick.ts_ms

        # ─── MA ───
        ma5 = _sma_int(st.closes_1m, 5)
        ma20 = _sma_int(st.closes_1m, 20)

        # ─── VWAP ───
        vwap = (
            st.today_price_vol_sum / st.today_vol_sum
            if st.today_vol_sum > 0
            else float(price)
        )

        # ─── 수익률 ───
        ret_1m = _ret(st.closes_1m, 1)
        ret_5m = _ret(st.closes_5m, 1) if len(st.closes_5m) >= 2 else _ret(st.closes_1m, 5)
        ret_15m = _ret(st.closes_15m, 1) if len(st.closes_15m) >= 2 else _ret(st.closes_1m, 15)
        prev_c = st.prev_close if st.prev_close > 0 else (int(st.closes_1m[0]) if st.closes_1m else 0)
        ret_1d = (price - prev_c) / prev_c * 100 if prev_c > 0 else 0.0

        # ─── 거래량 ───
        vol_ratio_5m = _vol_ratio(st.volumes_1m, 5)
        vol_ratio_20m = _vol_ratio(st.volumes_1m, 20)

        # 최근 5분봉 매수비율
        buy_ratio_5m = _buy_ratio(st.buy_counts_1m, st.trade_counts_1m, 5)

        # 당일 누적거래량 비율 (장 진행 시간 보정)
        # 09:00~15:30 = 390분. 현재까지 진행된 시간 비율로 정규화
        now_kst = _now_kst_minutes()
        elapsed = max(1, now_kst - 545)  # 9:05부터 (545 = 9*60+5)
        total_session = 370  # 9:05 ~ 15:15
        time_ratio = min(1.0, elapsed / total_session)
        expected_vol = st.today_vol_sum / time_ratio if time_ratio > 0.05 else st.today_vol_sum
        # 전일 평균 거래량 (1d 봉에서)
        avg_vol_1d = float(sma(list(st.closes_1d), min(5, len(st.closes_1d)))) if st.closes_1d else 0.0
        acml_vol_ratio = expected_vol / avg_vol_1d if avg_vol_1d > 0 else 1.0

        # ─── 호가 ───
        bid_ask_ratio = 1.0
        spread_bps = 5.0
        ob_depth_bid = 0
        ob_depth_ask = 0
        if ob is not None:
            bar_ = ob.bid_ask_ratio
            if bar_ is not None:
                bid_ask_ratio = bar_
            sp = ob.spread_bps
            if sp is not None:
                spread_bps = sp
            ob_depth_bid = sum(lv.qty for lv in ob.bids)
            ob_depth_ask = sum(lv.qty for lv in ob.asks)
        elif tick.bid_price > 0 and tick.ask_price > 0:
            mid = (tick.bid_price + tick.ask_price) / 2
            if mid > 0:
                spread_bps = (tick.ask_price - tick.bid_price) / mid * 10_000

        # ─── 체결강도 ───
        strength = (
            sum(st.strength_buf) / len(st.strength_buf)
            if st.strength_buf
            else tick.strength or 100.0
        )

        # ─── ATR ───
        atr = atr_pct(list(st.hlc_1m), period=14)
        if math.isnan(atr):
            atr = 0.0

        # ─── 갭 ───
        gap_pct = 0.0
        if st.today_open > 0 and prev_c > 0:
            gap_pct = (st.today_open - prev_c) / prev_c * 100

        # ─── KOSPI 상대 수익률 ───
        kospi_ret_5m = _ret(self._kospi_closes_5m, 1) if len(self._kospi_closes_5m) >= 2 else 0.0

        return KStockFeatures(
            symbol=symbol,
            ts_ms=ts_ms,
            price=price,
            open_price=st.today_open or price,
            high_price=max(list(st.highs_1m) or [price]),
            low_price=min(list(st.lows_1m) or [price]),
            prev_close=prev_c,
            ma5_1m=ma5,
            ma20_1m=ma20,
            vwap_today=vwap,
            ret_1m=ret_1m,
            ret_5m=ret_5m,
            ret_15m=ret_15m,
            ret_1d=ret_1d,
            vol_ratio_5m=vol_ratio_5m,
            vol_ratio_20m=vol_ratio_20m,
            buy_ratio_5m=buy_ratio_5m,
            acml_vol_ratio=max(0.1, acml_vol_ratio),
            bid_ask_ratio=bid_ask_ratio,
            spread_bps=spread_bps,
            ob_depth_bid=ob_depth_bid,
            ob_depth_ask=ob_depth_ask,
            strength=strength,
            kospi_ret_5m=kospi_ret_5m,
            sector_ret_5m=0.0,   # Phase 2 확장 예정
            market_regime="neutral",  # Phase 2 확장 예정
            atr_pct=atr,
            gap_pct=gap_pct,
            is_halt=False,
            is_limit_up=False,
            is_limit_down=False,
            is_admin=False,
        )

    def symbols(self) -> list[str]:
        return list(self._states.keys())


# ─────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────

def _sma_int(buf: deque[int], period: int) -> float:
    if len(buf) < period:
        return float(buf[-1]) if buf else 0.0
    return sum(list(buf)[-period:]) / period


def _ret(buf: deque, n: int) -> float:
    """최근 n개 봉 기준 수익률 (%)."""
    lst = list(buf)
    if len(lst) < n + 1:
        return 0.0
    past = lst[-n - 1]
    curr = lst[-1]
    if past <= 0:
        return 0.0
    return (curr - past) / past * 100.0


def _vol_ratio(volumes: deque[int], n: int) -> float:
    """현재 거래량 / n봉 평균."""
    lst = list(volumes)
    if len(lst) < 2:
        return 1.0
    current = lst[-1]
    avg = sum(lst[-n - 1:-1]) / max(1, len(lst[-n - 1:-1]))
    return current / avg if avg > 0 else 1.0


def _buy_ratio(buy_counts: deque[int], trade_counts: deque[int], n: int) -> float:
    """최근 n봉 매수비율."""
    bc = list(buy_counts)[-n:]
    tc = list(trade_counts)[-n:]
    total_t = sum(tc)
    total_b = sum(bc)
    return total_b / total_t if total_t > 0 else 0.5


def _now_kst_minutes() -> int:
    """현재 KST 시간을 분 단위로 반환 (0 = 자정)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
    now = datetime.now(KST)
    return now.hour * 60 + now.minute
