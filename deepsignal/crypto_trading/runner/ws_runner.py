"""
crypto_ws_runner.py — WebSocket 이벤트 드리븐 + 4-스레드 단타 엔진.

구조:
  Thread 1 (ws)        : Upbit WebSocket 실시간 가격 수신 → SELL 임계값 체크 → 큐 적재
  Thread 2 (analysis)  : 주기적 분석 (60s+), BUY 시그널 생성, Telegram 메뉴 폴
  Thread 3 (execution) : signal_queue 소비 → 즉시 주문 실행
  Thread 4 (order_mgmt): 10초 주기 미체결 매도 추적·재접수 / 미체결 매수 취소

분석이 실행을 블로킹하지 않고, 가격 이벤트(TP/SL)는 WebSocket 수신
즉시 처리된다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.runner.auto_runner import (
    CryptoAutoRunnerConfig,
    _persist_trade_state,
    _today_key,
    load_runner_state,
    save_runner_state,
)
from deepsignal.crypto_trading.upbit_broker import CryptoHolding, UpbitBroker
from deepsignal.live_trading.time_utils import now_kst_iso

logger = logging.getLogger(__name__)


def _maybe_refresh_news_async(cfg: Any, markets: list[str]) -> None:
    """LLM 뉴스 감성 갱신을 백그라운드 스레드로 실행(핫패스 비차단).

    기능 OFF면 즉시 반환. 뉴스 수집(RSS) + 코인별 감성 분석 → 캐시.
    """
    import os as _o
    if _o.environ.get("CRYPTO_LLM_NEWS_ENABLED", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return
    if not markets:
        return

    def _work() -> None:
        try:
            # 1) 최신 뉴스 수집(RSS)
            try:
                from deepsignal.config.settings import load_settings
                from deepsignal.pipelines.daily_pipeline import collect_news_to_db
                from deepsignal.storage.database import init_database
                _settings = load_settings()
                _pathstr = str(init_database(_settings.db_path))
                collect_news_to_db(_pathstr, _settings)
            except Exception as exc:  # noqa: BLE001
                logger.debug("news collect skip: %s", exc)
            # 2) LLM 감성 분석 → 캐시
            from deepsignal.ai.crypto_news_sentiment import refresh_crypto_news_sentiment
            res = refresh_crypto_news_sentiment(markets, output_dir=cfg.output_dir, max_markets=len(markets))
            logger.info("LLM 뉴스 감성 갱신: %s", res)
        except Exception as exc:  # noqa: BLE001
            logger.warning("news refresh error: %s", exc)

    threading.Thread(target=_work, daemon=True, name="news-refresh").start()

# ────────────────────────────────────────────
# 공유 데이터 구조
# ────────────────────────────────────────────

@dataclass
class SellThreshold:
    """단일 보유 코인의 실시간 매도 임계값."""
    market: str
    avg_buy_price: float
    take_profit_price: float    # avg × (1 + tp%)
    stop_loss_price: float      # avg × (1 + sl%)  ← sl%는 음수
    trailing_stop_pct: float    # 고점 대비 X% 하락 시 청산
    volume: float               # 보유 수량 (total_quantity)
    peak_price: float = 0.0
    triggered: bool = False     # 중복 시그널 방지

    def check(self, price: float) -> str | None:
        if self.triggered:
            return None
        if price >= self.take_profit_price:
            return "take_profit"
        if price <= self.stop_loss_price:
            return "stop_loss"
        if self.peak_price > 0 and price < self.peak_price * (1.0 - self.trailing_stop_pct / 100.0):
            return "trailing_stop"
        return None

    def update_peak(self, price: float) -> None:
        if price > self.peak_price:
            self.peak_price = price


@dataclass
class PriceSignal:
    """WebSocket 스레드가 execution 스레드로 전달하는 시그널."""
    signal_type: str        # "sell" | "buy"
    market: str
    trigger_price: float
    reason: str
    plan: Any = None        # CryptoOrderPlan (buy 시그널 전용)
    volume: float = 0.0     # sell 시그널 전용
    fastlane: bool = False  # True = 승인 없이 자동 체결된 패스트레인 신호


class SharedRunnerState:
    """4개 스레드가 공유하는 상태. 내부 Lock으로 thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.price_cache: dict[str, float] = {}
        self.sell_thresholds: dict[str, SellThreshold] = {}
        self._markets_to_watch: set[str] = set()
        self.signal_queue: queue.Queue[PriceSignal] = queue.Queue()
        self.stop_event = threading.Event()
        self._markets_version: int = 0  # 변경 시 ws 재연결 트리거

    # ── 가격 캐시 + 임계값 체크 (ws 스레드 호출) ──

    def on_price(self, market: str, price: float) -> None:
        with self._lock:
            self.price_cache[market] = price
            thresh = self.sell_thresholds.get(market)
            if thresh is None:
                return
            thresh.update_peak(price)
            reason = thresh.check(price)
            if reason is None:
                return
            thresh.triggered = True
            sig = PriceSignal(
                signal_type="sell",
                market=market,
                trigger_price=price,
                reason=reason,
                volume=thresh.volume,
            )
        # Lock 해제 후 큐에 추가
        self.signal_queue.put(sig)
        logger.info("ws→exec: %s %s @ %.2f", market, reason, price)

    # ── 임계값 갱신 (analysis 스레드 호출) ──

    def update_sell_thresholds(self, thresholds: dict[str, SellThreshold]) -> None:
        with self._lock:
            for market, new_t in thresholds.items():
                old = self.sell_thresholds.get(market)
                if old:
                    new_t.peak_price = max(old.peak_price, new_t.peak_price)
                    if old.triggered and old.avg_buy_price == new_t.avg_buy_price:
                        new_t.triggered = True  # 아직 청산 안 된 경우 유지
            self.sell_thresholds = thresholds

    def clear_sell_threshold(self, market: str) -> None:
        with self._lock:
            self.sell_thresholds.pop(market, None)

    # ── 마켓 구독 목록 (ws 스레드가 읽음, analysis 스레드가 씀) ──

    def update_markets(self, markets: set[str]) -> None:
        with self._lock:
            if markets != self._markets_to_watch:
                self._markets_to_watch = set(markets)
                self._markets_version += 1

    def get_markets(self) -> list[str]:
        with self._lock:
            return sorted(self._markets_to_watch)

    def get_markets_version(self) -> int:
        with self._lock:
            return self._markets_version

    # ── BUY 시그널 적재 (analysis 스레드) ──

    def push_buy_signal(self, plan: Any, price: float, *, fastlane: bool = False) -> None:
        sig = PriceSignal(
            signal_type="buy",
            market=plan.market,
            trigger_price=price,
            reason="fastlane_buy" if fastlane else "analysis_buy",
            plan=plan,
            fastlane=fastlane,
        )
        self.signal_queue.put(sig)


