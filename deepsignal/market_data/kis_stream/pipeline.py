"""KIS 실시간 스트림 파이프라인 오케스트레이터.

흐름:
  1. REST 워밍업 — pykrx/yfinance로 최근 봉 데이터 로드 → 피처엔진 초기화
  2. KIS Approval Key 취득
  3. WebSocket 연결 → H0STCNT0(체결) + H0STASP0(호가) 구독
  4. 틱 → OhlcvAggregator → 봉 생성 → 피처엔진 → K-GSQS 채점
  5. 장 운영시간(9:05–15:15 KST) 외에는 대기
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from deepsignal.market_data.kis_stream.aggregator import KisOhlcvAggregator
from deepsignal.market_data.kis_stream.config import KisStreamConfig
from deepsignal.market_data.kis_stream.models import (
    KisOhlcvBar,
    KisOrderBookSnapshot,
    KisTradeTick,
)
from deepsignal.market_data.kis_stream.parser import parse_message
from deepsignal.market_data.kis_stream.persistence import KisStreamPersistence
from deepsignal.market_data.kis_stream.ws_client import KisWebSocketClient, get_approval_key

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# 장 운영 시간 (KST)
_MARKET_OPEN = (9, 5)
_MARKET_CLOSE = (15, 15)


def is_market_hours(now_kst: datetime | None = None) -> bool:
    """현재 시각이 장 운영시간(09:05–15:15 KST) 내인지 확인."""
    if now_kst is None:
        now_kst = datetime.now(KST)
    t = (now_kst.hour, now_kst.minute)
    return _MARKET_OPEN <= t <= _MARKET_CLOSE


def seconds_to_market_open() -> float:
    """장 시작까지 남은 초. 이미 장 중이면 0 반환."""
    now = datetime.now(KST)
    if is_market_hours(now):
        return 0.0
    # 당일 09:05
    open_today = now.replace(
        hour=_MARKET_OPEN[0], minute=_MARKET_OPEN[1], second=0, microsecond=0
    )
    if now < open_today:
        return (open_today - now).total_seconds()
    # 이미 장 마감 → 내일 09:05 (간략 처리: +24h)
    from datetime import timedelta
    open_tomorrow = open_today + timedelta(days=1)
    return (open_tomorrow - now).total_seconds()


class KisRealtimePipeline:
    """KIS 국내주식 실시간 데이터 파이프라인."""

    def __init__(
        self,
        cfg: KisStreamConfig,
        app_key: str,
        app_secret: str,
        rest_base_url: str,
    ) -> None:
        self.cfg = cfg
        self.app_key = app_key
        self.app_secret = app_secret
        self.rest_base_url = rest_base_url

        self.symbols: list[str] = list(cfg.symbols)
        self.aggregators: dict[str, KisOhlcvAggregator] = {}
        self.orderbooks: dict[str, KisOrderBookSnapshot] = {}

        output_dir = cfg.resolved_output_dir
        self.persistence = KisStreamPersistence(
            output_dir=output_dir,
            max_recent_trades=cfg.max_trades_buffered,
        )

        self.stats: dict[str, Any] = {
            "trades": 0,
            "orderbooks": 0,
            "bars_closed": 0,
            "messages": 0,
            "errors": 0,
            "start_time": None,
        }

        from deepsignal.market_data.kis_stream.feature_engine import StockFeatureEngine
        from deepsignal.market_data.kis_stream.signal_bridge import KStockSignalBridge
        self._feature_engine: Any = StockFeatureEngine()
        # db_path: K-GSQS 고점수 신호를 AI 추천 엔진용 signals DB에도 기록
        from deepsignal.config.settings import load_settings as _load_settings
        _db_path: str | None = None
        try:
            _db_path = _load_settings().db_path
        except Exception:
            pass
        self._signal_bridge: Any = KStockSignalBridge(
            output_dir=cfg.resolved_output_dir,
            enable_telegram=True,
            db_path=_db_path,
        )
        self._ws_client: KisWebSocketClient | None = None
        self._stop_event = asyncio.Event()
        self._warmed_up: bool = False
        # 유니버스 갱신 지원
        self._session_stop = asyncio.Event()         # WS 세션 중단 신호 (갱신·마감 공용)
        self._refresh_universe_pending: list[str] | None = None  # None = 장마감, list = 갱신 재시작

    # ─────────────────────────────────────────────────────
    # 공개 API
    # ─────────────────────────────────────────────────────

    def prepare(self) -> list[str]:
        """어그리게이터 초기화."""
        self.aggregators = {
            sym: KisOhlcvAggregator(sym, timeframes_minutes=self.cfg.timeframes_minutes)
            for sym in self.symbols
        }
        logger.info("KIS 파이프라인 준비: %d 심볼", len(self.symbols))
        return self.symbols

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws_client:
            self._ws_client.stop()

    async def run(self) -> None:
        """파이프라인 메인 루프 (장 시간 대기 + WebSocket)."""
        self.prepare()
        self.stats["start_time"] = time.time()

        while not self._stop_event.is_set():
            # 장 운영시간 확인
            wait_s = seconds_to_market_open()
            if wait_s > 0:
                logger.info(
                    "장 시작까지 %.0f분 대기 (현재: %s KST)",
                    wait_s / 60,
                    datetime.now(KST).strftime("%H:%M:%S"),
                )
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._stop_event.wait()),
                        timeout=min(wait_s, 300),
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            # REST 워밍업 — 매 장 시작 전 1회 (피처엔진 prev_close 초기화)
            if not self._warmed_up:
                await self._warmup_from_rest()
                self._warmed_up = True

            # Approval Key 취득
            try:
                approval_key = await get_approval_key(
                    self.rest_base_url,
                    self.app_key,
                    self.app_secret,
                )
            except Exception as exc:
                logger.error("Approval Key 취득 실패: %s — 30초 후 재시도", exc)
                await asyncio.sleep(30)
                continue

            # WebSocket 실행
            self._ws_client = KisWebSocketClient(
                ws_url=self.cfg.ws_url,
                approval_key=approval_key,
                symbols=self.symbols,
                on_message=self._handle_message,
                subscribe_orderbook=self.cfg.subscribe_orderbook,
                subscribe_kospi=True,
                reconnect_delay_s=self.cfg.reconnect_delay_s,
                max_reconnect_attempts=self.cfg.max_reconnect_attempts,
                ping_interval_s=self.cfg.ping_interval_s,
            )

            # 장 마감 감시 + 유니버스 갱신 감시 + WS 동시 실행
            self._session_stop.clear()
            await asyncio.gather(
                self._ws_client.run(),
                self._market_close_watcher(),
                self._universe_refresh_watcher(),
            )

            if self._refresh_universe_pending is not None:
                # 유니버스 갱신으로 인한 세션 재시작 — 기존 어그리게이터 최대 보존
                new_syms = self._refresh_universe_pending
                self._refresh_universe_pending = None
                old_set = set(self.symbols)
                new_set = set(new_syms)
                added   = [s for s in new_syms if s not in old_set]
                removed = [s for s in self.symbols if s not in new_set]
                for sym in removed:
                    self.aggregators.pop(sym, None)
                for sym in added:
                    self.aggregators[sym] = KisOhlcvAggregator(sym, self.cfg.timeframes_minutes)
                self.symbols = new_syms
                logger.info(
                    "유니버스 갱신 후 WS 재연결: 심볼 %d개 (추가 %d, 제거 %d)",
                    len(self.symbols), len(added), len(removed),
                )
                # 추가된 심볼에 한해 pykrx 워밍업 (피처엔진 prev_close 초기화)
                if added:
                    _saved = self.symbols
                    self.symbols = added
                    try:
                        await self._warmup_pykrx()
                    except Exception as exc:
                        logger.debug("추가 심볼 pykrx 워밍업 실패 (무시): %s", exc)
                    self.symbols = _saved
                # _warmed_up 유지 → 다음 루프에서 전체 warmup 스킵
            else:
                # 장 마감 후 어그리게이터·워밍업 플래그 초기화 (다음 날 다시 warmup)
                for agg in self.aggregators.values():
                    agg.reset()
                self._warmed_up = False
                logger.info("장 마감 — 어그리게이터 초기화 완료")

    # ─────────────────────────────────────────────────────
    # 내부 메서드
    # ─────────────────────────────────────────────────────

    async def _universe_refresh_watcher(self) -> None:
        """30분마다 거래대금 상위 유니버스를 재조회, 20% 이상 변경 시 WS 세션 재시작.

        auto_universe=False 이면 즉시 반환 (비활성).
        """
        if not self.cfg.auto_universe:
            return

        _REFRESH_INTERVAL_S = 1800  # 30분
        _CHANGE_THRESHOLD   = 0.20  # 20% 이상 변경 시 재시작

        # 첫 갱신은 30분 후 — 장 시작 직후 이중 호출 방지
        try:
            await asyncio.wait_for(
                asyncio.shield(self._session_stop.wait()),
                timeout=_REFRESH_INTERVAL_S,
            )
        except asyncio.TimeoutError:
            pass
        if self._session_stop.is_set():
            return

        while not self._stop_event.is_set() and not self._session_stop.is_set():
            if not is_market_hours():
                return  # 장 마감 → _market_close_watcher가 처리
            try:
                new_syms = await self._fetch_top_volume_universe()
                if new_syms:
                    cur_set = set(self.symbols)
                    new_set = set(new_syms)
                    union   = cur_set | new_set
                    changed = len(cur_set ^ new_set)  # 추가 + 제거 심볼 수
                    ratio   = changed / max(len(union), 1)
                    if ratio >= _CHANGE_THRESHOLD:
                        logger.info(
                            "유니버스 변경 감지 (%.1f%%) — WS 세션 재시작: %d→%d",
                            ratio * 100, len(cur_set), len(new_set),
                        )
                        self._refresh_universe_pending = new_syms
                        self._session_stop.set()
                        if self._ws_client:
                            self._ws_client.stop()
                        return
                    else:
                        logger.debug(
                            "유니버스 소폭 변경 (%.1f%%) — 갱신 스킵 (임계값 %.0f%%)",
                            ratio * 100, _CHANGE_THRESHOLD * 100,
                        )
            except Exception as exc:
                logger.debug("유니버스 주기 갱신 실패 (무시): %s", exc)

            # 다음 갱신까지 대기
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._session_stop.wait()),
                    timeout=_REFRESH_INTERVAL_S,
                )
            except asyncio.TimeoutError:
                pass

    async def _market_close_watcher(self) -> None:
        """장 마감(15:15 KST) 감지 시 WS 중단."""
        while not self._stop_event.is_set():
            # 30초마다 확인 — 유니버스 갱신 세션 중단 신호도 함께 감시
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._session_stop.wait()),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                pass
            if self._session_stop.is_set():
                break  # 유니버스 갱신 신호 → 상위 gather() 정리
            if not is_market_hours():
                logger.info("장 마감 감지 — WebSocket 종료")
                if self._ws_client:
                    self._ws_client.stop()
                self._session_stop.set()
                break

    async def _handle_message(self, raw: str) -> None:
        """WebSocket 메시지 처리 진입점."""
        self.stats["messages"] = int(self.stats.get("messages", 0)) + 1
        try:
            msg_type, payload = parse_message(raw)
            if msg_type == "trade" and isinstance(payload, KisTradeTick):
                self._on_trade(payload)
            elif msg_type == "orderbook" and isinstance(payload, KisOrderBookSnapshot):
                self._on_orderbook(payload)
            elif msg_type == "index" and isinstance(payload, dict):
                self._on_index(payload)
        except Exception as exc:
            self.stats["errors"] = int(self.stats.get("errors", 0)) + 1
            logger.debug("메시지 처리 오류: %s", exc)

    def _on_index(self, payload: dict) -> None:
        """KOSPI 지수 체결가 → StockFeatureEngine에 주입."""
        price = payload.get("price")
        if price and self._feature_engine is not None:
            self._feature_engine.set_kospi_price(float(price), timeframe="5m")

    def _on_trade(self, tick: KisTradeTick) -> None:
        self.stats["trades"] = int(self.stats.get("trades", 0)) + 1
        self.persistence.on_tick(tick)
        if self._feature_engine is not None:
            self._feature_engine.on_tick(tick)

        agg = self.aggregators.get(tick.symbol)
        if agg is None:
            agg = KisOhlcvAggregator(tick.symbol, self.cfg.timeframes_minutes)
            self.aggregators[tick.symbol] = agg

        closed_bars = agg.on_tick(tick)
        for bar in closed_bars:
            self._on_bar(bar)

    def _on_orderbook(self, ob: KisOrderBookSnapshot) -> None:
        self.stats["orderbooks"] = int(self.stats.get("orderbooks", 0)) + 1
        self.orderbooks[ob.symbol] = ob
        self.persistence.on_orderbook(ob)
        if self._feature_engine is not None:
            self._feature_engine.on_orderbook(ob)

    def _on_bar(self, bar: KisOhlcvBar) -> None:
        self.stats["bars_closed"] = int(self.stats.get("bars_closed", 0)) + 1
        self.persistence.on_bar(bar)

        if self._feature_engine is not None:
            try:
                self._feature_engine.on_bar(bar)
                # 봉 완성 시 스코어 계산 (1m봉만)
                if bar.timeframe == "1m":
                    self._try_score(bar.symbol)
            except Exception as exc:
                logger.debug("피처엔진 on_bar 오류 [%s %s]: %s", bar.symbol, bar.timeframe, exc)

        logger.debug(
            "봉 완성 [%s %s] O=%d H=%d L=%d C=%d V=%d",
            bar.symbol, bar.timeframe,
            bar.open, bar.high, bar.low, bar.close, bar.volume,
        )

    def _try_score(self, symbol: str) -> None:
        """피처 계산 → K-GSQS 채점 → 신호 로그."""
        if self._feature_engine is None:
            return
        from deepsignal.scoring.kstock_scorer import compute_kgsqs, THRESHOLD_NOTIFY
        try:
            features = self._feature_engine.build_features(symbol)
            if features is None:
                return
            signal = compute_kgsqs(features)
            if signal.hard_blocked:
                logger.debug("[%s] 하드블록: %s", symbol, signal.blocked_reason)
                return
            if signal.total_score >= THRESHOLD_NOTIFY:
                logger.info(
                    "📈 K-GSQS 신호 [%s] %.1f pt → %s  sub=%s",
                    symbol,
                    signal.total_score,
                    signal.action,
                    {k: f"{v:.0f}" for k, v in signal.sub_scores.items()},
                )
                if self._signal_bridge is not None:
                    self._signal_bridge.on_signal(signal, current_price=float(features.price))
            else:
                logger.debug("[%s] K-GSQS %.1f pt (%s)", symbol, signal.total_score, signal.action)

            # 사후 수익률 체크 (1분마다)
            if self._signal_bridge is not None:
                current_prices = {
                    sym: float(state.last_tick.price)
                    for sym, state in self._feature_engine._states.items()
                    if state.last_tick is not None
                }
                completed = self._signal_bridge.check_outcomes(current_prices)
                # 가중치 자동최적화 (신규 완성 신호 발생 시)
                if completed and completed > 0:
                    self._maybe_optimize_weights()
            else:
                # signal_bridge 없어도 완성 체크 후 최적화 시도
                self._maybe_optimize_weights()
        except Exception as exc:
            logger.debug("채점 오류 [%s]: %s", symbol, exc)

    def _maybe_optimize_weights(self) -> None:
        """K-GSQS 가중치 자동최적화 (데이터 충분 시)."""
        try:
            if not hasattr(self, "_kstock_weight_optimizer"):
                from deepsignal.scoring.kstock_weight_optimizer import KStockWeightOptimizer
                self._kstock_weight_optimizer = KStockWeightOptimizer(
                    self.cfg.resolved_output_dir,
                    horizon_minutes=5,
                    asset_label="국장",
                )
                # 재시작 시 저장된 최적화 가중치 복원
                try:
                    from deepsignal.scoring import kstock_scorer
                    saved_w = self._kstock_weight_optimizer.load_weights()
                    kstock_scorer.WEIGHTS.update(saved_w)
                    logger.debug("저장된 K-GSQS 가중치 복원: %s", saved_w)
                except Exception as _we:
                    logger.debug("K-GSQS 가중치 복원 실패 (무시): %s", _we)

            opt = self._kstock_weight_optimizer
            if opt.should_run():
                logger.info("K-GSQS 국장 가중치 최적화 실행 중...")
                opt_result = opt.run()
                if "error" not in opt_result:
                    logger.info(
                        "K-GSQS 국장 가중치 최적화 완료: 승률 %.1f%% → %.1f%% (개선 %.1f%%) applied=%s",
                        opt_result.get("default_win_rate", 0) * 100,
                        opt_result.get("expected_win_rate", 0) * 100,
                        opt_result.get("improvement", 0) * 100,
                        opt_result.get("applied", True),
                    )
                else:
                    logger.debug("K-GSQS 국장 가중치 최적화 스킵: %s", opt_result.get("error"))
        except Exception as exc:
            logger.debug("K-GSQS 가중치 최적화 실패 (무시): %s", exc)

    async def _warmup_from_rest(self) -> None:
        """REST API로 최근 봉 데이터 로드 (pykrx 사용).

        Phase 2에서 StockFeatureEngine 연결 전까지는 봉만 저장.
        """
        # 자동 유니버스: 장 시작 전 거래대금 상위 N개 종목 자동 선정
        if self.cfg.auto_universe:
            try:
                new_syms = await self._fetch_top_volume_universe()
                if new_syms:
                    self.symbols = new_syms
                    # 어그리게이터 재생성
                    self.aggregators = {
                        sym: KisOhlcvAggregator(sym, self.cfg.timeframes_minutes)
                        for sym in self.symbols
                    }
                    logger.info(
                        "자동 유니버스 선정 완료: %d종목 — %s ...",
                        len(self.symbols),
                        ", ".join(self.symbols[:5]),
                    )
            except Exception as exc:
                logger.warning("자동 유니버스 선정 실패 (기본 목록 사용): %s", exc)

        logger.info("REST 워밍업 시작 (%d 심볼)...", len(self.symbols))
        try:
            await self._warmup_pykrx()
        except Exception as exc:
            logger.warning("REST 워밍업 실패 (비치명적): %s", exc)

    # ETF 브랜드 키워드 (이름에 포함되면 ETF로 판단)
    # 인버스·레버리지·선물 ETF도 차별 없이 모두 포함한다.
    _ETF_BRAND_KW = frozenset([
        "KODEX", "TIGER", "KBSTAR", "ARIRANG", "ACE", "SOL", "HANARO",
        "TIMEFOLIO", "FOCUS", "SMART", "KOSEF", "PLUS", "RISE", "WON", "BNK",
    ])

    async def _fetch_top_volume_universe(self) -> list[str]:
        """KIS REST API로 거래대금 상위 N개 종목코드 반환.

        구성: 상위 주식(n*2/3개) + ETF(n*1/3개, 인버스·레버리지·선물 포함).
        관리종목·투자경고는 항상 제외.
        """
        try:
            import aiohttp
        except ImportError:
            raise RuntimeError("aiohttp 패키지가 없습니다.")

        base = self.rest_base_url.rstrip("/")
        token_url = f"{base}/oauth2/tokenP"
        rank_url = f"{base}/uapi/domestic-stock/v1/quotations/volume-rank"
        headers_common = {
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": "FHPST01710000",
            "custtype": "P",
            "content-type": "application/json; charset=utf-8",
        }

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as sess:
            # 액세스 토큰 취득
            r = await sess.post(
                token_url,
                json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
                headers={"content-type": "application/json"},
            )
            if r.status != 200:
                raise RuntimeError(f"토큰 취득 실패 (HTTP {r.status})")
            token = (await r.json()).get("access_token")
            if not token:
                raise RuntimeError("access_token 없음")
            headers_common["authorization"] = f"Bearer {token}"

            n = self.cfg.universe_size
            n_stocks = (n * 2) // 3       # 종목 2/3
            n_etf = n - n_stocks           # ETF 1/3

            # ── Pass 1: 순수 주식 (이 단계만 ETF 제외 — ETF는 Pass 2에서 별도 합산) ──
            r1 = await sess.get(rank_url, headers=headers_common, params={
                "FID_COND_MRKT_DIV_CODE": self.cfg.universe_market,
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "1",           # 거래대금 기준
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "111111111",  # ETF·관리·경고 전부 제외
                "FID_INPUT_PRICE_1": "1000",
                "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
            })
            stocks_raw = (await r1.json()).get("output", []) if r1.status == 200 else []

            # ── Pass 2: ETF 포함 전체 (관리·경고만 제외) ──────────────────
            r2 = await sess.get(rank_url, headers=headers_common, params={
                "FID_COND_MRKT_DIV_CODE": self.cfg.universe_market,
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "1",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "000000000",  # 전체 포함
                "FID_INPUT_PRICE_1": "100",
                "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
            })
            all_raw = (await r2.json()).get("output", []) if r2.status == 200 else []

        def _valid_code(code: str) -> bool:
            return len(code) == 6 and code.isdigit()

        def _is_etf(name: str) -> bool:
            return any(k in name for k in self._ETF_BRAND_KW)

        # 주식 목록
        stock_codes: list[str] = []
        for row in stocks_raw:
            code = (row.get("mksc_shrn_iscd") or "").strip()
            if _valid_code(code):
                stock_codes.append(code)
            if len(stock_codes) >= n_stocks:
                break

        # ETF 목록 (인버스·레버리지·선물 포함 — 거래대금 상위 그대로)
        etf_codes: list[str] = []
        stock_set = set(stock_codes)
        for row in all_raw:
            code = (row.get("mksc_shrn_iscd") or "").strip()
            name = row.get("hts_kor_isnm", "")
            if not _valid_code(code):
                continue
            if code in stock_set:
                continue
            if not _is_etf(name):
                continue
            etf_codes.append(code)
            if len(etf_codes) >= n_etf:
                break

        combined = stock_codes + etf_codes
        logger.info(
            "유니버스 선정: 주식 %d개 + ETF %d개 = 총 %d개",
            len(stock_codes), len(etf_codes), len(combined),
        )
        return combined

    async def _warmup_pykrx(self) -> None:
        """pykrx로 최근 5일치 일봉 로드."""
        try:
            from pykrx import stock as krx
        except ImportError:
            logger.warning("pykrx 없음 — REST 워밍업 스킵")
            return

        from datetime import date, timedelta

        loop = asyncio.get_event_loop()
        end_date = date.today()
        start_date = end_date - timedelta(days=10)
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        for sym in self.symbols:
            try:
                df = await loop.run_in_executor(
                    None,
                    lambda s=sym: krx.get_market_ohlcv_by_date(start_str, end_str, s),
                )
                if df is None or df.empty:
                    continue
                count = 0
                for idx, row in df.iterrows():
                    ts_ms = int(
                        datetime.strptime(str(idx)[:10], "%Y-%m-%d")
                        .replace(tzinfo=KST)
                        .timestamp() * 1000
                    )
                    bar = KisOhlcvBar(
                        symbol=sym,
                        timeframe="1d",
                        open_ts_ms=ts_ms,
                        open=int(row.get("시가", 0) or 0),
                        high=int(row.get("고가", 0) or 0),
                        low=int(row.get("저가", 0) or 0),
                        close=int(row.get("종가", 0) or 0),
                        volume=int(row.get("거래량", 0) or 0),
                        trade_value=int(row.get("거래대금", 0) or 0),
                        trade_count=0,
                        closed=True,
                    )
                    if self._feature_engine is not None:
                        self._feature_engine.on_bar(bar, is_historical=True)
                    count += 1
                logger.debug("pykrx 워밍업 [%s]: %d봉", sym, count)
            except Exception as exc:
                logger.debug("pykrx 워밍업 [%s] 실패: %s", sym, exc)

        logger.info("pykrx 워밍업 완료")

    def get_status(self) -> dict[str, Any]:
        """파이프라인 현재 상태 (웹 UI용)."""
        ws_stats = self._ws_client.stats if self._ws_client else {}
        return {
            "running": not self._stop_event.is_set(),
            "market_hours": is_market_hours(),
            "symbols": self.symbols,
            "auto_universe": self.cfg.auto_universe,
            "universe_size": self.cfg.universe_size,
            "stats": {**self.stats, **ws_stats},
            "output_dir": str(self.persistence.output_dir),
            "symbol_states": self.persistence.get_all_symbols_state(),
        }
