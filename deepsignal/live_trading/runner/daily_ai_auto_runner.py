"""Daily AI auto trading loop — plan, Telegram approval, execute, report."""

from __future__ import annotations

import json
import signal
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Callable

from deepsignal.live_trading.daily_ai_trading_workflow import (
    build_daily_ai_trade_report,
    run_daily_ai_trade_plan,
)
from deepsignal.live_trading.telegram_approval import (
    APPROVAL_STATUS_PENDING,
    create_telegram_approval_request,
    load_latest_request,
    load_telegram_config_from_env,
    TelegramApprovalConfig,
)
from deepsignal.live_trading.inactive_auto_execute import (
    execute_kis_plan_inactive_auto,
    notify_inactive_kis_execution,
    try_execute_pending_kis_in_inactive_window,
)
from deepsignal.live_trading.kis_stock_auto_execute_policy import (
    should_notify_kis_plan_with_no_orders,
    should_skip_kis_telegram_approval,
)
from deepsignal.live_trading.operator_inactive_window import is_inactive_auto_execute_active
from deepsignal.live_trading.telegram_auto_execute import (
    format_operator_approval_request_text,
    format_operator_daily_report_text,
    format_operator_execution_result_text,
    format_operator_no_orders_text,
    format_operator_plan_blocked_text,
    poll_telegram_approval_once,
    send_runner_telegram,
    try_resume_approved_execution,
)
from deepsignal.live_trading.time_utils import now_kst, now_kst_iso

STATE_FILENAME = "DAILY_AI_AUTO_RUNNER_STATE.json"


@dataclass
class DailyAIAutoRunnerConfig:
    broker: str = "kis"
    network: bool = True
    output_dir: str = "outputs"
    plan_time: str = "09:05"
    report_time: str = "15:40"
    max_order_value: float = 300_000.0
    max_single_order_value: float = 300_000.0
    max_total_order_value: float = 300_000.0
    max_orders: int = 1
    expires_minutes: int = 420
    poll_interval: float = 3.0
    loop_sleep_seconds: float = 15.0
    timeout_seconds: float = 10.0
    allow_test_plan_order: bool = False
    ignore_safety_block_for_test: bool = False
    plan_runner: Callable[..., Any] | None = None
    report_runner: Callable[..., Any] | None = None


@dataclass
class DailyAIAutoRunnerState:
    started_at: str = ""
    last_plan_date: str | None = None
    last_report_date: str | None = None
    last_plan_order_count: int = 0
    pending_token: str | None = None
    telegram_update_offset: int | None = None
    last_event: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _root(output_dir: str | Path) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_path(output_dir: str | Path) -> Path:
    return _root(output_dir) / STATE_FILENAME


def load_runner_state(output_dir: str | Path) -> DailyAIAutoRunnerState:
    path = _state_path(output_dir)
    if not path.is_file():
        return DailyAIAutoRunnerState(started_at=now_kst_iso())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DailyAIAutoRunnerState(started_at=now_kst_iso())
    if not isinstance(data, dict):
        return DailyAIAutoRunnerState(started_at=now_kst_iso())
    return DailyAIAutoRunnerState(
        started_at=str(data.get("started_at") or now_kst_iso()),
        last_plan_date=data.get("last_plan_date"),
        last_report_date=data.get("last_report_date"),
        last_plan_order_count=int(data.get("last_plan_order_count") or 0),
        pending_token=data.get("pending_token"),
        telegram_update_offset=data.get("telegram_update_offset"),
        last_event=str(data.get("last_event") or ""),
        extra=dict(data.get("extra") or {}),
    )


def save_runner_state(output_dir: str | Path, state: DailyAIAutoRunnerState) -> None:
    path = _state_path(output_dir)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_hhmm(value: str) -> dt_time:
    parts = str(value).strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"invalid time {value!r}, expected HH:MM")
    return dt_time(int(parts[0]), int(parts[1]))


def _today_key(now: datetime | None = None) -> str:
    return (now or now_kst()).date().isoformat()


def _due_scheduled(last_date: str | None, hhmm: str, now: datetime | None = None) -> bool:
    current = now or now_kst()
    if last_date == _today_key(current):
        return False
    return current.time() >= _parse_hhmm(hhmm)


def _pending_approval_state(output_dir: str | Path) -> dict[str, Any]:
    state = load_latest_request(output_dir)
    if not state:
        return {}
    if str(state.get("status") or "") != APPROVAL_STATUS_PENDING:
        return {}
    from deepsignal.live_trading.telegram.approval import _state_expired

    if _state_expired(state):
        return {}
    return state


