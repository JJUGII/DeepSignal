"""FeatureEngine — realtime per-symbol numpy feature vectors."""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from deepsignal.market_data.binance_stream.models import (
    OhlcvBar,
    OrderBookSnapshot,
    TradeTick,
)
from deepsignal.market_data.binance_stream.parser import parse_depth_snapshot
from deepsignal.market_data.feature_engine.fear_greed import (
    default_cache_path,
    fear_greed_for_date,
    load_fear_greed_cache,
)
from deepsignal.market_data.feature_engine.math_utils import (
    atr_pct,
    bollinger_position,
    ema,
    forward_fill_vector,
    rsi,
    safe_return,
    sma,
    stddev,
    trend_sign,
    vwap_from_bars,
    TickReturnBuffer,
)
from deepsignal.market_data.feature_engine.orderbook_features import orderbook_features
from deepsignal.market_data.feature_engine.spec import FEATURE_COUNT, FEATURE_INDEX, FEATURE_NAMES


@dataclass
class SymbolFeatureState:
    closes_1m: deque[float] = field(default_factory=lambda: deque(maxlen=120))
    volumes_1m: deque[float] = field(default_factory=lambda: deque(maxlen=30))   # hlc_1m(30)과 정렬
    quote_vols_1m: deque[float] = field(default_factory=lambda: deque(maxlen=30))
    hlc_1m: deque[tuple[float, float, float]] = field(default_factory=lambda: deque(maxlen=30))
    closes_3m: deque[float] = field(default_factory=lambda: deque(maxlen=40))
    closes_15m: deque[float] = field(default_factory=lambda: deque(maxlen=20))
    tick_buf: TickReturnBuffer = field(default_factory=TickReturnBuffer)
    taker_buy_qty: float = 0.0
    total_qty: float = 0.0
    trade_window_ms: int = 60_000
    trade_events: deque[tuple[int, float, bool]] = field(
        default_factory=lambda: deque(maxlen=5000)
    )
    last_book: OrderBookSnapshot | None = None
    last_price: float = 0.0
    ob_1m_agg: dict[str, float] = field(default_factory=dict)
    funding_rate: float = float("nan")
    # CVD (Cumulative Volume Delta): 최근 5개 1m 바의 누적 매수-매도 델타
    cvd_bars: deque[float] = field(default_factory=lambda: deque(maxlen=5))
    # GSQS Phase-2: 봉 시가 (상승/하락봉 거래량 분리, 상단 꼬리 비율 계산용)
    opens_1m: deque[float] = field(default_factory=lambda: deque(maxlen=20))
    # GSQS Phase-2: 미결제약정 변화율 (REST 폴링으로 주입)
    oi_change_pct: float = float("nan")
    # GATS: 글로벌 롱/숏 비율 (REST 폴링으로 주입)
    long_short_ratio: float = float("nan")


@dataclass
class MarketFeatureState:
    btc_closes_1m: deque[float] = field(default_factory=lambda: deque(maxlen=120))
    fear_greed: float = float("nan")
    alt_quote_vol_1m: float = 0.0