# ────────────────────────────────────────────
# Thread 1: WebSocket
# ────────────────────────────────────────────

def _ws_thread_main(shared: SharedRunnerState) -> None:
    """Upbit WebSocket 수신 루프. asyncio 이벤트 루프를 이 스레드에서 운영."""
    from deepsignal.crypto_trading.crypto_ws_price_stream import stream_upbit_prices

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run() -> None:
        stop = asyncio.Event()

        def _check_stop() -> None:
            if shared.stop_event.is_set():
                stop.set()

        # 1초 주기로 파이썬 threading stop_event 확인
        async def _watch_stop() -> None:
            while not stop.is_set():
                _check_stop()
                await asyncio.sleep(1.0)

        async def _on_price(market: str, price: float) -> None:
            shared.on_price(market, price)

        watcher = loop.create_task(_watch_stop())
        try:
            await stream_upbit_prices(
                shared.get_markets,
                _on_price,
                stop,
            )
        finally:
            watcher.cancel()

    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
    logger.info("ws thread: 종료")


# ────────────────────────────────────────────
# Thread 2: Analysis
# ────────────────────────────────────────────

def _build_sell_thresholds(
    broker: UpbitBroker,
    cfg: CryptoAutoRunnerConfig,
    runner_state: dict[str, Any],
) -> dict[str, SellThreshold]:
    """현재 보유 코인에서 SELL 임계값 계산."""
    from deepsignal.crypto_trading.crypto_execution_engine import load_execution_positions

    thresholds: dict[str, SellThreshold] = {}
    try:
        holdings: list[CryptoHolding] = broker.get_crypto_holdings()
    except Exception as exc:
        logger.warning("analysis: holdings 조회 실패: %s", exc)
        return thresholds

    positions = load_execution_positions(runner_state)

    for h in holdings:
        market = h.market.upper()
        avg = float(h.avg_buy_price or 0)
        if avg <= 0:
            continue
        tp_pct = float(cfg.take_profit_pct)
        sl_pct = float(cfg.stop_loss_pct)        # 음수 값 (예: -2.0)
        trail_pct = 0.8  # ExecutionEngineConfig.trailing_stop_pct 기본값
        # ── 투자공격성 다이얼 → 매도(익절) 방식 연동 ──────────────────
        # 6단계+ trailing: 고정익절 사실상 끄고 트레일링으로만 청산(수익 달리기).
        try:
            from deepsignal.risk.aggression import resolve as _agg
            _p = _agg()
            if _p.take_profit_mode == "trailing":
                tp_pct = max(tp_pct, 50.0)                       # 고정익절 비활성(상한↑)
                trail_pct = round(1.5 + (_p.leverage_max - 1) * 0.5, 1)  # 1.5~2.5% 트레일링
            elif _p.take_profit_mode == "fixed":
                trail_pct = 0.6                                  # 보수: 타이트 트레일링
        except Exception:
            pass

        pos = positions.get(market)
        peak = float(pos.peak_price if pos else 0)
        cur = float(h.current_price or avg)
        peak = max(peak, cur)

        thresholds[market] = SellThreshold(
            market=market,
            avg_buy_price=avg,
            take_profit_price=avg * (1.0 + tp_pct / 100.0),
            stop_loss_price=avg * (1.0 + sl_pct / 100.0),
            trailing_stop_pct=trail_pct,
            volume=float(h.total_quantity),
            peak_price=peak,
        )
    return thresholds


