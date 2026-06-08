"""Layer 4 — execution engine: orderbook gates, limit buy/sell, Kelly sizing, dynamic exits."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from deepsignal.crypto_trading.crypto_execution_quality import (
    evaluate_pre_trade,
    record_fill_slippage_feedback,
    should_block_entry_by_execution_quality,
)
from deepsignal.crypto_trading.crypto_order_plan import CryptoOrderPlan
from deepsignal.crypto_trading.crypto_sell_pricing import round_crypto_limit_price
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitOrderResult, UpbitTicker
from deepsignal.live_trading.time_utils import now_kst, now_kst_iso, parse_datetime_with_default_tz
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto


def _news_event_block(market: str) -> str | None:
    """LLM 뉴스 감성 캐시에서 risk=block 악재면 차단 사유 반환. 아니면 None(통과).

    기능 OFF(CRYPTO_LLM_NEWS_ENABLED)거나 캐시 없으면 항상 None — fail-open.
    """
    try:
        if os.environ.get("CRYPTO_LLM_NEWS_ENABLED", "false").strip().lower() not in ("1", "true", "yes", "on"):
            return None
        if os.environ.get("CRYPTO_NEWS_GATE_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
            return None
        from deepsignal.ai.crypto_news_sentiment import load_news_sentiment
        rec = load_news_sentiment(str(market))
        if rec and rec.get("risk") == "block":
            ev = rec.get("event", "악재")
            summ = rec.get("summary") or ""
            return f"뉴스 악재 차단({ev}): {summ}".strip()
    except Exception:
        return None
    return None


def _log_order_failure(output_dir: Any, plan: Any, *, stage: str, reasons: list[str], krw: float = 0.0) -> None:
    """매수 실패/취소를 사유와 함께 이력에 남긴다(대시보드 표시용). 실패해도 무시."""
    try:
        from deepsignal.crypto_trading.execution.order_failure_log import record_crypto_order_failure
        record_crypto_order_failure(
            output_dir,
            market=str(getattr(plan, "market", "")),
            side="buy",
            stage=stage,
            reasons=reasons,
            krw=krw,
            display_name=str(getattr(plan, "display_name", "") or getattr(plan, "market", "")),
        )
    except Exception:
        pass

ExitReason = Literal[
    "ai_stop",
    "trailing_stop",
    "time_stop",
    "partial_take_profit",
    "take_profit",
    "near_take_profit",
    "stop_loss",
    "near_stop_loss",
    "overweight_reduce",
]


def execution_engine_enabled() -> bool:
    raw = (
        os.environ.get("CRYPTO_EXECUTION_ENGINE", "true")
        or os.environ.get("DEEPSIGNAL_CRYPTO_EXECUTION_ENGINE", "true")
    ).strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass
class ExecutionEngineConfig:
    # env 기반 값은 인스턴스 생성 시점에 읽어야 다이얼(런타임 env 변경)이 반영된다.
    # (클래스 정의 시점 1회 평가하면 import 후 적용된 다이얼이 안 먹힘)
    # 실행엔진 자체 ML 승률 게이트. 공격성 다이얼이 CRYPTO_EXEC_MIN_WIN_PROB로 낮춤(9~10).
    buy_min_win_prob: float = field(default_factory=lambda: float(os.environ.get("CRYPTO_EXEC_MIN_WIN_PROB") or 0.55))
    # AI 승률 매도 임계값. 매수 임계값보다 낮아야 모순 안 됨(다이얼이 낮춤).
    sell_ai_stop_prob: float = field(default_factory=lambda: float(os.environ.get("CRYPTO_SELL_AI_STOP_PROB") or 0.40))
    max_spread_pct: float = field(default_factory=lambda: float(os.environ.get("CRYPTO_MAX_SPREAD_PCT") or 0.25))
    min_bid_ask_volume_ratio: float = field(default_factory=lambda: float(os.environ.get("CRYPTO_MIN_BID_ASK_RATIO") or 1.0))
    orderbook_levels: int = 5
    limit_timeout_sec: float = 10.0
    limit_poll_sec: float = 0.5
    limit_retry_max: int = 1  # one retry after cancel
    use_mid_or_bid_plus_tick: bool = True
    kelly_max_fraction: float = 0.05
    kelly_min_fraction: float = 0.01
    trailing_stop_pct: float = 0.8
    partial_tp_pct: float = 1.2
    partial_tp_fraction: float = 0.5
    time_stop_minutes: float = 5.0
    time_stop_max_abs_pnl_pct: float = 0.5
    ai_recheck_interval_sec: float = 30.0
    take_profit_pct: float = _CRYPTO.take_profit_pct
    stop_loss_pct: float = _CRYPTO.stop_loss_pct


@dataclass
class OrderbookCheckResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    best_bid: float = 0.0
    best_ask: float = 0.0
    mid_price: float = 0.0
    spread_pct: float = 0.0
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    bid_ask_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BuyExecutionResult:
    success: bool
    order: UpbitOrderResult | None = None
    reasons: list[str] = field(default_factory=list)
    limit_price: float = 0.0
    krw_amount: float = 0.0
    win_probability: float = 0.0
    kelly_fraction: float = 0.0
    orderbook: OrderbookCheckResult | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.order is not None:
            d["order"] = {
                "uuid": self.order.uuid,
                "status": self.order.status,
                "price": self.order.price,
                "volume": self.order.volume,
                "krw_amount": self.order.krw_amount,
            }
        if self.orderbook is not None:
            d["orderbook"] = self.orderbook.to_dict()
        return d


@dataclass
class SellExitDecision:
    market: str
    reason: ExitReason
    volume_fraction: float
    limit_price: float
    message: str
    pnl_pct: float = 0.0
    win_probability: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PositionExecutionState:
    peak_price: float = 0.0
    partial_taken: bool = False
    remaining_fraction: float = 1.0
    entry_ts: str = ""
    last_ai_check_ts: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tick_increment(price: float) -> float:
    px = float(price)
    if px >= 1_000_000:
        return 1000.0
    if px >= 10_000:
        return 1.0
    if px >= 100:
        return 1.0
    if px >= 10:
        return 0.1
    return 0.01


def limit_price_bid_plus_tick(best_bid: float) -> float:
    return round_crypto_limit_price(float(best_bid) + _tick_increment(best_bid))


def kelly_fraction(
    win_prob: float,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_fraction: float = 0.05,
    min_fraction: float = 0.01,
) -> float:
    """f = (P*b - (1-P)) / b, b = reward/risk ratio (positive)."""
    p = max(0.0, min(1.0, float(win_prob)))
    reward = max(0.01, float(take_profit_pct))
    risk = max(0.01, abs(float(stop_loss_pct)))
    b = reward / risk
    f = (p * b - (1.0 - p)) / b
    if f <= 0:
        return 0.0
    return max(min_fraction, min(max_fraction, f))


def kelly_order_krw(
    total_portfolio_krw: float,
    win_prob: float,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    max_fraction: float = 0.05,
    min_fraction: float = 0.01,
    floor_krw: float = 10_000.0,
) -> tuple[float, float]:
    frac = kelly_fraction(
        win_prob,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        max_fraction=max_fraction,
        min_fraction=min_fraction,
    )
    if frac <= 0 or total_portfolio_krw <= 0:
        return 0.0, frac
    raw = total_portfolio_krw * frac
    return max(floor_krw, math.floor(raw)), frac


def check_orderbook_for_buy(
    broker: UpbitBroker,
    market: str,
    *,
    max_spread_pct: float = 0.15,
    min_bid_ask_ratio: float = 1.5,
    levels: int = 5,
) -> OrderbookCheckResult:
    try:
        ob = broker.get_orderbook(market, levels=levels)
    except Exception as exc:
        return OrderbookCheckResult(allowed=False, reasons=[f"호가 조회 실패: {exc}"])

    units = ob.get("orderbook_units") or []
    if not units:
        return OrderbookCheckResult(allowed=False, reasons=["호가 데이터 없음"])

    best = units[0]
    bid = float(best.get("bid_price") or 0)
    ask = float(best.get("ask_price") or 0)
    if bid <= 0 or ask <= 0:
        return OrderbookCheckResult(allowed=False, reasons=["유효 호가 없음"])

    mid = (bid + ask) / 2.0
    spread_pct = (ask - bid) / mid * 100.0 if mid > 0 else 999.0
    bid_vol = sum(float(u.get("bid_size") or 0) for u in units[:levels])
    ask_vol = sum(float(u.get("ask_size") or 0) for u in units[:levels])
    ratio = bid_vol / ask_vol if ask_vol > 0 else 0.0

    # ── 동적 스프레드 게이트 (Phase 2) ────────────────────────────────
    # 관측값 항상 기록 (enabled 여부 무관)
    try:
        from deepsignal.crypto_trading.execution.spread_gate import get_spread_gate
        gate = get_spread_gate()
        gate.record(market, spread_pct)
        # CRYPTO_DYNAMIC_SPREAD_ENABLED=true 면 동적 임계값 사용
        _dyn_allowed, _dyn_reason = gate.check(market, spread_pct)
        if not _dyn_allowed:
            # 동적 게이트가 활성화됐고 차단됨 → max_spread_pct 무시하고 동적 기준 적용
            effective_max = gate.threshold(market)
        else:
            effective_max = max_spread_pct  # 비활성 or 통과: 원래 기준 유지
    except Exception:
        effective_max = max_spread_pct
    # ──────────────────────────────────────────────────────────────────

    reasons: list[str] = []
    if spread_pct > effective_max:
        reasons.append(f"스프레드 {spread_pct:.3f}% > {effective_max:.2f}%")
    if ask_vol > 0 and bid_vol < ask_vol * min_bid_ask_ratio:
        reasons.append(
            f"매수벽 부족: bid_vol {bid_vol:.4f} < ask×{min_bid_ask_ratio} ({ask_vol * min_bid_ask_ratio:.4f})"
        )

    return OrderbookCheckResult(
        allowed=len(reasons) == 0,
        reasons=reasons,
        best_bid=bid,
        best_ask=ask,
        mid_price=mid,
        spread_pct=spread_pct,
        bid_volume=bid_vol,
        ask_volume=ask_vol,
        bid_ask_ratio=ratio,
    )


def compute_entry_limit_price(
    ob: OrderbookCheckResult,
    *,
    use_mid: bool = True,
) -> float:
    # 공격적 체결(높은 공격성 단계): 호가 맨앞(best ask)을 지정가로 잡아 즉시 체결.
    # 스프레드 게이트를 이미 통과한 뒤이므로 지불 스프레드는 한도 내로 제한된다.
    import os as _o
    if _o.environ.get("CRYPTO_AGGRESSIVE_FILL", "").strip().lower() in ("1", "true", "yes", "on"):
        if ob.best_ask > 0:
            return round_crypto_limit_price(ob.best_ask)
    if use_mid and ob.mid_price > 0:
        return round_crypto_limit_price(ob.mid_price)
    if ob.best_bid > 0:
        return limit_price_bid_plus_tick(ob.best_bid)
    return 0.0


def wait_limit_order(
    broker: UpbitBroker,
    uuid: str,
    *,
    timeout_sec: float = 10.0,
    poll_sec: float = 0.5,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.5, float(timeout_sec))
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = broker.get_order(uuid)
        state = str(last.get("state", ""))
        executed = float(last.get("executed_volume", 0) or 0)
        if state == "done":
            return last
        if executed > 0:
            return last
        if state == "cancel":
            return last
        time.sleep(max(0.2, float(poll_sec)))
    return last


def place_limit_with_timeout(
    broker: UpbitBroker,
    *,
    market: str,
    side: Literal["buy", "sell"],
    limit_price: float,
    krw_amount: float = 0.0,
    volume: float = 0.0,
    execute: bool,
    timeout_sec: float = 10.0,
    poll_sec: float = 0.5,
) -> tuple[UpbitOrderResult, dict[str, Any]]:
    step: dict[str, Any] = {"side": side, "limit_price": limit_price}
    if side == "buy":
        order = broker.place_limit_buy(
            market=market,
            krw_amount=krw_amount,
            price=limit_price,
            execute=execute,
        )
    else:
        order = broker.place_limit_sell(
            market=market,
            volume=volume,
            price=limit_price,
            execute=execute,
        )
    step["uuid"] = order.uuid
    step["status"] = order.status
    if not execute or not order.uuid or broker.config.dry_run:
        step["dry_run"] = True
        return order, step

    raw = wait_limit_order(broker, str(order.uuid), timeout_sec=timeout_sec, poll_sec=poll_sec)
    state = str(raw.get("state", ""))
    executed = float(raw.get("executed_volume", 0) or 0)
    step["final_state"] = state
    step["executed_volume"] = executed

    if state != "done" and executed <= 0:
        try:
            broker.cancel_order(str(order.uuid))
            step["cancelled"] = True
        except Exception as exc:
            step["cancel_failed"] = str(exc)
    elif executed > 0 and state != "done":
        step["partial_fill"] = True
    return order, step


def load_execution_positions(state: dict[str, Any]) -> dict[str, PositionExecutionState]:
    raw = state.get("execution_positions") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, PositionExecutionState] = {}
    for mk, row in raw.items():
        if not isinstance(row, dict):
            continue
        out[str(mk).upper()] = PositionExecutionState(
            peak_price=float(row.get("peak_price") or 0),
            partial_taken=bool(row.get("partial_taken")),
            remaining_fraction=float(row.get("remaining_fraction", 1.0) or 1.0),
            entry_ts=str(row.get("entry_ts") or ""),
            last_ai_check_ts=str(row.get("last_ai_check_ts") or ""),
        )
    return out


def save_execution_positions(state: dict[str, Any], positions: dict[str, PositionExecutionState]) -> None:
    state["execution_positions"] = {k: v.to_dict() for k, v in positions.items()}


def record_position_entry(
    state: dict[str, Any],
    *,
    market: str,
    entry_price: float,
) -> None:
    positions = load_execution_positions(state)
    m = market.upper()
    px = float(entry_price)
    positions[m] = PositionExecutionState(
        peak_price=px,
        partial_taken=False,
        remaining_fraction=1.0,
        entry_ts=now_kst_iso(),
        last_ai_check_ts="",
    )
    save_execution_positions(state, positions)


def clear_position_execution(state: dict[str, Any], market: str) -> None:
    positions = load_execution_positions(state)
    positions.pop(market.upper(), None)
    save_execution_positions(state, positions)


def update_peak_price(
    state: dict[str, Any],
    *,
    market: str,
    current_price: float,
) -> float:
    positions = load_execution_positions(state)
    m = market.upper()
    pos = positions.get(m)
    if pos is None:
        return float(current_price)
    peak = max(float(pos.peak_price or 0), float(current_price))
    pos.peak_price = peak
    positions[m] = pos
    save_execution_positions(state, positions)
    return peak


def mark_partial_taken(state: dict[str, Any], market: str) -> None:
    positions = load_execution_positions(state)
    m = market.upper()
    pos = positions.get(m)
    if pos is None:
        return
    pos.partial_taken = True
    pos.remaining_fraction = max(0.0, float(pos.remaining_fraction) * 0.5)
    positions[m] = pos
    save_execution_positions(state, positions)


def _resolve_win_probability(
    plan: CryptoOrderPlan,
    *,
    default: float = 0.55,
    predictor: Callable[[str], float] | None = None,
) -> float:
    bd = plan.score_breakdown if isinstance(plan.score_breakdown, dict) else {}
    for key in ("win_probability", "p_win", "ml_win_prob"):
        if key in bd:
            try:
                return float(bd[key])
            except (TypeError, ValueError):
                pass
    if predictor is not None:
        try:
            return float(predictor(plan.market))
        except Exception:
            pass
    gates = plan.quality_gates if isinstance(plan.quality_gates, dict) else {}
    if "win_probability" in gates:
        try:
            return float(gates["win_probability"])
        except (TypeError, ValueError):
            pass
    return default


def _load_lgbm_predictor(output_dir: str | Path) -> Callable[[str], float] | None:
    model_dir = Path(output_dir) / "models"
    for horizon in (5, 10):
        path = model_dir / f"crypto_scalp_lgbm_{horizon}m.txt"
        if not path.is_file():
            continue
        try:
            from deepsignal.market_data.feature_engine import FeatureEngine
            from deepsignal.ml.crypto_scalp_lgbm import load_lgbm_model, predict_proba
            import numpy as np

            model = load_lgbm_model(path)
            live = Path(output_dir) / "binance_stream" / "live_state.json"
            eng = FeatureEngine()
            if live.is_file():
                payload = json.loads(live.read_text(encoding="utf-8"))
                eng.ingest_live_state(payload)
                # 봉 히스토리 워밍업(필수): 없으면 모멘텀 피처 0 → 예측 상수 0.000
                try:
                    bars_dir = Path(output_dir) / "binance_stream" / "bars"
                    syms = [str(s).upper() for s in (payload.get("symbols") or [])]
                    if bars_dir.is_dir() and syms:
                        eng._load_historical_bars(bars_dir, syms, n_bars=120)
                except Exception:
                    pass

            def _pred(sym: str) -> float:
                vec = eng.compute(sym.replace("KRW-", "") + "USDT" if sym.startswith("KRW-") else sym)
                if vec is None or len(vec) == 0:
                    return 0.5
                return float(predict_proba(model, np.asarray(vec).reshape(1, -1))[0])

            return _pred
        except Exception:
            continue
    return None


class CryptoExecutionEngine:
    def __init__(
        self,
        broker: UpbitBroker,
        *,
        cfg: ExecutionEngineConfig | None = None,
        output_dir: str | Path = "outputs",
    ) -> None:
        self.broker = broker
        self.cfg = cfg or ExecutionEngineConfig()
        self.output_dir = Path(output_dir)
        self._predictor: Callable[[str], float] | None = None

    def _predictor_fn(self) -> Callable[[str], float] | None:
        if self._predictor is None:
            self._predictor = _load_lgbm_predictor(self.output_dir)
        return self._predictor

    def execute_buy(
        self,
        plan: CryptoOrderPlan,
        *,
        execute: bool,
        total_portfolio_krw: float | None = None,
        runner_state: dict[str, Any] | None = None,
    ) -> BuyExecutionResult:
        cfg = self.cfg
        p_win = _resolve_win_probability(plan, default=cfg.buy_min_win_prob, predictor=self._predictor_fn())
        if p_win < cfg.buy_min_win_prob:
            _reason = f"ML 승률 {p_win*100:.0f}% < 기준 {cfg.buy_min_win_prob*100:.0f}%"
            _log_order_failure(self.output_dir, plan, stage="ml_winprob", reasons=[_reason],
                               krw=float(plan.krw_amount or 0))
            return BuyExecutionResult(
                success=False,
                reasons=[f"P(win)={p_win:.3f} < {cfg.buy_min_win_prob}"],
                win_probability=p_win,
            )

        # ── 악재(이벤트) 사전 차단 게이트 — LLM 뉴스 감성 캐시 기반 ──
        _news_reason = _news_event_block(plan.market)
        if _news_reason:
            _log_order_failure(self.output_dir, plan, stage="news_risk", reasons=[_news_reason],
                               krw=float(plan.krw_amount or 0))
            return BuyExecutionResult(success=False, reasons=[_news_reason], win_probability=p_win)

        if total_portfolio_krw is None:
            try:
                avail = float(self.broker.get_krw_available())
            except Exception:
                avail = 0.0
            try:
                hold = sum(max(0.0, float(h.valuation_krw or 0)) for h in self.broker.get_crypto_holdings())
            except Exception:
                hold = 0.0
            total_portfolio_krw = avail + hold

        kelly_krw, k_frac = kelly_order_krw(
            float(total_portfolio_krw or 0),
            p_win,
            take_profit_pct=cfg.take_profit_pct,
            stop_loss_pct=cfg.stop_loss_pct,
            max_fraction=cfg.kelly_max_fraction,
            min_fraction=cfg.kelly_min_fraction,
        )
        base_krw = min(float(plan.krw_amount), kelly_krw) if kelly_krw > 0 else float(plan.krw_amount)

        ob = check_orderbook_for_buy(
            self.broker,
            plan.market,
            max_spread_pct=cfg.max_spread_pct,
            min_bid_ask_ratio=cfg.min_bid_ask_volume_ratio,
            levels=cfg.orderbook_levels,
        )
        if not ob.allowed:
            _log_order_failure(self.output_dir, plan, stage="gate", reasons=list(ob.reasons), krw=base_krw)
            return BuyExecutionResult(
                success=False,
                reasons=list(ob.reasons),
                orderbook=ob,
                win_probability=p_win,
                kelly_fraction=k_frac,
            )

        limit_px = compute_entry_limit_price(ob, use_mid=cfg.use_mid_or_bid_plus_tick)
        if limit_px <= 0:
            return BuyExecutionResult(success=False, reasons=["지정가 계산 실패"], orderbook=ob)

        eq = evaluate_pre_trade(
            self.broker,
            market=plan.market,
            side="buy",
            order_krw=base_krw,
            limit_price=limit_px,
            take_profit_pct=float(plan.take_profit_pct or cfg.take_profit_pct),
            stop_loss_pct=float(plan.stop_loss_pct or cfg.stop_loss_pct),
        )
        if should_block_entry_by_execution_quality(eq):
            _log_order_failure(self.output_dir, plan, stage="quality", reasons=list(eq.reasons), krw=base_krw)
            return BuyExecutionResult(
                success=False,
                reasons=list(eq.reasons),
                orderbook=ob,
                win_probability=p_win,
                kelly_fraction=k_frac,
            )

        krw = float(eq.effective_order_krw)
        steps: list[dict[str, Any]] = []
        last_order: UpbitOrderResult | None = None
        attempts = 1 + int(cfg.limit_retry_max)

        for attempt in range(attempts):
            if attempt > 0:
                ob = check_orderbook_for_buy(
                    self.broker,
                    plan.market,
                    max_spread_pct=cfg.max_spread_pct,
                    min_bid_ask_ratio=cfg.min_bid_ask_volume_ratio,
                    levels=cfg.orderbook_levels,
                )
                limit_px = compute_entry_limit_price(ob, use_mid=cfg.use_mid_or_bid_plus_tick)
                if not ob.allowed:
                    break

            order, step = place_limit_with_timeout(
                self.broker,
                market=plan.market,
                side="buy",
                limit_price=limit_px,
                krw_amount=krw,
                execute=execute,
                timeout_sec=cfg.limit_timeout_sec,
                poll_sec=cfg.limit_poll_sec,
            )
            step["attempt"] = attempt
            steps.append(step)
            last_order = order

            if not execute or not order.uuid or self.broker.config.dry_run:
                if runner_state is not None and execute:
                    record_position_entry(runner_state, market=plan.market, entry_price=limit_px)
                return BuyExecutionResult(
                    success=True,
                    order=order,
                    limit_price=limit_px,
                    krw_amount=krw,
                    win_probability=p_win,
                    kelly_fraction=k_frac,
                    orderbook=ob,
                    steps=steps,
                )

            raw = self.broker.get_order(str(order.uuid))
            state = str(raw.get("state", ""))
            executed = float(raw.get("executed_volume", 0) or 0)
            if state == "done" or executed > 0:
                fill_px = float(raw.get("avg_price") or raw.get("price") or limit_px)
                record_fill_slippage_feedback(
                    self.output_dir,
                    market=plan.market,
                    side="buy",
                    limit_price=limit_px,
                    fill_price=fill_px,
                    order_krw=krw,
                )
                if runner_state is not None:
                    record_position_entry(runner_state, market=plan.market, entry_price=fill_px)
                return BuyExecutionResult(
                    success=True,
                    order=order,
                    limit_price=limit_px,
                    krw_amount=krw,
                    win_probability=p_win,
                    kelly_fraction=k_frac,
                    orderbook=ob,
                    steps=steps,
                )

        if execute:
            _log_order_failure(self.output_dir, plan, stage="unfilled",
                               reasons=["미체결 — 타임아웃 후 취소"], krw=krw)
        return BuyExecutionResult(
            success=last_order is not None and not execute,
            order=last_order,
            reasons=["미체결 — 타임아웃 후 취소"] if execute else [],
            limit_price=limit_px,
            krw_amount=krw,
            win_probability=p_win,
            kelly_fraction=k_frac,
            orderbook=ob,
            steps=steps,
        )

    def execute_sell(
        self,
        plan: CryptoOrderPlan,
        *,
        execute: bool,
        volume_fraction: float = 1.0,
    ) -> UpbitOrderResult:
        vol = float(plan.volume or 0) * max(0.0, min(1.0, float(volume_fraction)))
        if vol <= 0:
            raise ValueError("SELL volume must be > 0")
        limit_px = float(plan.limit_price)
        order, _step = place_limit_with_timeout(
            self.broker,
            market=plan.market,
            side="sell",
            limit_price=limit_px,
            volume=vol,
            execute=execute,
            timeout_sec=self.cfg.limit_timeout_sec,
            poll_sec=self.cfg.limit_poll_sec,
        )
        order.volume = vol
        order.krw_amount = vol * limit_px
        return order

    def evaluate_exit(
        self,
        holding: Any,
        *,
        runner_state: dict[str, Any] | None = None,
        static_trigger: str | None = None,
        allow_rule_fallback: bool = False,
    ) -> SellExitDecision | None:
        cfg = self.cfg
        market = str(holding.market).upper()
        cur = float(holding.current_price or 0)
        pnl = float(holding.pnl_pct or 0)
        if cur <= 0:
            return None

        positions = load_execution_positions(runner_state or {})
        pos = positions.get(market)
        if pos is None and runner_state is not None:
            open_ts = (runner_state.get("position_open_ts_by_market") or {}).get(market)
            if open_ts:
                pos = PositionExecutionState(
                    peak_price=max(cur, float(holding.avg_buy_price or 0)),
                    entry_ts=str(open_ts),
                )
                positions[market] = pos
                save_execution_positions(runner_state, positions)

        if runner_state is not None:
            peak = update_peak_price(runner_state, market=market, current_price=cur)
        else:
            peak = max(float(pos.peak_price if pos else 0), cur)

        now = now_kst()
        if pos and pos.entry_ts:
            try:
                entry_dt = parse_datetime_with_default_tz(pos.entry_ts)
                held_min = (now - entry_dt).total_seconds() / 60.0
            except Exception:
                held_min = 0.0
        else:
            held_min = 0.0

        need_ai = True
        if pos and pos.last_ai_check_ts:
            try:
                last_ai = parse_datetime_with_default_tz(pos.last_ai_check_ts)
                need_ai = (now - last_ai).total_seconds() >= cfg.ai_recheck_interval_sec
            except Exception:
                need_ai = True

        p_win: float | None = None
        if need_ai:
            pred = self._predictor_fn()
            if pred is not None:
                try:
                    p_win = pred(market)
                except Exception:
                    p_win = None
            if runner_state is not None and pos is not None:
                pos.last_ai_check_ts = now_kst_iso()
                positions[market] = pos
                save_execution_positions(runner_state, positions)

        # AI 승률 기반 즉시청산은 최소보유시간(min_hold) 이후에만 — 사자마자
        # 다음 틱에 'AI 승률 낮음'으로 손절하는 churn 방지. 가격기반 손절/트레일은 아래에서 별도.
        _min_hold = float(_CRYPTO.min_hold_minutes_before_sell)
        _ai_sell_ok = held_min >= _min_hold
        if p_win is not None and p_win < cfg.sell_ai_stop_prob and _ai_sell_ok:
            exit_px = cur
            try:
                ob = self.broker.get_orderbook(market, levels=1)
                units = ob.get("orderbook_units") or []
                if units:
                    bid = float(units[0].get("bid_price") or 0)
                    if bid > 0:
                        exit_px = bid
            except Exception:
                pass
            return SellExitDecision(
                market=market,
                reason="ai_stop",
                volume_fraction=float(pos.remaining_fraction if pos else 1.0),
                limit_price=round_crypto_limit_price(exit_px),
                message=f"AI P(win)={p_win:.3f}<{cfg.sell_ai_stop_prob} 즉시 시장가 청산(호가)",
                pnl_pct=pnl,
                win_probability=p_win,
            )

        if peak > 0 and cur < peak * (1.0 - cfg.trailing_stop_pct / 100.0):
            return SellExitDecision(
                market=market,
                reason="trailing_stop",
                volume_fraction=float(pos.remaining_fraction if pos else 1.0),
                limit_price=round_crypto_limit_price(cur),
                message=f"트레일링 -{cfg.trailing_stop_pct}% (고점 {peak:,.0f})",
                pnl_pct=pnl,
                win_probability=p_win,
            )

        if (
            held_min >= cfg.time_stop_minutes
            and abs(pnl) <= cfg.time_stop_max_abs_pnl_pct
            and not (pos and pos.partial_taken)
        ):
            return SellExitDecision(
                market=market,
                reason="time_stop",
                volume_fraction=float(pos.remaining_fraction if pos else 1.0),
                limit_price=round_crypto_limit_price(cur),
                message=f"{cfg.time_stop_minutes:.0f}분 경과·방향 미약 (+/-{cfg.time_stop_max_abs_pnl_pct}% 이내)",
                pnl_pct=pnl,
                win_probability=p_win,
            )

        if pnl >= cfg.partial_tp_pct and pos and not pos.partial_taken:
            return SellExitDecision(
                market=market,
                reason="partial_take_profit",
                volume_fraction=cfg.partial_tp_fraction * float(pos.remaining_fraction),
                limit_price=round_crypto_limit_price(
                    float(holding.avg_buy_price or 0) * (1.0 + cfg.partial_tp_pct / 100.0)
                ),
                message=f"+{cfg.partial_tp_pct}% 부분익절 {cfg.partial_tp_fraction:.0%}",
                pnl_pct=pnl,
                win_probability=p_win,
            )

        if allow_rule_fallback and static_trigger:
            frac = float(pos.remaining_fraction if pos else 1.0)
            from deepsignal.crypto_trading.crypto_sell_pricing import compute_sell_limit_price

            lp = compute_sell_limit_price(
                holding,
                static_trigger,
                take_profit_pct=cfg.take_profit_pct,
                stop_loss_pct=cfg.stop_loss_pct,
            )
            return SellExitDecision(
                market=market,
                reason=static_trigger,  # type: ignore[arg-type]
                volume_fraction=frac,
                limit_price=lp,
                message=f"규칙 매도: {static_trigger}",
                pnl_pct=pnl,
                win_probability=p_win,
            )
        return None


def scan_dynamic_exit_holdings(
    broker: UpbitBroker,
    *,
    runner_state: dict[str, Any] | None = None,
    output_dir: str | Path = "outputs",
    engine_cfg: ExecutionEngineConfig | None = None,
) -> SellExitDecision | None:
    if not execution_engine_enabled():
        return None
    engine = CryptoExecutionEngine(broker, cfg=engine_cfg, output_dir=output_dir)
    best: SellExitDecision | None = None
    priority = {
        "ai_stop": 5,
        "trailing_stop": 4,
        "time_stop": 3,
        "partial_take_profit": 2,
    }
    state = runner_state if runner_state is not None else {}
    for h in broker.get_crypto_holdings():
        dec = engine.evaluate_exit(
            h,
            runner_state=state,
            static_trigger=None,
            allow_rule_fallback=False,
        )
        if dec is None:
            continue
        if best is None or priority.get(dec.reason, 0) > priority.get(best.reason, 0):
            best = dec
    return best
