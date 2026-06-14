"""24h crypto auto-runner: plan → Telegram approval → execute on approve."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from deepsignal.crypto_trading.crypto_order_plan import (
    build_plan_from_recommendation,
    save_crypto_plan,
)
from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig
from deepsignal.crypto_trading.crypto_recommendation import build_daily_crypto_recommendation
from deepsignal.crypto_trading.crypto_telegram_flow import (
    create_crypto_approval_request,
    load_crypto_telegram_config_from_env,
    poll_crypto_telegram_until_done,
)
from deepsignal.crypto_trading.upbit_broker import UpbitBroker
from deepsignal.live_trading.inactive_auto_execute import (
    execute_crypto_plan_inactive_auto,
    try_execute_pending_crypto_in_inactive_window,
)
from deepsignal.live_trading.operator_inactive_window import is_inactive_auto_execute_active
from deepsignal.live_trading.time_utils import now_kst, now_kst_iso
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto

STATE_FILE = "CRYPTO_AUTO_RUNNER_STATE.json"


@dataclass
class CryptoAutoRunnerConfig:
    output_dir: str = "outputs"
    max_order_value: float = 0.0
    interval_minutes: float = 1.0
    max_orders_per_day: int = 0
    take_profit_pct: float = _CRYPTO.take_profit_pct
    stop_loss_pct: float = _CRYPTO.stop_loss_pct
    take_profit_buffer_pct: float = _CRYPTO.take_profit_buffer_pct
    stop_loss_buffer_pct: float = _CRYPTO.stop_loss_buffer_pct
    min_volume_ratio: float = _CRYPTO.min_volume_ratio
    send_telegram: bool = True
    poll_telegram: bool = True
    wait_fill_seconds: float = 0.0
    fill_poll_interval: float = 3.0
    network: bool = False
    menu_poll_seconds: float = 5.0
    stream_stale_alert: bool = True
    crypto_universe: str = _CRYPTO.market_universe
    max_buy_scan_markets: int = _CRYPTO.max_buy_scan_markets
    prefer_non_holding_buy: bool = _CRYPTO.prefer_non_holding_buy
    rebuy_cooldown_minutes: int = _CRYPTO.rebuy_cooldown_minutes
    max_distinct_buy_markets_per_day: int = _CRYPTO.max_distinct_buy_markets_per_day
    max_buy_krw_per_day: float = _CRYPTO.max_buy_krw_per_day


def _state_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / STATE_FILE


# 같은 프로세스의 여러 스레드(analysis/execution/order-mgmt/pyramiding)가
# 동일 상태파일을 갱신하므로 직렬화한다. RLock으로 transaction 중첩 save를 허용.
_STATE_LOCK = threading.RLock()


def load_runner_state(output_dir: str | Path) -> dict[str, Any]:
    path = _state_path(output_dir)
    with _STATE_LOCK:
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


def save_runner_state(output_dir: str | Path, state: dict[str, Any]) -> None:
    path = _state_path(output_dir)
    with _STATE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 원자적 쓰기: 임시파일에 쓰고 os.replace로 교체해 torn-write(부분 저장 →
        # 다음 load 시 {} 폴백으로 일일 카운터 리셋·한도 해제)를 방지한다.
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            tmp.write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


@contextmanager
def runner_state_transaction(output_dir: str | Path) -> Iterator[dict[str, Any]]:
    """load→수정→save를 락으로 감싸 read-modify-write 경합(카운터 유실)을 막는다.

    사용: ``with runner_state_transaction(out) as st: st["orders_today"] += 1``
    """
    with _STATE_LOCK:
        state = load_runner_state(output_dir)
        yield state
        save_runner_state(output_dir, state)


def _today_key() -> str:
    return now_kst().date().isoformat()


def _state_today(state: dict[str, Any]) -> dict[str, Any]:
    from deepsignal.crypto_trading.crypto_overtrading_guards import prune_state_for_new_day

    prune_state_for_new_day(state, _today_key())
    return state


def _portfolio_total_krw(broker: UpbitBroker) -> float:
    try:
        avail = float(broker.get_krw_available())
    except Exception:
        avail = 0.0
    try:
        hold_val = sum(max(0.0, float(h.valuation_krw or 0.0)) for h in broker.get_crypto_holdings())
    except Exception:
        hold_val = 0.0
    return max(avail + hold_val, avail)


def _ensure_scalping_active_thresholds(output_dir: str | Path) -> None:
    """Rollback 펀드형 outcome 튜닝(TP 10% / vol 0.85)이 남아 있으면 단타 기본값으로 복구."""
    if not bool(_CRYPTO.scalping_mode):
        return
    from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import (
        load_active_crypto_thresholds,
        reset_scalping_active_thresholds,
    )

    tuned = load_active_crypto_thresholds(output_dir)
    cap = float(_CRYPTO.outcome_tune_max_volume_ratio)
    if tuned is None:
        try:
            reset_scalping_active_thresholds(output_dir)
        except Exception:
            pass
        return
    if (
        float(tuned.min_volume_ratio) > cap + 1e-6
        or float(tuned.take_profit_pct) > float(_CRYPTO.take_profit_pct) + 0.5
        or float(tuned.stop_loss_pct) < float(_CRYPTO.stop_loss_pct) - 0.5
    ):
        try:
            reset_scalping_active_thresholds(output_dir)
        except Exception:
            pass


def _persist_trade_state(output_dir: str | Path, plan: Any) -> None:
    from deepsignal.crypto_trading.crypto_overtrading_guards import record_buy_in_state, record_sell_in_state

    st = load_runner_state(output_dir)
    _state_today(st)
    side = str(getattr(plan, "side", "") or "").lower()
    market = str(getattr(plan, "market", "") or "").strip().upper()
    st["last_order_date"] = _today_key()
    st["orders_today"] = int(st.get("orders_today", 0) or 0) + 1
    st["last_market"] = market
    if side == "buy":
        krw = float(getattr(plan, "krw_amount", 0) or 0)
        record_buy_in_state(st, market=market, krw_amount=krw)
        st["buy_krw_today"] = float(st.get("buy_krw_today", 0.0) or 0.0) + krw
        bm = {str(m).upper() for m in (st.get("buy_markets_today") or []) if str(m).strip()}
        if market:
            bm.add(market)
        st["buy_markets_today"] = sorted(bm)
    elif side == "sell":
        krw = float(getattr(plan, "krw_amount", 0) or 0)
        st["sell_krw_today"] = float(st.get("sell_krw_today", 0.0) or 0.0) + krw
        record_sell_in_state(st, market=market)
    save_runner_state(output_dir, st)


def can_place_order_today(state: dict[str, Any], *, max_orders_per_day: int) -> bool:
    _state_today(state)
    if int(max_orders_per_day) <= 0:
        return True
    if state.get("last_order_date") != _today_key():
        return True
    return int(state.get("orders_today", 0) or 0) < int(max_orders_per_day)


def run_crypto_auto_tick(
    broker: UpbitBroker,
    cfg: CryptoAutoRunnerConfig,
) -> dict[str, Any]:
    state = load_runner_state(cfg.output_dir)
    result: dict[str, Any] = {"tick_at": now_kst_iso(), "action": "idle"}
    from deepsignal.crypto_trading.crypto_live_data import check_binance_live_state

    live_status = check_binance_live_state(cfg.output_dir)
    result["binance_live_state"] = live_status.to_dict()
    from deepsignal.crypto_trading.crypto_paper_state import touch_paper_state

    paper_st = touch_paper_state(cfg.output_dir)
    if paper_st is not None:
        result["paper_state"] = paper_st.to_dict()
        state["paper_state"] = paper_st.to_dict()
    try:
        return _run_crypto_auto_tick_body(broker, cfg, state, result)
    finally:
        _post_tick_crypto_outcomes(broker, cfg, state)
        # _persist_trade_state()가 last_buy_by_market 등 오버트레이딩 가드 필드를
        # 디스크에 기록했을 수 있으므로 해당 필드만 디스크 버전으로 덮어쓴 뒤 저장한다.
        # orders_today / buy_krw_today 등 카운터는 in-memory state(tick body에서 갱신)를 유지.
        _GUARD_PERSIST_KEYS = (
            "last_buy_by_market",
            "buy_timestamps_by_market",
            "buy_krw_by_market_today",
            "buy_count_by_market_today",
            "last_sell_by_market",
            "position_open_ts_by_market",
        )
        try:
            fresh = load_runner_state(cfg.output_dir)
            for k in _GUARD_PERSIST_KEYS:
                if k in fresh:
                    state[k] = fresh[k]
        except Exception:
            pass
        save_runner_state(cfg.output_dir, state)


def _run_crypto_auto_tick_body(
    broker: UpbitBroker,
    cfg: CryptoAutoRunnerConfig,
    state: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    _state_today(state)
    from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import (
        apply_active_thresholds_to_runner,
        crypto_outcomes_db_path,
        run_tune_crypto_thresholds_from_outcomes,
    )

    from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import reset_scalping_active_thresholds

    _ensure_scalping_active_thresholds(cfg.output_dir)
    apply_active_thresholds_to_runner(cfg, cfg.output_dir)
    odb = crypto_outcomes_db_path(cfg.output_dir)
    if (
        bool(_CRYPTO.outcome_tune_enabled)
        and odb.is_file()
        and state.get("last_threshold_tune_date") != _today_key()
    ):
        try:
            if bool(_CRYPTO.scalping_mode):
                reset_scalping_active_thresholds(cfg.output_dir)
            else:
                tuned = run_tune_crypto_thresholds_from_outcomes(
                    odb, output_dir=cfg.output_dir, lookback_days=60
                )
                state["last_threshold_tune"] = tuned.to_dict()
            apply_active_thresholds_to_runner(cfg, cfg.output_dir)
            state["last_threshold_tune_date"] = _today_key()
        except Exception:
            pass

    from deepsignal.config.settings import load_settings
    from deepsignal.crypto_trading.crypto_signal_scorer import load_crypto_macro_context
    from deepsignal.crypto_trading.crypto_position_sizing import (
        apply_runtime_sizing_to_runner,
        resolve_crypto_runtime_sizing,
        save_active_sizing_snapshot,
    )
    from deepsignal.storage.database import init_database

    macro_db = str(init_database(load_settings().db_path))
    macro_ctx = load_crypto_macro_context(macro_db)
    regime = str(macro_ctx.get("market_regime") or "neutral")
    sizing = resolve_crypto_runtime_sizing(
        broker,
        output_dir=cfg.output_dir,
        macro_regime=regime,
        hard_cap_order_krw=float(cfg.max_order_value or 0),
        hard_cap_orders_per_day=int(cfg.max_orders_per_day or 0),
    )
    apply_runtime_sizing_to_runner(cfg, sizing)
    save_active_sizing_snapshot(cfg.output_dir, sizing)
    result["sizing"] = sizing.to_dict()

    buy_daily_limit = not can_place_order_today(state, max_orders_per_day=cfg.max_orders_per_day)
    # 공격성 다이얼 오버라이드: 일일 매수금액·종목수 한도 (9~10단계는 완화/해제, 0=무제한)
    _cap_krw = float(cfg.max_buy_krw_per_day or 0.0)
    _cap_mkts = int(cfg.max_distinct_buy_markets_per_day or 0)
    try:
        _ov_krw = os.environ.get("CRYPTO_MAX_BUY_KRW_PER_DAY", "").strip()
        if _ov_krw != "":
            _cap_krw = float(_ov_krw)
        _ov_mkt = os.environ.get("CRYPTO_MAX_DISTINCT_BUY_MARKETS_PER_DAY", "").strip()
        if _ov_mkt != "":
            _cap_mkts = int(float(_ov_mkt))
    except ValueError:
        pass
    buy_krw_today = float(state.get("buy_krw_today", 0.0) or 0.0)
    if _cap_krw > 0 and buy_krw_today >= _cap_krw:
        buy_daily_limit = True
        result["daily_limit_reason"] = "buy_krw_cap_reached"
    buy_markets_today = {
        str(m).upper()
        for m in (state.get("buy_markets_today") or [])
        if str(m).strip()
    }
    if _cap_mkts > 0 and len(buy_markets_today) >= _cap_mkts:
        buy_daily_limit = True
        result["daily_limit_reason"] = "distinct_buy_markets_cap_reached"

    # ── 전역 킬스위치(TRADING_HALT) + 일일 실현손실 한도 (#3) ────────────
    # 매도/청산은 계속하되 신규 매수만 차단한다.
    from deepsignal.risk.trading_halt import check_crypto_buy_halt

    halted, halt_reason = check_crypto_buy_halt(cfg.output_dir)
    if halted:
        buy_daily_limit = True
        result["halt_active"] = True
        result["halt_reason"] = halt_reason

    # ── 실시간 스트림 stale 시 신규매수 차단 (#7) ───────────────────────
    # live_state가 오래되거나 없으면 추천이 Upbit REST 폴백(낡은 거래량비율)으로
    # 매수할 수 있으므로, 신규 매수만 차단한다(매도/청산은 계속). env로 비활성 가능.
    _block_on_stale = os.environ.get(
        "CRYPTO_BLOCK_BUY_ON_STALE_STREAM", "true"
    ).strip().lower() in ("1", "true", "yes")
    if _block_on_stale and bool((result.get("binance_live_state") or {}).get("stale")):
        buy_daily_limit = True
        result["stale_block"] = True
        result.setdefault("halt_reason", "binance 스트림 stale — 신규매수 차단")

    # ── EDGE_GATE: 검증된 엣지가 있는 전략만 신규매수 허용 (#9) ──────────
    # 엣지 모니터가 crypto_scalp_5m을 deploy=true로 올린 날에만 매수가 열린다.
    # 기본 닫힘(엣지 미검증 → 차단). env DEEPSIGNAL_ENFORCE_EDGE_GATE=false로 무시 가능.
    from deepsignal.risk.edge_gate import edge_gate_allows_buy, strategy_for_live

    eg_ok, eg_reason = edge_gate_allows_buy(cfg.output_dir, strategy_for_live("crypto"))
    result["edge_gate"] = {"allows_buy": eg_ok, "reason": eg_reason}
    if not eg_ok:
        buy_daily_limit = True
        result.setdefault("halt_reason", eg_reason)

    tg_cfg = load_crypto_telegram_config_from_env(output_dir=cfg.output_dir)
    tg_cfg.send = bool(cfg.send_telegram)
    tg_cfg.max_orders_per_day = cfg.max_orders_per_day
    tg_cfg.wait_fill_seconds = float(cfg.wait_fill_seconds or 0)
    tg_cfg.fill_poll_interval = float(cfg.fill_poll_interval or 3.0)

    try:
        pending_crypto = try_execute_pending_crypto_in_inactive_window(
            broker,
            output_dir=cfg.output_dir,
            tg_cfg=tg_cfg,
            wait_fill_seconds=cfg.wait_fill_seconds,
            fill_poll_interval=cfg.fill_poll_interval,
        )
    except Exception as exc:
        pending_crypto = {"status": "ERROR", "reason": str(exc)}
    if pending_crypto is not None:
        result["inactive_pending_crypto"] = pending_crypto
        if pending_crypto.get("status") == "APPROVED":
            result["action"] = "inactive_auto_executed_pending"
            state["last_order_date"] = _today_key()
            state["orders_today"] = int(state.get("orders_today", 0) or 0) + 1
            save_runner_state(cfg.output_dir, state)
            return result
        if pending_crypto.get("status") == "REJECTED":
            result["action"] = "inactive_pending_rejected"
            return result

    from deepsignal.crypto_trading.crypto_universe import CryptoUniverseConfig

    buy_quality = CryptoBuyQualityConfig(min_volume_ratio=float(cfg.min_volume_ratio))
    universe_cfg = CryptoUniverseConfig(
        universe=str(cfg.crypto_universe),
        max_buy_scan_markets=int(cfg.max_buy_scan_markets),
    )
    from deepsignal.crypto_trading.crypto_overtrading_guards import (
        OvertradingGuardConfig,
        check_buy_allowed,
        excluded_markets_for_buy,
    )

    guard_cfg = OvertradingGuardConfig(rebuy_cooldown_minutes=int(cfg.rebuy_cooldown_minutes))
    excluded = excluded_markets_for_buy(state, guard_cfg)
    if excluded:
        result["overtrading_excluded_markets"] = list(excluded)
    # ── SELL 체크: 매수 한도와 무관하게 항상 먼저 실행 ─────────────────
    # 포지션 보유 중이면 TP/SL 도달 여부를 항상 모니터링해야 한다.
    # 기존 코드는 buy_daily_limit=True일 때만 매도 체크 → 보유 중에도
    # 매수 한도가 남아있으면 TP/SL이 무시되는 버그가 있었음.
    from deepsignal.crypto_trading.crypto_recommendation import build_sell_recommendation

    sell_rec = build_sell_recommendation(
        broker,
        take_profit_pct=cfg.take_profit_pct,
        stop_loss_pct=cfg.stop_loss_pct,
        take_profit_buffer_pct=cfg.take_profit_buffer_pct,
        stop_loss_buffer_pct=cfg.stop_loss_buffer_pct,
        macro_db_path=macro_db,
        runner_state=state,
    )

    if sell_rec is not None:
        # 매도 신호 발생 → 매수 한도와 무관하게 즉시 처리
        rec = sell_rec
    elif buy_daily_limit:
        # 매도 없음 + 매수 한도 도달 → 이 틱은 패스
        result["action"] = "daily_buy_limit_no_sell"
        return result
    else:
        # 매도 없음 + 매수 가능 → 매수 신호 탐색
        rec = build_daily_crypto_recommendation(
            broker,
            take_profit_pct=cfg.take_profit_pct,
            stop_loss_pct=cfg.stop_loss_pct,
            take_profit_buffer_pct=cfg.take_profit_buffer_pct,
            stop_loss_buffer_pct=cfg.stop_loss_buffer_pct,
            max_order_value=float(cfg.max_order_value),
            exclude_markets=excluded,
            prefer_non_holding_buy=bool(cfg.prefer_non_holding_buy),
            buy_quality=buy_quality,
            universe_config=universe_cfg,
            macro_db_path=macro_db,
            output_dir=cfg.output_dir,
            runner_state=state,
        )

    if rec is None:
        from deepsignal.crypto_trading.crypto_recommendation_diagnostics import (
            build_crypto_recommendation_diagnostics,
            save_crypto_no_recommendation_artifacts,
        )

        diagnostics = build_crypto_recommendation_diagnostics(
            broker,
            take_profit_pct=cfg.take_profit_pct,
            stop_loss_pct=cfg.stop_loss_pct,
            take_profit_buffer_pct=cfg.take_profit_buffer_pct,
            stop_loss_buffer_pct=cfg.stop_loss_buffer_pct,
            max_order_value=cfg.max_order_value,
            buy_quality=buy_quality,
            universe_config=universe_cfg,
            macro_db_path=macro_db,
            output_dir=cfg.output_dir,
        )
        jpath, mpath = save_crypto_no_recommendation_artifacts(cfg.output_dir, diagnostics)
        result["action"] = "no_recommendation"
        result["plan_json"] = jpath.as_posix()
        result["plan_md"] = mpath.as_posix()
        result["diagnostics"] = diagnostics.to_dict()
        if excluded:
            result["diagnostics"]["cooldown_excluded_markets"] = list(excluded)
        return result

    plan = build_plan_from_recommendation(rec)
    if rec.side == "buy":
        from deepsignal.crypto_trading.crypto_execution_quality import (
            evaluate_pre_trade,
            should_block_entry_by_execution_quality,
        )

        eq = evaluate_pre_trade(
            broker,
            market=plan.market,
            side="buy",
            order_krw=float(plan.krw_amount),
            limit_price=float(plan.limit_price or 0) or None,
            take_profit_pct=float(cfg.take_profit_pct),
            stop_loss_pct=float(cfg.stop_loss_pct),
        )
        if should_block_entry_by_execution_quality(eq):
            result["action"] = "blocked_execution_quality"
            result["blocked_market"] = plan.market
            result["execution_quality"] = eq.to_dict()
            return result
        plan.krw_amount = float(eq.effective_order_krw)
        if eq.limit_price or plan.limit_price:
            plan.limit_price = float(eq.limit_price or plan.limit_price)
        total_pf = _portfolio_total_krw(broker)
        ok_ot, ot_reason = check_buy_allowed(
            state,
            market=plan.market,
            order_krw=float(plan.krw_amount),
            total_portfolio_krw=total_pf,
            cfg=guard_cfg,
        )
        if not ok_ot:
            result["action"] = "blocked_overtrading"
            result["blocked_market"] = plan.market
            result["overtrading_reason"] = ot_reason
            return result
        if (
            _cap_mkts > 0
            and rec.market.upper() not in buy_markets_today
            and len(buy_markets_today) >= _cap_mkts
        ):
            result["action"] = "blocked_distinct_buy_markets_cap"
            result["blocked_market"] = rec.market
            return result
        if _cap_krw > 0 and (
            buy_krw_today + float(plan.krw_amount)
        ) > _cap_krw:
            result["action"] = "blocked_buy_krw_cap"
            result["blocked_market"] = rec.market
            result["blocked_amount"] = float(plan.krw_amount)
            return result
    json_path, md_path = save_crypto_plan(cfg.output_dir, plan)
    result["plan_json"] = json_path.as_posix()
    result["plan_md"] = md_path.as_posix()

    from deepsignal.crypto_trading.crypto_recommendation_outcomes import record_crypto_recommendation

    outcome_id = record_crypto_recommendation(plan, outcomes_db=cfg.output_dir, rec=rec)
    result["outcome_id"] = outcome_id

    from deepsignal.crypto_trading.crypto_auto_execute_policy import should_auto_execute_crypto_on_runner_tick

    if should_auto_execute_crypto_on_runner_tick():
        audit = execute_crypto_plan_inactive_auto(
            broker,
            plan,
            tg_cfg=tg_cfg,
            output_dir=cfg.output_dir,
            wait_fill_seconds=cfg.wait_fill_seconds,
            fill_poll_interval=cfg.fill_poll_interval,
            outcome_id=outcome_id,
        )
        result["inactive_crypto_audit"] = audit
        result["order_result"] = audit.get("result")
        result["outcome_tracking"] = audit.get("outcome_tracking")
        result["action"] = "auto_executed_no_approval"
        state["last_order_date"] = _today_key()
        state["orders_today"] = int(state.get("orders_today", 0) or 0) + 1
        state["last_market"] = plan.market
        if plan.side.lower() == "buy":
            state["buy_krw_today"] = float(state.get("buy_krw_today", 0.0) or 0.0) + float(plan.krw_amount or 0.0)
            bm = {
                str(m).upper()
                for m in (state.get("buy_markets_today") or [])
                if str(m).strip()
            }
            bm.add(plan.market.upper())
            state["buy_markets_today"] = sorted(bm)
        save_runner_state(cfg.output_dir, state)
        _persist_trade_state(cfg.output_dir, plan)
        return result

    req = create_crypto_approval_request(plan, cfg=tg_cfg, plan_path=json_path)
    result["approval"] = req.to_dict()
    result["action"] = "approval_sent"

    if cfg.poll_telegram and tg_cfg.bot_token and not should_auto_execute_crypto_on_runner_tick():
        poll_out = poll_crypto_telegram_until_done(
            tg_cfg,
            broker,
            max_wait_seconds=cfg.interval_minutes * 60,
            runner_cfg=cfg,
        )
        result["poll"] = poll_out
        if poll_out.get("status") == "APPROVED":
            state["last_order_date"] = _today_key()
            state["orders_today"] = int(state.get("orders_today", 0) or 0) + 1
            state["last_market"] = plan.market
            if plan.side.lower() == "buy":
                state["buy_krw_today"] = float(state.get("buy_krw_today", 0.0) or 0.0) + float(plan.krw_amount or 0.0)
                bm = {
                    str(m).upper()
                    for m in (state.get("buy_markets_today") or [])
                    if str(m).strip()
                }
                bm.add(plan.market.upper())
                state["buy_markets_today"] = sorted(bm)
            save_runner_state(cfg.output_dir, state)
            _persist_trade_state(cfg.output_dir, plan)
            result["action"] = "approved"
            if isinstance(poll_out, dict):
                ot = poll_out.get("outcome_tracking")
                ff = poll_out.get("fill_follow_up")
                if not ot and isinstance(ff, dict):
                    ot = ff.get("outcome_tracking")
                if ot:
                    result["outcome_tracking"] = ot

    return result


def _post_tick_crypto_outcomes(
    broker: UpbitBroker,
    cfg: CryptoAutoRunnerConfig,
    state: dict[str, Any],
) -> None:
    from deepsignal.crypto_trading.crypto_recommendation_outcomes import (
        maybe_send_crypto_daily_summary,
        refresh_crypto_outcomes,
    )
    from deepsignal.crypto_trading.crypto_telegram_flow import load_crypto_telegram_config_from_env

    try:
        refresh_crypto_outcomes(broker, cfg.output_dir, lookback_days=30)
    except Exception:
        pass
    tg = load_crypto_telegram_config_from_env(output_dir=cfg.output_dir)
    tg.send = bool(cfg.send_telegram)
    summary = maybe_send_crypto_daily_summary(
        broker, tg, outcomes_db=cfg.output_dir, runner_state=state
    )
    if summary:
        state["last_daily_summary_date"] = _today_key()
    # 실제 save는 run_crypto_auto_tick() finally 블록에서 수행


def poll_telegram_menu_fast(
    broker: UpbitBroker,
    cfg: CryptoAutoRunnerConfig,
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Short-interval Telegram poll (menu + approval callbacks)."""
    from deepsignal.config.settings import load_settings
    from deepsignal.crypto_trading.crypto_telegram_flow import load_crypto_telegram_config_from_env
    from deepsignal.crypto_trading.crypto_telegram_menu import poll_telegram_updates_once
    from deepsignal.storage.database import init_database

    tg = load_crypto_telegram_config_from_env(output_dir=cfg.output_dir)
    if not tg.bot_token or not tg.allowed_chat_id:
        return None
    db_path = str(init_database(load_settings().db_path))
    try:
        summary = poll_telegram_updates_once(
            tg,
            broker,
            runner_cfg=cfg,
            db_path=db_path,
            network=bool(cfg.network),
            process_approvals=True,
        )
    except Exception as exc:
        from deepsignal.crypto_trading.crypto_telegram_menu import log_menu_event

        log_menu_event("menu poll error", error=str(exc))
        return None
    if state is not None and (summary.get("menu") or summary.get("callbacks")):
        state["last_menu_poll"] = {
            "menu_actions": [r.get("action") for r in summary.get("menu") or []],
            "callback_statuses": [r.get("status") for r in summary.get("callbacks") or []],
            "updates": summary.get("updates"),
        }
    return summary