def _universe_markets(broker: UpbitBroker, cfg: CryptoAutoRunnerConfig) -> set[str]:
    """구독 대상 마켓 = universe 상위 N종목 (WebSocket 사전 구독용)."""
    from deepsignal.crypto_trading.crypto_universe import CryptoUniverseConfig, resolve_crypto_markets

    try:
        uni_cfg = CryptoUniverseConfig(
            universe=str(cfg.crypto_universe),
            max_buy_scan_markets=int(cfg.max_buy_scan_markets),
        )
        result = resolve_crypto_markets(broker, config=uni_cfg)
        return {m.upper() for m in result.markets if m.startswith("KRW-")}
    except Exception as exc:
        logger.warning("analysis: universe 조회 실패: %s", exc)
        return set()


def _analysis_thread_main(
    broker: UpbitBroker,
    shared: SharedRunnerState,
    cfg: CryptoAutoRunnerConfig,
    *,
    execute: bool,
) -> None:
    """주기적 분석 스레드. BUY 추천 생성 + SELL 임계값 갱신 + Telegram 메뉴 폴."""
    from deepsignal.crypto_trading.runner.auto_runner import (
        run_crypto_auto_tick,
        poll_telegram_menu_fast,
        _today_key,
    )
    from deepsignal.crypto_trading.crypto_auto_execute_policy import should_auto_execute_crypto_on_runner_tick
    from deepsignal.crypto_trading.runner.fastlane import (
        should_fastlane,
        record_fastlane,
        extract_pwin,
        notify_fastlane_result,
    )

    interval_sec = max(cfg.interval_minutes * 60.0, 60.0)
    menu_sleep = max(float(cfg.menu_poll_seconds), 1.0)
    last_analysis_at = 0.0
    last_spread_save_at = 0.0
    last_feedback_tune_at = 0.0
    last_news_refresh_at = 0.0
    _SPREAD_SAVE_INTERVAL = 300.0       # 5분마다 스프레드 히스토리 저장
    _FEEDBACK_TUNE_INTERVAL = 86400.0   # Phase 4: 24시간마다 피드백 튜닝
    _NEWS_REFRESH_INTERVAL = 1800.0     # 30분마다 LLM 뉴스 감성 갱신(백그라운드)

    while not shared.stop_event.is_set():
        state = load_runner_state(cfg.output_dir)

        # 임계값 + 구독 마켓 갱신
        thresholds = _build_sell_thresholds(broker, cfg, state)
        shared.update_sell_thresholds(thresholds)

        holding_markets = set(thresholds.keys())
        universe = _universe_markets(broker, cfg)
        shared.update_markets(holding_markets | universe)

        # Telegram 메뉴 폴 (빠른 주기)
        try:
            poll_telegram_menu_fast(broker, cfg, state=state)
            save_runner_state(cfg.output_dir, state)
        except Exception as exc:
            logger.warning("analysis: menu poll error: %s", exc)

        # 분석 틱 (interval_sec 마다)
        now = time.time()

        # LLM 뉴스 감성 갱신 (30분마다, 백그라운드 — 핫패스 차단 안 함)
        if now - last_news_refresh_at >= _NEWS_REFRESH_INTERVAL:
            last_news_refresh_at = now
            _maybe_refresh_news_async(cfg, sorted(universe)[:30])

        if now - last_analysis_at >= interval_sec:
            logger.info("analysis: 분석 틱 시작")
            try:
                tick = run_crypto_auto_tick(broker, cfg)
                action = tick.get("action", "")
                print(json.dumps({"analysis_tick": tick}, ensure_ascii=False))

                # BUY 추천이 나왔고 자동실행 모드면 execution 큐에 적재
                if action in ("auto_executed_no_approval", "approved"):
                    # 이미 run_crypto_auto_tick 내부에서 실행 완료
                    pass
                elif action == "approval_sent":
                    plan_json = tick.get("plan_json")
                    if plan_json:
                        try:
                            from deepsignal.crypto_trading.crypto_order_plan import load_crypto_plan
                            from deepsignal.crypto_trading.runner.regime_policy import apply_regime_to_plan
                            plan = load_crypto_plan(plan_json)
                            if plan.side.lower() == "buy":
                                try:
                                    tickers = broker.get_tickers([plan.market])
                                    cur_price = float(tickers[plan.market].trade_price) if plan.market in tickers else 0.0
                                except Exception:
                                    cur_price = float(plan.limit_price or 0)

                                # ── Phase 5: 다이나믹 TP/SL 적용 ────────────
                                try:
                                    from deepsignal.crypto_trading.execution.dynamic_tp_sl import (
                                        apply_dynamic_tp_sl_to_plan,
                                    )
                                    plan, _dyn_tp_sl = apply_dynamic_tp_sl_to_plan(plan)
                                    if _dyn_tp_sl.reason != "dynamic_tp_sl_disabled":
                                        logger.info(
                                            "dynamic_tp_sl: %s → %s",
                                            plan.market, _dyn_tp_sl.reason,
                                        )
                                except Exception as _dyn_exc:
                                    logger.debug("dynamic_tp_sl 적용 실패 (무시): %s", _dyn_exc)
                                # ─────────────────────────────────────────────

                                # ── Phase 3: 레짐 연동 공격성 조절 ──────────
                                plan, regime_blocked, regime_reason = apply_regime_to_plan(plan)
                                if regime_blocked:
                                    logger.info(
                                        "analysis: 레짐 게이트 차단 %s → %s",
                                        plan.market, regime_reason,
                                    )
                                else:
                                    # ── Phase 1: 패스트레인 판정 ────────────
                                    today = _today_key()
                                    fl_state = load_runner_state(cfg.output_dir)
                                    fl_ok, fl_reason = should_fastlane(
                                        plan, fl_state, today, output_dir=cfg.output_dir
                                    )
                                    if fl_ok:
                                        logger.info(
                                            "analysis: 패스트레인 조건 충족 → 자동 실행 (%s) %s",
                                            plan.market, fl_reason,
                                        )
                                        shared.push_buy_signal(plan, cur_price, fastlane=True)
                                    elif should_auto_execute_crypto_on_runner_tick():
                                        # 기존 전체-자동실행 모드 폴백
                                        shared.push_buy_signal(plan, cur_price, fastlane=False)
                                    else:
                                        logger.info(
                                            "analysis: 패스트레인 미충족 (%s) → 승인 대기", fl_reason,
                                        )
                                    # ─────────────────────────────────────────
                        except Exception as exc:
                            logger.warning("analysis: buy signal push 실패: %s", exc)

                # ── Phase 6: 피라미딩 스캔 ──────────────────────────
                try:
                    from deepsignal.crypto_trading.runner.pyramiding import (
                        load_pyramiding_config,
                        scan_pyramid_candidates,
                        record_pyramid,
                        notify_pyramid_result,
                    )
                    pyr_cfg = load_pyramiding_config()
                    if pyr_cfg.enabled:
                        holdings = broker.get_holdings()
                        pyr_state = load_runner_state(cfg.output_dir)
                        pyr_today = _today_key()
                        # 마지막 틱의 GSQS를 state에서 꺼내 활용
                        gsqs_cache = pyr_state.get("last_gsqs_by_market") or {}
                        candidates = scan_pyramid_candidates(
                            holdings, pyr_state, pyr_today,
                            cfg=pyr_cfg,
                            gsqs_by_market=gsqs_cache,
                            base_order_krw=float(cfg.max_order_value or 300_000),
                        )
                        for addon_plan, pyr_reason in candidates:
                            try:
                                addon_price = float(addon_plan.limit_price or 0)
                                shared.push_buy_signal(addon_plan, addon_price, fastlane=False)
                                record_pyramid(pyr_state, market=addon_plan.market, today_key=pyr_today)
                                save_runner_state(cfg.output_dir, pyr_state)
                                notify_pyramid_result(
                                    cfg.output_dir, addon_plan,
                                    success=True, reason=pyr_reason,
                                )
                            except Exception as _pe:
                                logger.warning("pyramiding: %s 실행 실패: %s", addon_plan.market, _pe)
                except Exception as _pyr_exc:
                    logger.debug("pyramiding scan 실패 (무시): %s", _pyr_exc)
                # ─────────────────────────────────────────────────────

            except Exception as exc:
                logger.error("analysis: tick error: %s", exc, exc_info=True)
            last_analysis_at = now

        # ── 동적 스프레드 히스토리 주기 저장 ──────────────────────
        now2 = time.time()
        if now2 - last_spread_save_at >= _SPREAD_SAVE_INTERVAL:
            try:
                from deepsignal.crypto_trading.execution.spread_gate import get_spread_gate
                get_spread_gate(cfg.output_dir).save(cfg.output_dir)
                last_spread_save_at = now2
            except Exception as _se:
                logger.debug("spread gate save 실패: %s", _se)
        # ────────────────────────────────────────────────────────

        # ── Phase 4: 피드백 튜닝 (24h 주기) ──────────────────────
        if now2 - last_feedback_tune_at >= _FEEDBACK_TUNE_INTERVAL:
            try:
                from deepsignal.crypto_trading.runner.feedback_tuner import (
                    run_feedback_tuning,
                    load_feedback_tuner_config,
                )
                fb_cfg = load_feedback_tuner_config()
                if fb_cfg.enabled:
                    from deepsignal.crypto_trading.runner.fastlane import load_fastlane_config
                    fl_cfg = load_fastlane_config()
                    db_path = str(cfg.output_dir).rstrip("/") + "/crypto_recommendation_outcomes.db"
                    fb_result = run_feedback_tuning(
                        db_path, cfg.output_dir,
                        cfg=fb_cfg,
                        base_min_gsqs=fl_cfg.min_gsqs,
                        base_min_pwin=fl_cfg.min_pwin,
                    )
                    logger.info(
                        "feedback_tuner: 완료 — gsqs=%.2f pwin=%.4f samples=%d",
                        fb_result.tuned_min_gsqs or fl_cfg.min_gsqs,
                        fb_result.tuned_min_pwin or fl_cfg.min_pwin,
                        fb_result.total_samples,
                    )
            except Exception as _fe:
                logger.warning("feedback_tuner: 실행 실패 (무시): %s", _fe)
            last_feedback_tune_at = now2
        # ────────────────────────────────────────────────────────

        shared.stop_event.wait(timeout=menu_sleep)

    logger.info("analysis thread: 종료")