class FeatureEngine:
    """Compute fixed-order numpy feature vectors per symbol with forward-fill."""

    def __init__(
        self,
        *,
        btc_symbol: str = "BTCUSDT",
        fear_greed_path: str | Path | None = None,
        output_dir: str | Path | None = None,
    ) -> None:
        self.btc_symbol = btc_symbol.upper()
        if fear_greed_path is not None:
            self.fear_greed_path = Path(fear_greed_path)
        elif output_dir is not None:
            self.fear_greed_path = default_cache_path(output_dir)
        else:
            self.fear_greed_path = default_cache_path()
        self._symbols: dict[str, SymbolFeatureState] = {}
        self._market = MarketFeatureState()
        self._last_vectors: dict[str, np.ndarray] = {}
        self._load_fear_greed()

    def _state(self, symbol: str) -> SymbolFeatureState:
        sym = symbol.upper()
        if sym not in self._symbols:
            self._symbols[sym] = SymbolFeatureState()
        return self._symbols[sym]

    def _load_fear_greed(self) -> None:
        cache = load_fear_greed_cache(self.fear_greed_path)
        from deepsignal.live_trading.time_utils import now_kst

        day = now_kst().date().isoformat()
        val = fear_greed_for_date(cache, day)
        if val is not None:
            self._market.fear_greed = float(val)
        elif cache and cache.get("value") is not None:
            try:
                self._market.fear_greed = float(cache["value"])
            except (TypeError, ValueError):
                pass

    def set_fear_greed(self, value: float | int | None) -> None:
        if value is None:
            self._market.fear_greed = float("nan")
        else:
            self._market.fear_greed = float(value)

    def on_trade(self, tick: TradeTick) -> None:
        st = self._state(tick.symbol)
        st.last_price = float(tick.price)
        st.tick_buf.on_price(float(tick.price))
        qty = float(tick.qty)
        st.trade_events.append((int(tick.ts_ms), qty, not tick.is_buyer_maker))
        self._prune_trade_window(st, int(tick.ts_ms))
        if tick.symbol.upper() == self.btc_symbol:
            self._market.btc_closes_1m.append(float(tick.price))

    def _prune_trade_window(self, st: SymbolFeatureState, now_ms: int) -> None:
        cutoff = now_ms - st.trade_window_ms
        taker = 0.0
        total = 0.0
        for ts, qty, is_taker_buy in st.trade_events:
            if ts < cutoff:
                continue
            total += qty
            if is_taker_buy:
                taker += qty
        st.taker_buy_qty = taker
        st.total_qty = total

    def on_orderbook(self, book: OrderBookSnapshot) -> None:
        self._state(book.symbol).last_book = book

    def on_bar(self, bar: OhlcvBar, *, is_historical: bool = False) -> None:
        """바 데이터 수신 처리.

        Args:
            bar:           닫힌 OHLCV 바 (closed=True 인 것만 처리).
            is_historical: True이면 파일에서 재생 중인 과거 바.
                           CVD 업데이트를 건너뛴다 — 히스토리 로딩 시
                           trade_events가 현재 창의 값이므로 오염 발생.
        """
        if not bar.closed:
            return
        st = self._state(bar.symbol)
        c = float(bar.close)
        if bar.timeframe == "1m":
            st.closes_1m.append(c)
            st.volumes_1m.append(float(bar.volume))
            st.quote_vols_1m.append(float(bar.quote_volume))
            st.hlc_1m.append((float(bar.high), float(bar.low), c))
            st.opens_1m.append(float(bar.open))   # GSQS: 상승/하락봉 판별용
            # CVD: 라이브 스트림에서만 업데이트 (히스토리 바는 현재 trade_events로
            # 오염되므로 건너뜀)
            if not is_historical and st.total_qty > 0:
                # 라이브 스트림: 현재 trade window로 CVD 계산
                bar_delta = (st.taker_buy_qty - (st.total_qty - st.taker_buy_qty))
                st.cvd_bars.append(bar_delta)
            elif is_historical and bar.taker_buy_ratio > 0 and bar.volume > 0:
                # 히스토리 바: 저장된 taker_buy_ratio로 정확한 CVD 복원
                # (taker_buy_ratio * 2 - 1) * volume → -volume ~ +volume 범위
                bar_delta = (bar.taker_buy_ratio * 2.0 - 1.0) * bar.volume
                st.cvd_bars.append(bar_delta)
            if bar.symbol.upper() != self.btc_symbol:
                self._market.alt_quote_vol_1m += float(bar.quote_volume)
        elif bar.timeframe == "3m":
            st.closes_3m.append(c)
        elif bar.timeframe == "15m":
            st.closes_15m.append(c)
        if bar.symbol.upper() == self.btc_symbol and bar.timeframe == "1m":
            self._market.btc_closes_1m.append(c)

    def ingest_live_state(self, payload: dict[str, Any]) -> None:
        """Bootstrap from binance_stream live_state.json snapshot."""
        fg = payload.get("fear_greed")
        if isinstance(fg, (int, float)):
            self.set_fear_greed(float(fg))

        funding = payload.get("funding") or {}
        if isinstance(funding, dict):
            for sym, raw in funding.items():
                if isinstance(raw, dict) and raw.get("funding_rate") is not None:
                    try:
                        self._state(str(sym)).funding_rate = float(raw["funding_rate"])
                    except (TypeError, ValueError):
                        pass

        # GSQS: OI 변화율 주입 (pipeline REST 폴링에서 수집)
        oi_data = payload.get("open_interest") or {}
        if isinstance(oi_data, dict):
            for sym, info in oi_data.items():
                if isinstance(info, dict):
                    try:
                        v = info.get("oi_change_pct")
                        if v is not None:
                            self._state(str(sym)).oi_change_pct = float(v)
                        v_ls = info.get("long_short_ratio")
                        if v_ls is not None:
                            self._state(str(sym)).long_short_ratio = float(v_ls)
                    except (TypeError, ValueError):
                        pass

        btc = payload.get("btc")
        if isinstance(btc, dict):
            tick = TradeTick(
                symbol=str(btc.get("symbol") or self.btc_symbol),
                price=float(btc.get("price") or 0),
                qty=float(btc.get("qty") or 0),
                ts_ms=int(btc.get("ts_ms") or 0),
                is_buyer_maker=bool(btc.get("is_buyer_maker")),
            )
            if tick.price > 0:
                self.on_trade(tick)

        books = payload.get("orderbooks") or {}
        if isinstance(books, dict):
            for sym, raw in books.items():
                if isinstance(raw, dict):
                    self.on_orderbook(parse_depth_snapshot(str(sym), raw))

        trades = payload.get("recent_trades") or {}
        if isinstance(trades, dict):
            for sym, rows in trades.items():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    try:
                        self.on_trade(
                            TradeTick(
                                symbol=str(sym),
                                price=float(row["price"]),
                                qty=float(row["qty"]),
                                ts_ms=int(row["ts_ms"]),
                                is_buyer_maker=bool(row.get("is_buyer_maker")),
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue

    def _btc_ret(self, minutes: int) -> float:
        closes = self._market.btc_closes_1m
        if len(closes) <= minutes:
            return float("nan")
        return safe_return(closes[-1], closes[-1 - minutes])

    def _compute_raw(self, symbol: str) -> np.ndarray:
        vec = np.full(FEATURE_COUNT, np.nan, dtype=np.float64)
        sym = symbol.upper()
        st = self._state(sym)
        idx = FEATURE_INDEX

        c1 = list(st.closes_1m)
        if len(c1) >= 2:
            vec[idx["ret_1m"]] = safe_return(c1[-1], c1[-2])
        if len(c1) >= 6:
            vec[idx["ret_5m"]] = safe_return(c1[-1], c1[-6])
        if len(c1) >= 61:
            vec[idx["ret_1h"]] = safe_return(c1[-1], c1[-61])

        c3 = list(st.closes_3m)
        if len(c3) >= 2:
            vec[idx["ret_3m"]] = safe_return(c3[-1], c3[-2])

        c15 = list(st.closes_15m)
        if len(c15) >= 2:
            vec[idx["ret_15m"]] = safe_return(c15[-1], c15[-2])

        btc_1m = self._btc_ret(1)
        if not math.isnan(vec[idx["ret_1m"]]) and not math.isnan(btc_1m):
            vec[idx["alpha_vs_btc_1m"]] = vec[idx["ret_1m"]] - btc_1m
        btc_15 = self._btc_ret(15)
        if not math.isnan(vec[idx["ret_15m"]]) and not math.isnan(btc_15):
            vec[idx["alpha_vs_btc_15m"]] = vec[idx["ret_15m"]] - btc_15

        s1 = trend_sign(c1) if len(c1) >= 3 else 0
        s3 = trend_sign(c3) if len(c3) >= 3 else 0
        s15 = trend_sign(c15) if len(c15) >= 3 else 0
        if s1 == s3 == s15 and s1 != 0:
            vec[idx["trend_align_1m_3m_15m"]] = 1.0
        elif s1 != 0 or s3 != 0 or s15 != 0:
            vec[idx["trend_align_1m_3m_15m"]] = 0.0

        vols = list(st.volumes_1m)
        if len(vols) >= 1:
            avg20 = sma(vols, min(20, len(vols)))
            if not math.isnan(avg20) and avg20 > 0:
                vec[idx["volume_ratio_20"]] = vols[-1] / avg20

        if st.total_qty > 0:
            vec[idx["taker_buy_ratio"]] = st.taker_buy_qty / st.total_qty

        qv = list(st.quote_vols_1m)
        if len(qv) >= 10:
            recent = sum(qv[-5:])
            prior = sum(qv[-10:-5])
            if prior > 0:
                vec[idx["quote_vol_spike_5m"]] = recent / prior

        ob_feats = orderbook_features(st.last_book)
        for k, v in ob_feats.items():
            if k in idx:
                vec[idx[k]] = v

        hlc = list(st.hlc_1m)
        if len(hlc) >= 15:
            vec[idx["atr_14_1m_pct"]] = atr_pct(hlc, 14)

        vec[idx["realized_vol_20tick"]] = st.tick_buf.realized_vol()

        if len(c1) >= 20:
            vec[idx["bb_position_20"]] = bollinger_position(c1, 20)

        btc_c = list(self._market.btc_closes_1m)
        if len(btc_c) >= 61:
            vec[idx["btc_trend_1h"]] = float(trend_sign(btc_c, lookback=60))

        alt_sum = self._market.alt_quote_vol_1m
        if alt_sum > 0:
            vec[idx["alt_quote_vol_sum_log"]] = math.log10(alt_sum)

        if not math.isnan(self._market.fear_greed):
            vec[idx["fear_greed_norm"]] = float(np.clip(self._market.fear_greed / 100.0, 0.0, 1.0))
            vec[idx["fear_greed_index"]] = float(self._market.fear_greed)

        self._fill_extended_features(vec, sym, st, idx, c1, c3, c15, vols, qv, hlc)

        return vec

    def _fill_extended_features(
        self,
        vec: np.ndarray,
        sym: str,
        st: SymbolFeatureState,
        idx: dict[str, int],
        c1: list[float],
        c3: list[float],
        c15: list[float],
        vols: list[float],
        qv: list[float],
        hlc: list[tuple[float, float, float]],
    ) -> None:
        if len(c3) >= 17:
            rsi_now = rsi(c3, 14)
            rsi_prev = rsi(c3[:-1], 14)
            if not math.isnan(rsi_now) and not math.isnan(rsi_prev):
                vec[idx["rsi_slope_3m"]] = rsi_now - rsi_prev

        if len(hlc) >= 3 and len(vols) >= len(hlc) and st.last_price > 0:
            bars_hlcv = [(h, l, c, vols[i]) for i, (h, l, c) in enumerate(hlc)]
            vwap = vwap_from_bars(bars_hlcv)
            if not math.isnan(vwap) and vwap > 0:
                vec[idx["vwap_deviation"]] = (st.last_price - vwap) / vwap

        if len(c1) >= 6:
            btc_5 = self._btc_ret(5)
            if not math.isnan(vec[idx["ret_5m"]]) and not math.isnan(btc_5):
                vec[idx["btc_relative_return_5m"]] = vec[idx["ret_5m"]] - btc_5

        if len(c1) >= 20:
            e1 = ema(c1, 12)
            e3 = ema(c3, 12) if len(c3) >= 12 else float("nan")
            if not math.isnan(e1) and not math.isnan(e3) and e3 != 0:
                vec[idx["ema_spread_1m_3m"]] = (e1 - e3) / e3

        if len(c1) >= 3:
            r_prev = safe_return(c1[-2], c1[-3])
            if not math.isnan(vec[idx["ret_1m"]]) and not math.isnan(r_prev):
                vec[idx["price_accel_1m"]] = vec[idx["ret_1m"]] - r_prev

        if hlc:
            h, l, c = hlc[-1]
            if h > l:
                vec[idx["high_low_position_1m"]] = (c - l) / (h - l)
                vec[idx["range_pct_1m"]] = (h - l) / c * 100.0 if c > 0 else float("nan")

        if len(c1) >= 6:
            vec[idx["momentum_sign_5m"]] = float(trend_sign(c1, lookback=5))

        def _vol_ratio(period: int) -> float:
            if len(vols) < period:
                return float("nan")
            avg = sma(vols, period)
            if math.isnan(avg) or avg <= 0:
                return float("nan")
            return vols[-1] / avg

        if len(vols) >= 2:
            avg1 = vols[-2]
            vec[idx["volume_ratio_1m"]] = vols[-1] / avg1 if avg1 > 0 else float("nan")
        vec[idx["volume_ratio_5m"]] = _vol_ratio(5)
        vec[idx["volume_ratio_15m"]] = _vol_ratio(15)

        if len(vols) >= 3:
            recent = vols[-1]
            prior = vols[-2]
            if prior > 0:
                vec[idx["volume_acceleration"]] = (recent - prior) / prior

        if st.trade_events:
            cutoff = int(st.trade_events[-1][0]) - 60_000
            sizes = [q for ts, q, _ in st.trade_events if ts >= cutoff]
            if sizes:
                threshold = sorted(sizes)[max(0, int(len(sizes) * 0.9) - 1)]
                vec[idx["large_trade_count_1m"]] = float(sum(1 for q in sizes if q >= threshold))

        if len(qv) >= 20:
            m = sma(qv, 20)
            sd = stddev(qv, 20)
            if not math.isnan(m) and not math.isnan(sd) and sd > 0:
                vec[idx["quote_vol_zscore_20"]] = (qv[-1] - m) / sd

        ob_ext = orderbook_features(st.last_book)
        for k in (
            "ob_imbalance_l1",
            "ob_imbalance_l5",
            "spread_bps",
            "bid_wall_distance",
            "ask_wall_distance",
            "ob_slope_bid",
            "bid_ask_depth_ratio",   # GSQS Phase-2
        ):
            if k in idx and k in ob_ext:
                vec[idx[k]] = ob_ext[k]

        agg = st.ob_1m_agg
        if agg:
            for k in ("ob_imbalance_1m_mean", "spread_1m_mean", "wall_bid_price_1m", "wall_ask_price_1m"):
                if k in idx and k in agg:
                    vec[idx[k]] = float(agg[k])

        total_alt = self._market.alt_quote_vol_1m
        sym_qv = qv[-1] if qv else 0.0
        if total_alt > 0 and sym_qv > 0:
            vec[idx["alt_total_volume_ratio"]] = sym_qv / total_alt

        if not math.isnan(st.funding_rate):
            vec[idx["funding_rate"]] = st.funding_rate

        # buy_sell_delta: 최근 5개 1m 바 CVD를 총 거래량 대비 정규화 (-1..+1)
        if "buy_sell_delta" in idx and st.cvd_bars:
            cvd_sum = sum(st.cvd_bars)
            # taker_buy_ratio 기반 총 거래량 추정으로 정규화
            total_est = sum(abs(d) for d in st.cvd_bars)
            if total_est > 0:
                vec[idx["buy_sell_delta"]] = max(-1.0, min(1.0, cvd_sum / total_est))

        if len(c1) >= 5:
            rets = [safe_return(c1[i], c1[i - 1]) for i in range(1, len(c1))]
            vol20 = stddev(rets[-20:], min(20, len(rets)))
            if not math.isnan(vol20):
                if vol20 > 0.02:
                    vec[idx["market_regime"]] = 2.0
                elif vol20 > 0.008:
                    vec[idx["market_regime"]] = 1.0
                else:
                    vec[idx["market_regime"]] = 0.0

        # ── GSQS Phase-2 신규 피처 ────────────────────────────────

        # ema_gap_1m: (EMA9 - EMA21) / close * 100  (양수 = 단기 상승 배열)
        if "ema_gap_1m" in idx and len(c1) >= 21:
            e9 = ema(c1, 9)
            e21 = ema(c1, 21)
            if not math.isnan(e9) and not math.isnan(e21) and st.last_price > 0:
                vec[idx["ema_gap_1m"]] = (e9 - e21) / st.last_price * 100.0

        # upper_wick_ratio: 상단 꼬리 / 전체 범위 (0~1)
        # 가짜 펌핑 감지: 큰 위꼬리 = 세력 물량 출회
        if "upper_wick_ratio" in idx and hlc and st.opens_1m:
            h, l, c = hlc[-1]
            o = float(st.opens_1m[-1]) if st.opens_1m else c
            range_total = h - l
            if range_total > 1e-10:
                upper_wick = h - max(o, c)
                vec[idx["upper_wick_ratio"]] = max(0.0, upper_wick / range_total)

        # up_vol_ratio_10: 최근 10봉 중 상승봉(close>open) 거래량 비율 (0~1)
        if "up_vol_ratio_10" in idx:
            opens = list(st.opens_1m)
            closes = list(st.closes_1m)
            _vols = list(st.volumes_1m)
            n10 = min(10, len(opens), len(closes), len(_vols))
            if n10 >= 3:
                up_vol = sum(
                    _vols[-n10 + i] for i in range(n10)
                    if closes[-n10 + i] > opens[-n10 + i]
                )
                total_v = sum(_vols[-n10:])
                if total_v > 0:
                    vec[idx["up_vol_ratio_10"]] = up_vol / total_v

        # btc_ret_5m: BTC 절대 5분 수익률 (시장 방향 판단)
        if "btc_ret_5m" in idx:
            btc5 = self._btc_ret(5)
            if not math.isnan(btc5):
                vec[idx["btc_ret_5m"]] = btc5

        # oi_change_pct: 미결제약정 변화율 (pipeline REST 폴링에서 주입)
        if "oi_change_pct" in idx and not math.isnan(st.oi_change_pct):
            vec[idx["oi_change_pct"]] = st.oi_change_pct

        # bid_ask_depth_ratio: 위 ob_ext 루프에서 이미 처리됨 (중복 계산 없음)

        # ── GATS 단타 적합 피처 ────────────────────────────────────────

        # long_short_ratio: REST 폴링 주입값 (pipeline._poll_one_futures에서 수집)
        if "long_short_ratio" in idx and not math.isnan(st.long_short_ratio):
            vec[idx["long_short_ratio"]] = st.long_short_ratio

        # breakout_score: (현재가 - 직전 20봉 최고가) / 최고가 * 100
        # 양수 = 신고가 돌파 중, 음수 = 고점 아래
        if "breakout_score" in idx and len(hlc) >= 5:
            prior_highs = [h for h, _l, _c in hlc[:-1]]
            max_high = max(prior_highs)
            if max_high > 0 and st.last_price > 0:
                vec[idx["breakout_score"]] = (st.last_price - max_high) / max_high * 100.0

        # trade_acceleration: (1분 체결건수 - 5분 평균 체결건수) / 5분 평균
        # 양수 = 체결 가속 중, 음수 = 체결 감속
        if "trade_acceleration" in idx and st.trade_events:
            now_ms = int(st.trade_events[-1][0])
            cutoff_1m = now_ms - 60_000
            cutoff_5m = now_ms - 300_000
            count_1m = sum(1 for ts, _, _ in st.trade_events if ts >= cutoff_1m)
            count_5m = sum(1 for ts, _, _ in st.trade_events if ts >= cutoff_5m)
            avg_per_min = count_5m / 5.0
            if avg_per_min > 0:
                vec[idx["trade_acceleration"]] = (count_1m - avg_per_min) / avg_per_min

        # whale_trade_ratio: 최대 단일 체결량 / 평균 체결량 (1분 이내)
        # 고래 출현 감지 — 단일 대형 주문이 평소보다 N배 크면 세력 움직임 신호
        if "whale_trade_ratio" in idx and st.trade_events:
            now_ms = int(st.trade_events[-1][0])
            cutoff_1m = now_ms - 60_000
            sizes_1m = [q for ts, q, _ in st.trade_events if ts >= cutoff_1m]
            if len(sizes_1m) >= 3:
                mean_size = sum(sizes_1m) / len(sizes_1m)
                max_size = max(sizes_1m)
                if mean_size > 0:
                    vec[idx["whale_trade_ratio"]] = max_size / mean_size

        # relative_strength_rank: compute_all()에서 cross-symbol 후처리로 채움

    def replay_at(
        self,
        symbol: str,
        ts_ms: int,
        *,
        stream_dir: str | Path = "outputs/binance_stream",
        forward_fill: bool = False,
    ) -> np.ndarray:
        """Compute features at ts_ms using only data strictly before ts_ms."""
        from deepsignal.market_data.feature_engine.replay import build_engine_at

        eng = build_engine_at(
            symbol,
            int(ts_ms),
            stream_dir=stream_dir,
            btc_symbol=self.btc_symbol,
            fear_greed_path=self.fear_greed_path,
        )
        return eng.compute(symbol.upper(), forward_fill=forward_fill)

    def compute(self, symbol: str, *, forward_fill: bool = True) -> np.ndarray:
        raw = self._compute_raw(symbol)
        if not forward_fill:
            return raw
        filled = forward_fill_vector(raw, self._last_vectors.get(symbol.upper()))
        self._last_vectors[symbol.upper()] = filled
        return filled

    def _fill_relative_strength_rank(self, vectors: dict[str, np.ndarray]) -> None:
        """심볼 간 5m 모멘텀 순위를 0~1로 정규화해 relative_strength_rank 벡터에 주입."""
        if "relative_strength_rank" not in FEATURE_INDEX:
            return
        rank_idx = FEATURE_INDEX["relative_strength_rank"]
        ret5_idx = FEATURE_INDEX.get("ret_5m")
        if ret5_idx is None:
            return
        pairs = [(sym, float(vec[ret5_idx])) for sym, vec in vectors.items()]
        valid = [(sym, r) for sym, r in pairs if not math.isnan(r)]
        if len(valid) < 2:
            return
        sorted_pairs = sorted(valid, key=lambda x: x[1])
        n = len(sorted_pairs)
        for rank, (sym, _) in enumerate(sorted_pairs):
            vectors[sym][rank_idx] = rank / (n - 1)

    def compute_all(self, symbols: list[str] | None = None) -> dict[str, np.ndarray]:
        syms = [s.upper() for s in (symbols or list(self._symbols.keys()))]
        result = {sym: self.compute(sym) for sym in syms}
        self._fill_relative_strength_rank(result)
        return result

    def feature_dict(self, symbol: str) -> dict[str, float]:
        arr = self.compute(symbol)
        return {name: float(arr[i]) for i, name in enumerate(FEATURE_NAMES)}

    def _load_historical_bars(self, bars_dir: Path, symbols: list[str], *, n_bars: int = 120) -> None:
        """bars/ jsonl에서 최근 n_bars개 바를 로드해 버퍼를 워밍업한다.

        CVD 오염을 방지하기 위해 is_historical=True로 on_bar()를 호출.
        returns/volume/ATR/EMA 등 bar-based 피처가 0이 되는 문제 해결.
        """
        from deepsignal.market_data.binance_stream.models import OhlcvBar

        for sym in symbols:
            for tf in ("1m", "3m", "15m"):
                f = bars_dir / f"{sym}_{tf}.jsonl"
                if not f.exists():
                    continue
                try:
                    lines = f.read_text(encoding="utf-8").strip().splitlines()
                except OSError:
                    continue
                for line in lines[-n_bars:]:
                    try:
                        row = json.loads(line)
                        bar = OhlcvBar(
                            symbol=str(row["symbol"]),
                            timeframe=str(row["timeframe"]),
                            open_ts_ms=int(row["open_ts_ms"]),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row["volume"]),
                            quote_volume=float(row["quote_volume"]),
                            trade_count=int(row.get("trade_count", 0)),
                            closed=True,
                        )
                        self.on_bar(bar, is_historical=True)
                    except (KeyError, ValueError, json.JSONDecodeError):
                        continue

    @staticmethod
    def from_live_state_path(
        path: str | Path,
        *,
        load_bars: bool = True,
        n_bars: int = 120,
        **kwargs: Any,
    ) -> dict[str, np.ndarray]:
        """live_state.json에서 피처 벡터를 계산한다.

        Args:
            path:      live_state.json 경로
            load_bars: True이면 인접한 bars/ 디렉토리에서 과거 OHLCV 바를
                       자동 로드해 bar-based 피처(returns, ATR, EMA 등)를 채운다.
            n_bars:    각 심볼·타임프레임별 최대 로드 바 수 (기본 120).
        """
        p = Path(path)
        payload = json.loads(p.read_text(encoding="utf-8"))
        engine = FeatureEngine(**kwargs)
        engine.ingest_live_state(payload)
        symbols = [str(s).upper() for s in payload.get("symbols") or []]
        if load_bars:
            bars_dir = p.parent / "bars"
            if bars_dir.is_dir():
                engine._load_historical_bars(bars_dir, symbols, n_bars=n_bars)
        return engine.compute_all(symbols)
