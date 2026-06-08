"""Binance WebSocket realtime pipeline orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from deepsignal.market_data.binance_stream.config import BinanceStreamConfig
from deepsignal.market_data.binance_stream.models import (
    FundingSnapshot,
    OhlcvBar,
    OrderBookSnapshot,
    TradeTick,
)
from deepsignal.market_data.binance_stream.ohlcv import OhlcvAggregator
from deepsignal.market_data.binance_stream.parser import (
    parse_combined_message,
    parse_depth_snapshot,
    parse_mark_price,
    parse_trade,
    stream_symbol,
)
from deepsignal.market_data.binance_stream.ob_history import OrderBookHistoryRecorder
from deepsignal.market_data.binance_stream.persistence import StreamPersistence
from deepsignal.market_data.binance_stream.symbols import resolve_stream_symbols

logger = logging.getLogger(__name__)


def build_spot_stream_names(symbols: list[str], *, depth_levels: int) -> list[str]:
    depth = max(5, min(20, int(depth_levels)))
    names: list[str] = []
    for sym in symbols:
        s = sym.lower()
        names.append(f"{s}@trade")
        names.append(f"{s}@depth{depth}@100ms")
    return names


def build_futures_stream_names(symbols: list[str]) -> list[str]:
    return [f"{sym.lower()}@markPrice@1s" for sym in symbols]


def combined_ws_url(base: str, streams: list[str]) -> str:
    joined = "/".join(streams)
    return f"{base.rstrip('/')}/stream?streams={joined}"


class BinanceRealtimePipeline:
    def __init__(self, cfg: BinanceStreamConfig) -> None:
        self.cfg = cfg
        self.symbols: list[str] = []
        self.aggregators: dict[str, OhlcvAggregator] = {}
        self.orderbooks: dict[str, OrderBookSnapshot] = {}
        self.funding: dict[str, FundingSnapshot] = {}
        # GSQS: OI (미결제약정) + Long/Short 비율
        self.open_interest: dict[str, float] = {}       # symbol -> current OI
        self.open_interest_prev: dict[str, float] = {}  # symbol -> previous OI
        self.open_interest_change: dict[str, float] = {}  # symbol -> OI change %
        self.long_short_ratio: dict[str, float] = {}    # symbol -> global L/S ratio
        self.btc_tick: TradeTick | None = None
        self.persistence = StreamPersistence(
            output_dir=Path(self.cfg.output_dir),
            max_recent_trades=self.cfg.max_trades_buffered,
        )
        self.stats: dict[str, Any] = {
            "trades": 0,
            "depth_updates": 0,
            "funding_updates": 0,
            "oi_updates": 0,
            "bars_closed": 0,
            "messages": 0,
            "errors": 0,
        }
        self._last_flush = 0.0
        self._feature_engine: Any = None   # REST 워밍업 후 초기화
        self._ob_recorder: OrderBookHistoryRecorder | None = None
        self._signal_notifier: Any = None  # ScalpSignalNotifier (지연 초기화)
        self._corr_tracker: Any = None     # CorrelationTracker (지연 초기화)
        self._macro_guard: Any = None      # MacroGuard (지연 초기화)
        self._state_mgr: Any = None        # StreamStateManager (지연 초기화)

    def prepare(self) -> list[str]:
        self.symbols = resolve_stream_symbols(self.cfg)
        self.aggregators = {
            sym: OhlcvAggregator(sym, timeframes_minutes=self.cfg.timeframes_minutes)
            for sym in self.symbols
        }
        bars_dir = Path(self.cfg.output_dir) / "bars"
        self._ob_recorder = OrderBookHistoryRecorder(
            bars_dir,
            interval_seconds=float(self.cfg.ob_snapshot_seconds),
        )
        return self.symbols

    async def _warmup_symbol_rest(self, session: Any, sym: str) -> None:
        """심볼 하나의 최근 1m/3m/15m 봉을 REST로 당겨 FeatureEngine 워밍업."""
        from deepsignal.market_data.feature_engine.engine import FeatureEngine

        if self._feature_engine is None:
            self._feature_engine = FeatureEngine(
                btc_symbol=self.cfg.btc_symbol,
                output_dir=Path(self.cfg.output_dir).parent,
            )

        for tf_min, limit in [(1, 120), (3, 40), (15, 20)]:
            try:
                async with session.get(
                    "https://api.binance.com/api/v3/klines",
                    params={"symbol": sym, "interval": f"{tf_min}m", "limit": limit},
                ) as resp:
                    if resp.status != 200:
                        continue
                    rows = await resp.json()
                    # 마지막 봉은 아직 열려있으므로 제외
                    for row in rows[:-1]:
                        vol = float(row[5])
                        tbv = float(row[9])   # taker buy base asset volume
                        bar = OhlcvBar(
                            symbol=sym,
                            timeframe=f"{tf_min}m",
                            open_ts_ms=int(row[0]),
                            open=float(row[1]),
                            high=float(row[2]),
                            low=float(row[3]),
                            close=float(row[4]),
                            volume=vol,
                            quote_volume=float(row[7]),
                            trade_count=int(row[8]),
                            taker_buy_ratio=tbv / vol if vol > 0 else 0.0,
                            closed=True,
                        )
                        self._feature_engine.on_bar(bar, is_historical=True)
            except Exception as exc:
                logger.debug("REST warmup [%s %dm]: %s", sym, tf_min, exc)

    async def _warmup_from_rest(self) -> None:
        """워밍업 진입점.

        1) bars/*.jsonl + state_snapshot.json 으로 즉시 복원 시도
        2) 복원 성공 → 누락 봉만 delta REST fetch
        3) 복원 실패 → 기존 풀 REST 워밍업 (폴백)
        """
        from deepsignal.market_data.feature_engine.engine import FeatureEngine
        from deepsignal.market_data.binance_stream.state_persistence import StreamStateManager

        output_dir = Path(self.cfg.output_dir).parent

        if self._feature_engine is None:
            self._feature_engine = FeatureEngine(
                btc_symbol=self.cfg.btc_symbol,
                output_dir=output_dir,
            )

        if self._state_mgr is None:
            self._state_mgr = StreamStateManager()

        # ── 1. 상태 복원 시도 ───────────────────────────────────
        result = self._state_mgr.load_and_warmup(
            output_dir=output_dir / "binance_stream",
            eng=self._feature_engine,
            corr_tracker=self._corr_tracker,
            symbols=list(self.symbols),
        )

        if result.success:
            # ── 2. delta REST fetch (누락 봉만) ─────────────────
            await self._delta_fetch(result)
            n = sum(
                len(list(self._feature_engine._state(s).closes_1m))
                for s in self.symbols
            )
            logger.info(
                "✅ 상태 복원 워밍업 완료 — 총 1m 봉 버퍼: %d (delta: %d심볼 REST)",
                n, sum(1 for v in result.delta_needed.values() if any(v.values())),
            )
            # 복원 못한 심볼은 풀 REST
            if result.symbols_failed:
                logger.info(
                    "미복원 심볼 %d개 → REST 풀 fetch: %s",
                    len(result.symbols_failed), result.symbols_failed[:5],
                )
                await self._full_rest_warmup(symbols=result.symbols_failed)
            return

        # ── 3. 폴백: 풀 REST 워밍업 ────────────────────────────
        await self._full_rest_warmup()

    async def _full_rest_warmup(
        self,
        symbols: list[str] | None = None,
    ) -> None:
        """지정 심볼 전체를 REST로 워밍업 (기존 방식)."""
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp 없음 — REST 워밍업 스킵 (pip install aiohttp)")
            return

        targets = symbols if symbols is not None else self.symbols
        logger.info("REST 풀 워밍업 시작: %d 심볼...", len(targets))
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            await asyncio.gather(
                *[self._warmup_symbol_rest(session, sym) for sym in targets],
                return_exceptions=True,
            )
        n = sum(len(list(self._feature_engine._state(s).closes_1m))
                for s in self.symbols) if self._feature_engine else 0
        logger.info("REST 워밍업 완료 — 총 1m 봉 버퍼: %d", n)

    async def _delta_fetch(self, result: Any) -> None:
        """복원 후 누락된 봉만 REST로 추가 수집."""
        try:
            import aiohttp
        except ImportError:
            return

        # delta가 있는 심볼만 추림
        targets = {
            sym: deltas
            for sym, deltas in result.delta_needed.items()
            if any(deltas.values())
        }
        if not targets:
            return

        logger.info("delta fetch 시작: %d심볼", len(targets))
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20)
        ) as session:
            await asyncio.gather(
                *[
                    self._delta_fetch_symbol(session, sym, deltas)
                    for sym, deltas in targets.items()
                ],
                return_exceptions=True,
            )

    async def _delta_fetch_symbol(
        self,
        session: Any,
        sym: str,
        deltas: dict[int, int],
    ) -> None:
        """심볼 하나의 누락 봉을 timeframe 별로 fetch."""
        for tf_min, count in deltas.items():
            if count <= 0:
                continue
            try:
                async with session.get(
                    "https://api.binance.com/api/v3/klines",
                    params={"symbol": sym, "interval": f"{tf_min}m", "limit": count + 1},
                ) as resp:
                    if resp.status != 200:
                        continue
                    rows = await resp.json()
                    for row in rows[:-1]:  # 마지막은 아직 열린 봉
                        vol = float(row[5])
                        tbv = float(row[9])
                        from deepsignal.market_data.binance_stream.models import OhlcvBar
                        bar = OhlcvBar(
                            symbol=sym,
                            timeframe=f"{tf_min}m",
                            open_ts_ms=int(row[0]),
                            open=float(row[1]),
                            high=float(row[2]),
                            low=float(row[3]),
                            close=float(row[4]),
                            volume=vol,
                            quote_volume=float(row[7]),
                            trade_count=int(row[8]),
                            taker_buy_ratio=tbv / vol if vol > 0 else 0.0,
                            closed=True,
                        )
                        self._feature_engine.on_bar(bar, is_historical=True)
            except Exception as exc:
                logger.debug("delta fetch [%s %dm]: %s", sym, tf_min, exc)

    def handle_payload(self, raw: dict[str, Any]) -> None:
        self.stats["messages"] = int(self.stats.get("messages", 0)) + 1
        stream, data = parse_combined_message(raw)
        if not stream and "e" in data:
            stream = str(data.get("e") or "")

        if "markPrice" in stream or str(data.get("e") or "") == "markPriceUpdate":
            snap = parse_mark_price(data)
            if snap and snap.symbol:
                self.funding[snap.symbol] = snap
                self.stats["funding_updates"] = int(self.stats.get("funding_updates", 0)) + 1
            return

        sym = stream_symbol(stream) if stream else str(data.get("s") or "").upper()
        if not sym:
            return

        if "@trade" in stream or str(data.get("e") or "") == "trade":
            tick = parse_trade(data)
            if tick is None or tick.price <= 0:
                return
            self.stats["trades"] = int(self.stats.get("trades", 0)) + 1
            self.persistence.record_trade(tick)
            if sym == self.cfg.btc_symbol.upper():
                self.btc_tick = tick
            agg = self.aggregators.get(sym)
            if agg:
                for bar in agg.on_trade(tick):
                    self.persistence.append_closed_bar(bar)
                    self.stats["bars_closed"] = int(self.stats.get("bars_closed", 0)) + 1
                    # FeatureEngine에도 실시간 바 전달 (CVD 포함)
                    if self._feature_engine is not None:
                        self._feature_engine.on_bar(bar)
            return

        if "depth" in stream or "bids" in data or "asks" in data:
            book = parse_depth_snapshot(sym, data, ts_ms=int(data.get("E") or time.time() * 1000))
            self.orderbooks[sym] = book
            self.stats["depth_updates"] = int(self.stats.get("depth_updates", 0)) + 1
            if self._ob_recorder is not None:
                self._ob_recorder.maybe_record(book)

    def _write_feature_snapshot(self) -> None:
        try:
            from deepsignal.market_data.feature_engine.engine import FeatureEngine

            try:
                from deepsignal.market_data.feature_engine.fear_greed import update_fear_greed_cache

                update_fear_greed_cache(
                    Path(self.cfg.output_dir).parent / "fear_greed_cache.json",
                    force=False,
                )
            except Exception:
                pass
            eng = FeatureEngine(
                btc_symbol=self.cfg.btc_symbol,
                output_dir=Path(self.cfg.output_dir).parent,
            )
            if self.btc_tick is not None:
                eng.on_trade(self.btc_tick)
            for sym, book in self.orderbooks.items():
                eng.on_orderbook(book)
            for sym, agg in self.aggregators.items():
                for bar in agg.snapshot_open_bars():
                    eng.on_bar(bar)
            vectors = eng.compute_all(self.symbols)
            out = {
                sym: vec.tolist() for sym, vec in vectors.items()
            }
            path = Path(self.cfg.output_dir) / "feature_vectors.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            meta = {
                "feature_names": list(
                    __import__(
                        "deepsignal.market_data.feature_engine.spec",
                        fromlist=["FEATURE_NAMES"],
                    ).FEATURE_NAMES
                ),
                "vectors": out,
            }
            path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("feature snapshot skip: %s", exc)

    def maybe_flush_state(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_flush) < float(self.cfg.state_flush_seconds):
            return
        open_bars: list = []
        for agg in self.aggregators.values():
            open_bars.extend(agg.snapshot_open_bars())
        # GSQS: OI + L/S 데이터를 live_state에 포함
        oi_payload = {
            sym: {
                "oi": self.open_interest.get(sym, 0),
                "oi_change_pct": self.open_interest_change.get(sym, None),
                "long_short_ratio": self.long_short_ratio.get(sym, None),
            }
            for sym in self.symbols
        }
        self.persistence.write_live_state(
            symbols=self.symbols,
            orderbooks=self.orderbooks,
            funding=self.funding,
            open_interest=oi_payload,
            open_bars=open_bars,
            btc=self.btc_tick,
            stats=dict(self.stats),
        )
        self._last_flush = now

        # ── 신호 기록 + 사후평가 + 자동 최적화 트리거 ──────────────
        self._score_and_log()

    def _score_and_log(self) -> None:
        """FeatureEngine으로 전 심볼 채점 → BUY 신호 기록 → 사후 결과 업데이트."""
        try:
            from deepsignal.market_data.feature_engine.engine import FeatureEngine
            from deepsignal.market_data.feature_engine.spec import FEATURE_NAMES
            from deepsignal.crypto_trading.signal.scalping_scorer import compute_scalping_score
            from deepsignal.crypto_trading.signal.signal_logger import SignalLogger
            from deepsignal.crypto_trading.signal.signal_notifier import ScalpSignalNotifier
            from deepsignal.crypto_trading.signal.weight_optimizer import WeightOptimizer

            # FeatureEngine 공유 인스턴스 사용 (워밍업된 상태 유지)
            eng = self._feature_engine
            if eng is None:
                eng = FeatureEngine(
                    btc_symbol=self.cfg.btc_symbol,
                    output_dir=Path(self.cfg.output_dir).parent,
                )
                self._feature_engine = eng

            # 라이브 데이터 주입
            if self.btc_tick is not None:
                eng.on_trade(self.btc_tick)
            for sym, book in self.orderbooks.items():
                eng.on_orderbook(book)

            # OI/L·S 데이터 주입 (ingest_live_state 경유)
            oi_state = {
                sym: {
                    "oi_change_pct": self.open_interest_change.get(sym),
                    "long_short_ratio": self.long_short_ratio.get(sym),
                }
                for sym in self.symbols
            }
            for sym, info in oi_state.items():
                st = eng._state(sym)
                if info.get("oi_change_pct") is not None:
                    import math as _math
                    try:
                        st.oi_change_pct = float(info["oi_change_pct"])
                    except (TypeError, ValueError):
                        pass
                if info.get("long_short_ratio") is not None:
                    try:
                        st.long_short_ratio = float(info["long_short_ratio"])
                    except (TypeError, ValueError):
                        pass

            # funding rate 주입
            for sym, snap in self.funding.items():
                try:
                    eng._state(sym).funding_rate = float(snap.funding_rate)
                except (TypeError, ValueError, AttributeError):
                    pass

            vectors = eng.compute_all(self.symbols)

            # feature_vectors.json 주기적 업데이트 (30초마다)
            # — _write_feature_snapshot()은 종료 시에만 호출되므로
            #   여기서 직접 주기적으로 씀 → 웹UI 신호분석 GSQS 스코어보드 갱신
            _now_mono = time.monotonic()
            if not hasattr(self, "_last_fv_write") or (_now_mono - self._last_fv_write) >= 30.0:
                try:
                    _fv_path = Path(self.cfg.output_dir) / "feature_vectors.json"
                    _fv_path.parent.mkdir(parents=True, exist_ok=True)
                    _fv_meta = {
                        "feature_names": list(FEATURE_NAMES),
                        "vectors": {sym: vec.tolist() for sym, vec in vectors.items()},
                        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                    }
                    _fv_path.write_text(
                        json.dumps(_fv_meta, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    self._last_fv_write = _now_mono
                except Exception:
                    pass

            # SignalLogger 초기화 (최초 1회)
            if not hasattr(self, "_signal_logger"):
                self._signal_logger = SignalLogger(Path(self.cfg.output_dir).parent)
            sig_logger: SignalLogger = self._signal_logger  # type: ignore[attr-defined]

            # ScalpSignalNotifier 초기화 (최초 1회)
            if self._signal_notifier is None:
                self._signal_notifier = ScalpSignalNotifier()
            notifier: ScalpSignalNotifier = self._signal_notifier

            # MacroGuard / CorrelationTracker 초기화 (최초 1회)
            if self._corr_tracker is None:
                from deepsignal.crypto_trading.macro import CorrelationTracker, MacroGuard
                self._corr_tracker = CorrelationTracker()
                self._macro_guard = MacroGuard()
                self._macro_guard.set_alert_callback(notifier.notify_macro_event)

            # 현재 가격 수집 (사후 평가용)
            current_prices: dict[str, float] = {}
            now_ms = int(time.time() * 1000)

            for sym in self.symbols:
                feats = {name: float(vectors[sym][i]) for i, name in enumerate(FEATURE_NAMES)}

                # 현재가 수집
                price = eng._state(sym).last_price
                if price > 0:
                    current_prices[sym] = price

                # CorrelationTracker 업데이트 (종가 기록)
                if price > 0:
                    self._corr_tracker.update(sym, price, now_ms)

                # GSQS 채점
                score = compute_scalping_score(sym, feats)

                # BUY_CANDIDATE 이상 신호만 기록 + Telegram 알림
                if score.is_buy and price > 0:
                    macro_active = self._macro_guard.active
                    sig_logger.log_signal(
                        score, price=price, ts_ms=now_ms, macro_risk=macro_active
                    )
                    if macro_active:
                        notifier.notify_macro_warning(
                            score, price=price,
                            reason=self._macro_guard.trigger_reason,
                        )
                    else:
                        notifier.notify(score, price=price)

            # 매크로 이벤트 평가 (전 심볼 업데이트 후)
            self._macro_guard.evaluate(self._corr_tracker)

            # 사후 결과 업데이트 (pending 신호들의 경과 시간 확인)
            completed = sig_logger.check_outcomes(current_prices, now_ms=now_ms)
            if completed > 0:
                logger.info("신호 결과 기록 완료: %d건", completed)

            # 가중치 자동 최적화 (데이터 충분 시)
            if not hasattr(self, "_weight_optimizer"):
                self._weight_optimizer = WeightOptimizer(
                    Path(self.cfg.output_dir).parent,
                    horizon_minutes=5,
                )
                # 재시작 시 저장된 최적화 가중치 복원
                try:
                    from deepsignal.crypto_trading.signal import scalping_scorer as _sc
                    _saved_w = self._weight_optimizer.load_weights()  # type: ignore[attr-defined]
                    _sc._WEIGHTS.update(_saved_w)
                    logger.debug("저장된 GSQS 가중치 복원 완료: %s", _saved_w)
                except Exception as _we:
                    logger.debug("가중치 복원 실패 (무시): %s", _we)

            opt: WeightOptimizer = self._weight_optimizer  # type: ignore[attr-defined]
            if opt.should_run():
                logger.info("GSQS 가중치 최적화 실행 중...")
                opt_result = opt.run()
                if "error" not in opt_result:
                    logger.info(
                        "가중치 최적화 완료: 승률 %.1f%% → %.1f%% (+%.1f%%) applied=%s",
                        opt_result["default_win_rate"] * 100,
                        opt_result["expected_win_rate"] * 100,
                        opt_result["improvement"] * 100,
                        opt_result.get("applied", True),
                    )
                    # 개선이 있을 때만 Telegram 알림
                    if opt_result.get("applied") and self._signal_notifier is not None:
                        try:
                            from deepsignal.crypto_trading.signal.weight_optimizer import DEFAULT_WEIGHTS
                            self._signal_notifier.notify_weight_update(
                                new_weights=opt_result["weights"],
                                old_weights=opt_result.get("default_weights", DEFAULT_WEIGHTS),
                                improvement=opt_result["improvement"],
                                n_samples=opt_result["n_samples"],
                            )
                        except Exception as _ne:
                            logger.debug("가중치 알림 전송 실패 (무시): %s", _ne)

        except Exception as exc:
            logger.debug("score_and_log skip: %s", exc)

    async def _consume_ws(
        self,
        url: str,
        *,
        label: str,
        stop: asyncio.Event,
    ) -> None:
        import websockets

        backoff = 1.0
        while not stop.is_set():
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=60,
                    max_size=8 * 1024 * 1024,
                ) as ws:
                    logger.info("%s connected (%d streams in URL)", label, url.count("/") + 1)
                    backoff = 1.0
                    async for message in ws:
                        if stop.is_set():
                            break
                        try:
                            payload = json.loads(message)
                        except json.JSONDecodeError:
                            self.stats["errors"] = int(self.stats.get("errors", 0)) + 1
                            continue
                        if isinstance(payload, dict):
                            self.handle_payload(payload)
                        self.maybe_flush_state()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stats["errors"] = int(self.stats.get("errors", 0)) + 1
                logger.warning("%s disconnected: %s — retry in %.1fs", label, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)

    async def _poll_one_futures(
        self,
        session: Any,
        sym: str,
    ) -> None:
        """심볼 하나의 funding/OI/L·S를 한 번에 폴링."""
        # ① Funding rate (mark price 포함)
        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": sym},
            ) as resp:
                if resp.status == 200:
                    d = await resp.json()
                    snap = FundingSnapshot(
                        symbol=str(d.get("symbol") or "").upper(),
                        mark_price=float(d.get("markPrice") or 0),
                        funding_rate=float(d.get("lastFundingRate") or 0),
                        next_funding_ts_ms=int(d.get("nextFundingTime") or 0),
                        ts_ms=int(d.get("time") or 0),
                    )
                    if snap.symbol:
                        self.funding[snap.symbol] = snap
                        self.stats["funding_updates"] = (
                            int(self.stats.get("funding_updates", 0)) + 1
                        )
        except Exception as exc:
            logger.debug("funding REST [%s]: %s", sym, exc)

        # ② Open Interest (미결제약정)
        try:
            async with session.get(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": sym},
            ) as resp:
                if resp.status == 200:
                    d = await resp.json()
                    oi = float(d.get("openInterest") or 0)
                    if oi > 0:
                        prev = self.open_interest.get(sym, oi)
                        if prev > 0:
                            chg = (oi - prev) / prev * 100.0
                            self.open_interest_change[sym] = chg
                        self.open_interest_prev[sym] = prev
                        self.open_interest[sym] = oi
                        self.stats["oi_updates"] = (
                            int(self.stats.get("oi_updates", 0)) + 1
                        )
        except Exception as exc:
            logger.debug("OI REST [%s]: %s", sym, exc)

        # ③ Global Long/Short Ratio
        try:
            async with session.get(
                "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                params={"symbol": sym, "period": "5m", "limit": "1"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and isinstance(data, list):
                        ls = float(data[0].get("longShortRatio") or 1.0)
                        self.long_short_ratio[sym] = ls
        except Exception as exc:
            logger.debug("L/S REST [%s]: %s", sym, exc)

    async def _poll_funding_rest(self, stop: asyncio.Event) -> None:
        """fapi.binance.com REST로 60초마다 선물 데이터 폴링.

        수집 데이터: funding rate / open interest / long·short ratio
        fstream WebSocket이 지역 차단 등으로 불통일 때 백업 역할도 수행.
        """
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp 없음 — 선물 REST 폴링 비활성화")
            return

        interval = 60  # seconds
        first_run = True
        while not stop.is_set():
            if not first_run:
                for _ in range(interval):
                    if stop.is_set():
                        return
                    await asyncio.sleep(1.0)
            first_run = False

            if stop.is_set():
                return

            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as session:
                    # 전체 심볼을 동시에 폴링 (asyncio.gather)
                    await asyncio.gather(
                        *[self._poll_one_futures(session, sym) for sym in list(self.symbols)],
                        return_exceptions=True,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("futures REST session error: %s", exc)

    async def run_async(self, *, duration_seconds: float = 0.0) -> dict[str, Any]:
        self.persistence = StreamPersistence(
            Path(self.cfg.output_dir),
            max_recent_trades=self.cfg.max_trades_buffered,
        )
        self.prepare()
        await self._warmup_from_rest()
        stop = asyncio.Event()
        tasks: list[asyncio.Task[None]] = []

        spot_streams = build_spot_stream_names(self.symbols, depth_levels=self.cfg.depth_levels)
        spot_url = combined_ws_url(self.cfg.spot_ws_base, spot_streams)
        tasks.append(asyncio.create_task(self._consume_ws(spot_url, label="spot", stop=stop)))

        if self.cfg.include_funding:
            fut_streams = build_futures_stream_names(self.symbols)
            fut_url = combined_ws_url(self.cfg.futures_ws_base, fut_streams)
            tasks.append(asyncio.create_task(self._consume_ws(fut_url, label="futures", stop=stop)))
            # REST 폴링 백업: fstream WS가 지역 차단 등으로 불통일 때도 funding rate 갱신
            tasks.append(asyncio.create_task(self._poll_funding_rest(stop)))

        try:
            if duration_seconds > 0:
                await asyncio.sleep(duration_seconds)
                stop.set()
            else:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            stop.set()
            raise
        finally:
            stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.maybe_flush_state(force=True)
            # 상태 스냅샷 저장 (재시작 시 0초 워밍업용)
            if self._feature_engine is not None:
                try:
                    from deepsignal.market_data.binance_stream.state_persistence import (
                        StreamStateManager,
                    )
                    if self._state_mgr is None:
                        self._state_mgr = StreamStateManager()
                    self._state_mgr.save_snapshot(
                        output_dir=Path(self.cfg.output_dir),
                        eng=self._feature_engine,
                        corr_tracker=self._corr_tracker,
                    )
                except Exception as _se:
                    logger.debug("상태 스냅샷 저장 실패 (무시): %s", _se)
            if self._signal_notifier is not None:
                self._signal_notifier.shutdown()
        self._write_feature_snapshot()

        stats = dict(self.stats)
        if self._ob_recorder is not None:
            stats["ob_snapshots_written"] = self._ob_recorder.snapshots_written
        return {
            "symbols": self.symbols,
            "output_dir": str(self.cfg.output_dir),
            "stats": stats,
        }


async def run_binance_stream_async(
    cfg: BinanceStreamConfig,
    *,
    duration_seconds: float = 0.0,
) -> dict[str, Any]:
    pipeline = BinanceRealtimePipeline(cfg)
    return await pipeline.run_async(duration_seconds=duration_seconds)


def run_binance_stream(
    cfg: BinanceStreamConfig,
    *,
    duration_seconds: float = 0.0,
) -> dict[str, Any]:
    return asyncio.run(run_binance_stream_async(cfg, duration_seconds=duration_seconds))