# ────────────────────────────────────────────
# Thread 3: Execution
# ────────────────────────────────────────────

def _execute_sell_signal(
    broker: UpbitBroker,
    sig: PriceSignal,
    shared: SharedRunnerState,
    cfg: CryptoAutoRunnerConfig,
    *,
    execute: bool,
) -> None:
    """SELL 시그널 실행 (WebSocket 트리거)."""
    from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
    from deepsignal.crypto_trading.crypto_execution_engine import CryptoExecutionEngine, ExecutionEngineConfig
    from deepsignal.crypto_trading.crypto_sell_pricing import round_crypto_limit_price

    market = sig.market
    price = sig.trigger_price
    logger.info("exec: SELL %s reason=%s price=%.2f", market, sig.reason, price)

    try:
        holdings = broker.get_crypto_holdings()
        holding = next((h for h in holdings if h.market.upper() == market), None)
        if holding is None:
            logger.warning("exec: %s 보유 없음, SELL 스킵", market)
            shared.clear_sell_threshold(market)
            return

        vol = float(holding.total_quantity)
        if vol <= 0:
            shared.clear_sell_threshold(market)
            return

        limit_px = round_crypto_limit_price(price)
        eng_cfg = ExecutionEngineConfig(
            take_profit_pct=cfg.take_profit_pct,
            stop_loss_pct=cfg.stop_loss_pct,
        )
        plan = CryptoOrderPlan(
            market=market,
            side="sell",
            limit_price=limit_px,
            volume=vol,
            krw_amount=vol * limit_px,
            take_profit_pct=cfg.take_profit_pct,
            stop_loss_pct=cfg.stop_loss_pct,
        )
        eng = CryptoExecutionEngine(broker, cfg=eng_cfg, output_dir=cfg.output_dir)
        order = eng.execute_sell(plan, execute=execute)

        from deepsignal.crypto_trading.crypto_overtrading_guards import record_sell_in_state

        state = load_runner_state(cfg.output_dir)
        state["last_order_date"] = _today_key()
        state["orders_today"] = int(state.get("orders_today", 0) or 0) + 1
        # post_sell_reentry_cooldown 작동을 위해 매도 시각 기록
        record_sell_in_state(state, market=market)
        save_runner_state(cfg.output_dir, state)

        print(json.dumps({
            "ws_sell": {
                "market": market,
                "reason": sig.reason,
                "trigger_price": price,
                "limit_price": limit_px,
                "volume": vol,
                "order_status": order.status,
                "order_uuid": order.uuid,
                "execute": execute,
                "ts": now_kst_iso(),
            }
        }, ensure_ascii=False))

        shared.clear_sell_threshold(market)

    except Exception as exc:
        logger.error("exec: SELL 실패 %s: %s", market, exc, exc_info=True)