def _telegram_cfg(runner: DailyAIAutoRunnerConfig, *, send: bool = True) -> TelegramApprovalConfig:
    return load_telegram_config_from_env(
        output_dir=runner.output_dir,
        expires_minutes=int(runner.expires_minutes),
        max_total_order_value=float(runner.max_total_order_value),
        max_single_order_value=float(runner.max_single_order_value),
        max_orders=int(runner.max_orders),
        send=bool(send),
        timeout_seconds=float(runner.timeout_seconds),
    )


def run_morning_plan(
    runner: DailyAIAutoRunnerConfig,
    *,
    db_path: str,
    state: DailyAIAutoRunnerState,
) -> DailyAIAutoRunnerState:
    plan_fn = runner.plan_runner or run_daily_ai_trade_plan
    plan = plan_fn(
        db_path,
        broker=runner.broker,
        network=runner.network,
        output_dir=runner.output_dir,
        max_order_value=runner.max_order_value,
        allow_test_plan_order=runner.allow_test_plan_order,
        ignore_safety_block_for_test=runner.ignore_safety_block_for_test,
        debug_plan=False,
    )
    state.last_plan_date = _today_key()
    state.last_plan_order_count = int(plan.order_count)
    state.last_event = f"plan_done orders={plan.order_count}"
    tg = _telegram_cfg(runner)
    if not tg.bot_token or not tg.allowed_chat_id:
        state.last_event = "plan_done telegram_config_missing"
        return state

    if plan.order_count <= 0:
        if should_notify_kis_plan_with_no_orders():
            send_runner_telegram(text=format_operator_no_orders_text(), config=tg)
        state.pending_token = None
        state.last_event = "plan_done_no_orders"
        return state

    plan_path = plan.latest_order_plan_json
    if should_skip_kis_telegram_approval():
        execution = execute_kis_plan_inactive_auto(
            plan_path,
            db_path=db_path,
            output_dir=runner.output_dir,
            tg_config=tg,
            max_single_order_value=runner.max_single_order_value,
            max_total_order_value=runner.max_total_order_value,
            max_orders=runner.max_orders,
        )
        notify_inactive_kis_execution(execution=execution, plan_path=plan_path, tg_config=tg)
        state.pending_token = None
        from deepsignal.live_trading.kis_stock_auto_execute_policy import is_kis_stock_auto_execute_without_approval

        tag = "kis_stock_auto" if is_kis_stock_auto_execute_without_approval() else "inactive_auto"
        state.last_event = f"{tag}_execute success={execution.success}"
        return state

    request, _, _ = create_telegram_approval_request(plan_path, _telegram_cfg(runner, send=False))
    if request.status != APPROVAL_STATUS_PENDING:
        send_runner_telegram(text=format_operator_plan_blocked_text(request.status), config=tg)
        state.pending_token = None
        return state

    text = format_operator_approval_request_text(request, plan_path=plan_path)
    sent = send_runner_telegram(text=text, config=tg, reply_markup=True, token=request.token)
    if not sent.get("ok"):
        state.last_event = "telegram_send_failed"
        return state
    state.pending_token = request.token
    return state


def run_evening_report(
    runner: DailyAIAutoRunnerConfig,
    *,
    state: DailyAIAutoRunnerState,
) -> DailyAIAutoRunnerState:
    report_fn = runner.report_runner or build_daily_ai_trade_report
    report = report_fn(
        output_dir=runner.output_dir,
        broker=runner.broker,
        network=runner.network,
    )
    state.last_report_date = _today_key()
    state.last_event = "report_done"
    tg = _telegram_cfg(runner)
    if tg.bot_token and tg.allowed_chat_id:
        send_runner_telegram(text=format_operator_daily_report_text(report), config=tg)
    return state