def _execute_buy_signal(
    broker: UpbitBroker,
    sig: PriceSignal,
    shared: SharedRunnerState,
    cfg: CryptoAutoRunnerConfig,
    *,
    execute: bool,
) -> None:
    """BUY 시그널 실행 (analysis 스레드 추천). 패스트레인 신호면 사후 통보 포함."""
    from deepsignal.crypto_trading.crypto_execution_engine import CryptoExecutionEngine, ExecutionEngineConfig
    from deepsignal.crypto_trading.runner.fastlane import (
        record_fastlane, extract_pwin, notify_fastlane_result,
    )

    plan = sig.plan
    if plan is None:
        return
    is_fastlane = sig.fastlane
    logger.info("exec: BUY %s @ ~%.2f (fastlane=%s)", sig.market, sig.trigger_price, is_fastlane)

    try:
        eng_cfg = ExecutionEngineConfig(
            take_profit_pct=cfg.take_profit_pct,
            stop_loss_pct=cfg.stop_loss_pct,
        )
        eng = CryptoExecutionEngine(broker, cfg=eng_cfg, output_dir=cfg.output_dir)
        state = load_runner_state(cfg.output_dir)
        result = eng.execute_buy(plan, execute=execute, runner_state=state)

        today = _today_key()
        if result.success:
            state["last_order_date"] = today
            state["orders_today"] = int(state.get("orders_today", 0) or 0) + 1
            _persist_trade_state(cfg.output_dir, plan)
            # 패스트레인 실행 횟수 기록
            if is_fastlane:
                pwin = extract_pwin(plan)
                gsqs = float(getattr(plan, "final_score", None) or 0) or None
                record_fastlane(state, market=sig.market, today_key=today, pwin=pwin, gsqs=gsqs)

        save_runner_state(cfg.output_dir, state)

        log_entry: dict = {
            "market": sig.market,
            "success": result.success,
            "limit_price": result.limit_price,
            "krw_amount": result.krw_amount,
            "win_probability": result.win_probability,
            "reasons": result.reasons,
            "fastlane": is_fastlane,
            "execute": execute,
            "ts": now_kst_iso(),
        }
        print(json.dumps({"ws_buy": log_entry}, ensure_ascii=False))

        # 패스트레인 → 텔레그램 사후 통보 (성공/실패 모두)
        if is_fastlane:
            notify_fastlane_result(
                str(cfg.output_dir),
                plan,
                success=result.success,
                pwin=extract_pwin(plan),
                reasons=result.reasons,
                order_uuid=getattr(result.order, "uuid", None) if result.order else None,
            )

    except Exception as exc:
        logger.error("exec: BUY 실패 %s: %s", sig.market, exc, exc_info=True)
        if is_fastlane:
            notify_fastlane_result(
                str(cfg.output_dir),
                plan,
                success=False,
                pwin=extract_pwin(plan),
                reasons=[str(exc)],
            )


def _execution_thread_main(
    broker: UpbitBroker,
    shared: SharedRunnerState,
    cfg: CryptoAutoRunnerConfig,
    *,
    execute: bool,
) -> None:
    """signal_queue 소비 스레드. SELL/BUY 즉시 처리."""
    while not shared.stop_event.is_set():
        try:
            sig = shared.signal_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        try:
            if sig.signal_type == "sell":
                _execute_sell_signal(broker, sig, shared, cfg, execute=execute)
            elif sig.signal_type == "buy":
                _execute_buy_signal(broker, sig, shared, cfg, execute=execute)
        finally:
            shared.signal_queue.task_done()

    logger.info("execution thread: 종료")


# ────────────────────────────────────────────
# Thread 4: Order Management
# ────────────────────────────────────────────

def _order_mgmt_thread_main(
    broker: UpbitBroker,
    shared: SharedRunnerState,
    cfg: CryptoAutoRunnerConfig,
    *,
    execute: bool,
    interval: float = 10.0,
) -> None:
    """미체결 주문 관리 (10초 주기): 매도 재접수 + 매수 취소."""
    from deepsignal.crypto_trading.crypto_order_manager import (
        manage_open_buy_orders,
        manage_open_sell_orders,
    )

    while not shared.stop_event.is_set():
        try:
            sell_actions = manage_open_sell_orders(broker, cfg.output_dir, execute=execute)
            if sell_actions:
                print(json.dumps({"order_manager": sell_actions}, ensure_ascii=False))
        except Exception as exc:
            logger.warning("order_mgmt(sell): 오류: %s", exc)

        try:
            buy_actions = manage_open_buy_orders(broker, cfg.output_dir, execute=execute)
            if buy_actions:
                print(json.dumps({"order_manager_buy": buy_actions}, ensure_ascii=False))
        except Exception as exc:
            logger.warning("order_mgmt(buy): 오류: %s", exc)

        shared.stop_event.wait(timeout=interval)

    logger.info("order_mgmt thread: 종료")