def _tick_auto_sell(
    db_path: str,
    *,
    output_dir: str,
    tg: Any,
    state: DailyAIAutoRunnerState,
) -> DailyAIAutoRunnerState:
    """손절·익절 자동 매도 실행 (KIS_AUTO_SELL_* 플래그가 켜진 경우)."""
    from deepsignal.live_trading.risk.auto_sell_executor import (
        is_any_auto_sell_enabled,
        try_auto_sell_on_risk_alert,
        format_auto_sell_telegram,
    )
    if not is_any_auto_sell_enabled():
        return state

    # Fix-4: KRX 정규 세션(09:00~15:30 KST, 평일)에만 주문 시도
    # 장외 시간에 스냅샷 기준 LIMIT 가격으로 주문이 나가는 것을 방지
    try:
        from deepsignal.live_trading.utils.trading_session import is_trading_session_open
        _session = is_trading_session_open()
        if not _session.is_open:
            import logging as _logging
            _logging.getLogger(__name__).debug(
                "[AutoSell] 장외 시간 — TP/SL 체크 skip: %s", _session.reason
            )
            return state
    except Exception:
        pass  # 세션 체크 실패 시 안전하게 계속 진행

    try:
        sell_results = try_auto_sell_on_risk_alert(db_path, output_dir=output_dir)
        if sell_results:
            state.last_event = (
                f"auto_sell count={len(sell_results)} "
                f"ok={sum(1 for r in sell_results if r.success)}"
            )
            save_runner_state(output_dir, state)
            tg_text = format_auto_sell_telegram(sell_results)
            if tg_text and getattr(tg, "bot_token", None) and getattr(tg, "allowed_chat_id", None):
                send_runner_telegram(text=tg_text, config=tg)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning("auto_sell tick 오류 (비치명적): %s", exc)
    return state


_kr_scan_state: dict[str, float] = {"last_scan": 0.0, "last_replan": 0.0}


def _maybe_intraday_scan_and_replan(runner, *, db_path: str, state):
    """전 시장 급등주 스캔(KIS 순위 API) + 장중 주기 재계획.

    KR_SCANNER_ENABLED(다이얼 L9-10)일 때만. 스캔은 KR_SCANNER_INTERVAL_MIN(기본 3분),
    재계획은 KR_REPLAN_INTERVAL_MIN(기본 5분) 주기 — 새 급등 신호가 있을 때
    run_morning_plan을 재호출해 기존 계획·실행 게이트 경로로 흘려보낸다.
    실패는 전부 비치명(기존 러너 동작 불변).
    """
    import os as _o
    import time as _t
    try:
        from deepsignal.live_trading.kr_market_scanner import run_kr_scan, scanner_enabled, _is_kr_market_hours
        if not scanner_enabled() or not _is_kr_market_hours():
            return state
        now = _t.monotonic()
        scan_iv = max(60.0, float(_o.environ.get("KR_SCANNER_INTERVAL_MIN", "3") or 3) * 60)
        if now - _kr_scan_state["last_scan"] >= scan_iv:
            _kr_scan_state["last_scan"] = now
            res = run_kr_scan(db_path=db_path)
            if res.get("recorded"):
                import logging as _lg
                _lg.getLogger(__name__).info("[KR스캔] %s", res)
                print(f"[KR스캔] 급등주 {res.get('scanned')}건 → 신호 {res.get('recorded')}건: {res.get('top')}", flush=True)
        replan_iv = max(120.0, float(_o.environ.get("KR_REPLAN_INTERVAL_MIN", "5") or 5) * 60)
        if now - _kr_scan_state["last_replan"] >= replan_iv:
            _kr_scan_state["last_replan"] = now
            state = run_morning_plan(runner, db_path=db_path, state=state)
            save_runner_state(runner.output_dir, state)
    except Exception as exc:  # noqa: BLE001
        import logging as _lg
        _lg.getLogger(__name__).warning("[KR스캔] 비치명 오류: %s", exc)
    return state