# ────────────────────────────────────────────
# 진입점
# ────────────────────────────────────────────

def run_crypto_ws_runner_loop(
    broker: UpbitBroker,
    cfg: CryptoAutoRunnerConfig,
    *,
    execute: bool = False,
) -> None:
    """
    WebSocket + 4-Thread 단타 엔진 메인 루프.

    Ctrl-C(KeyboardInterrupt) 또는 SIGTERM으로 모든 스레드 정상 종료.
    """
    from deepsignal.crypto_trading.crypto_env import emit_crypto_runner_startup_log, ensure_crypto_runtime_env

    env_result = ensure_crypto_runtime_env()
    emit_crypto_runner_startup_log(execute=execute, env_result=env_result)
    logger.info("ws-runner: 시작 (execute=%s)", execute)

    shared = SharedRunnerState()

    threads = [
        threading.Thread(
            target=_ws_thread_main,
            args=(shared,),
            name="ws-price-stream",
            daemon=True,
        ),
        threading.Thread(
            target=_analysis_thread_main,
            args=(broker, shared, cfg),
            kwargs={"execute": execute},
            name="analysis",
            daemon=True,
        ),
        threading.Thread(
            target=_execution_thread_main,
            args=(broker, shared, cfg),
            kwargs={"execute": execute},
            name="execution",
            daemon=True,
        ),
        threading.Thread(
            target=_order_mgmt_thread_main,
            args=(broker, shared, cfg),
            kwargs={"execute": execute},
            name="order-mgmt",
            daemon=True,
        ),
    ]

    for t in threads:
        t.start()
        logger.info("ws-runner: 스레드 시작 → %s", t.name)

    try:
        while True:
            alive = [t for t in threads if t.is_alive()]
            if not alive:
                logger.error("ws-runner: 모든 스레드 종료됨, 루프 탈출")
                break
            time.sleep(5.0)
    except KeyboardInterrupt:
        logger.info("ws-runner: KeyboardInterrupt → 종료 중...")
    finally:
        shared.stop_event.set()
        for t in threads:
            t.join(timeout=10.0)
        logger.info("ws-runner: 완전 종료")