def tick_runner(
    runner: DailyAIAutoRunnerConfig,
    *,
    db_path: str,
    state: DailyAIAutoRunnerState,
) -> DailyAIAutoRunnerState:
    # 손절·익절 자동 매도 (매 tick — 약 15초마다 체크)
    tg_early = _telegram_cfg(runner)
    state = _tick_auto_sell(db_path, output_dir=runner.output_dir, tg=tg_early, state=state)

    # ── 킬스위치(TRADING_HALT) + EDGE_GATE (#3/#F) ──────────────────────
    # halt 중이거나 국내주식 전략 엣지 미검증이면 신규 매수(plan/resume/inactive/
    # approval)를 전부 건너뛴다. 위 _tick_auto_sell(청산)은 이미 실행됐다.
    from deepsignal.risk.edge_gate import edge_gate_allows_buy, strategy_for_live
    from deepsignal.risk.trading_halt import is_trading_halted

    _halted, _halt_reason = is_trading_halted(runner.output_dir)
    _eg_ok, _eg_reason = edge_gate_allows_buy(runner.output_dir, strategy_for_live("kis_domestic"))
    if _halted or not _eg_ok:
        state.last_event = f"buys_blocked: {_halt_reason if _halted else _eg_reason}"
        if _due_scheduled(state.last_report_date, runner.report_time):
            state = run_evening_report(runner, state=state)
        return state

    if _due_scheduled(state.last_plan_date, runner.plan_time):
        state = run_morning_plan(runner, db_path=db_path, state=state)
        save_runner_state(runner.output_dir, state)

    # ── 전 시장 급등주 스캔 + 장중 재계획 (공격성 L9-10에서 다이얼이 켬) ──
    # KIS 순위 API로 워치리스트 밖 급등주를 신호화하고, 아침 1회 계획의 한계를
    # 깨고 장중에도 새 후보가 계획·실행 경로(기존 게이트 그대로)에 오르게 한다.
    state = _maybe_intraday_scan_and_replan(runner, db_path=db_path, state=state)

    tg = _telegram_cfg(runner)
    resume = try_resume_approved_execution(
        runner.output_dir,
        db_path=db_path,
        config=tg,
        format_result=format_operator_execution_result_text,
    )
    if resume is not None:
        state.last_event = f"resume_execution success={resume.success}"
        state.pending_token = None
        save_runner_state(runner.output_dir, state)

    inactive_exec = try_execute_pending_kis_in_inactive_window(
        runner.output_dir,
        db_path=db_path,
        tg_config=tg,
        max_single_order_value=runner.max_single_order_value,
        max_total_order_value=runner.max_total_order_value,
        max_orders=runner.max_orders,
    )
    if inactive_exec is not None:
        state.last_event = f"inactive_auto_pending success={inactive_exec.success}"
        state.pending_token = None
        save_runner_state(runner.output_dir, state)
        if _due_scheduled(state.last_report_date, runner.report_time):
            state = run_evening_report(runner, state=state)
        return state

    pending = _pending_approval_state(runner.output_dir)
    if pending and not should_skip_kis_telegram_approval():
        state.pending_token = str(pending.get("token") or "") or state.pending_token
        outcome, new_offset = poll_telegram_approval_once(
            runner.output_dir,
            db_path=db_path,
            config=tg,
            poll_interval=runner.poll_interval,
            update_offset=state.telegram_update_offset,
            format_result=format_operator_execution_result_text,
        )
        if new_offset is not None:
            state.telegram_update_offset = new_offset
        if outcome.outcome in {"executed", "execution_failed", "rejected", "expired", "blocked"}:
            state.last_event = outcome.outcome
            if outcome.outcome in {"executed", "execution_failed", "rejected", "expired"}:
                state.pending_token = None
        elif outcome.outcome != "idle":
            state.last_event = outcome.outcome

    if _due_scheduled(state.last_report_date, runner.report_time):
        state = run_evening_report(runner, state=state)

    return state


def _venv_active() -> bool:
    base = getattr(sys, "base_prefix", sys.prefix)
    if base != sys.prefix:
        return True
    if hasattr(sys, "real_prefix"):
        return True
    return "/.venv/" in sys.executable or sys.prefix.rstrip("/").endswith(".venv")


def emit_runner_startup_check() -> bool:
    """Log python/venv/pandas state; return False if required imports fail."""
    lines = [
        "[runner startup]",
        f"python: {sys.argv[0] if sys.argv else 'unknown'}",
        f"sys.executable: {sys.executable}",
        f"venv: {'true' if _venv_active() else 'false'}",
    ]
    ok = True
    for mod in ("pandas", "numpy"):
        try:
            __import__(mod)
            lines.append(f"{mod} import: OK")
        except Exception as exc:
            lines.append(f"{mod} import: FAIL ({exc})")
            ok = False
    text = "\n".join(lines)
    print(text, flush=True)
    if not ok:
        print("[runner startup] FATAL: startup dependency check failed", file=sys.stderr, flush=True)
    return ok


def run_daily_ai_auto_runner_loop(
    runner: DailyAIAutoRunnerConfig,
    *,
    db_path: str,
    max_iterations: int | None = None,
) -> None:
    if not emit_runner_startup_check():
        raise SystemExit(1)

    state = load_runner_state(runner.output_dir)
    if not state.started_at:
        state.started_at = now_kst_iso()

    stop = False

    def _handle_stop(*_args: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    iterations = 0
    while not stop:
        state = tick_runner(runner, db_path=db_path, state=state)
        save_runner_state(runner.output_dir, state)
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break
        time.sleep(max(float(runner.loop_sleep_seconds), 1.0))

    save_runner_state(runner.output_dir, state)
