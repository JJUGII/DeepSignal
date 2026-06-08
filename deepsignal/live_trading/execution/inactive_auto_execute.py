"""Execute orders during operator inactive hours without Telegram approval."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from deepsignal.live_trading.approved_execution import (
    FINAL_CONFIRM_TEXT,
    ApprovedExecutionResult,
)
from deepsignal.live_trading.kis_broker import KISBroker
from deepsignal.live_trading.kis_config import load_kis_config_from_env, validate_kis_config
from deepsignal.live_trading.live_execution_guard import LiveExecutionPolicy
from deepsignal.live_trading.live_order_executor import execute_live_order_plan, write_live_approval_audit_log
from deepsignal.live_trading.operator_inactive_window import (
    OperatorInactiveConfig,
    is_inactive_auto_execute_active,
    load_operator_inactive_config_from_env,
)
from deepsignal.live_trading.telegram_approval import (
    APPROVAL_STATUS_PENDING,
    TelegramApprovalConfig,
    load_latest_request,
    validate_plan_limits,
)
from deepsignal.live_trading.telegram_auto_execute import send_runner_telegram
from deepsignal.live_trading.telegram_operator_messages import (
    format_operator_execution_result_text,
    load_plan_order_context,
)
from deepsignal.live_trading.time_utils import now_kst_iso, stamp_daily_ai_payload


def format_inactive_auto_preamble(cfg: OperatorInactiveConfig) -> str:
    return f"[DeepSignal 비활동 자동매매]\n운영자 비활동 구간({cfg.describe_window()}) — 승인 없이 주문 실행 후 결과만 보고합니다.\n"


def format_kis_stock_auto_execute_preamble() -> str:
    return (
        "[DeepSignal 국내주식 자동매매]\n"
        "KIS_STOCK_AUTO_EXECUTE_WITHOUT_APPROVAL=on — 장중 승인 없이 매수·매도, 체결 시에만 Telegram 보고합니다.\n"
    )


def format_kis_stock_execution_preamble(
    *,
    inactive_cfg: OperatorInactiveConfig | None = None,
) -> str:
    from deepsignal.live_trading.kis_stock_auto_execute_policy import is_kis_stock_auto_execute_without_approval

    if is_kis_stock_auto_execute_without_approval():
        return format_kis_stock_auto_execute_preamble()
    cfg = inactive_cfg or load_operator_inactive_config_from_env()
    return format_inactive_auto_preamble(cfg)


def _kis_auto_approval_channel() -> str:
    from deepsignal.live_trading.kis_stock_auto_execute_policy import is_kis_stock_auto_execute_without_approval

    if is_kis_stock_auto_execute_without_approval():
        return "kis_stock_auto_execute"
    return "inactive_auto_execute"


def format_crypto_auto_execute_preamble() -> str:
    # 기술 헤더 제거 — 체결 결과 메시지가 단독으로 전송됨
    return ""


def format_crypto_execution_preamble(
    *,
    inactive_cfg: OperatorInactiveConfig | None = None,
) -> str:
    from deepsignal.crypto_trading.crypto_auto_execute_policy import is_crypto_auto_execute_without_approval

    if is_crypto_auto_execute_without_approval():
        return format_crypto_auto_execute_preamble()
    cfg = inactive_cfg or load_operator_inactive_config_from_env()
    return format_inactive_auto_preamble(cfg)


def execute_kis_plan_inactive_auto(
    plan_path: str | Path,
    *,
    db_path: str,
    output_dir: str | Path,
    tg_config: TelegramApprovalConfig,
    max_single_order_value: float | None = None,
    max_total_order_value: float | None = None,
    max_orders: int | None = None,
) -> ApprovedExecutionResult:
    """Run live order plan without Telegram approval (inactive window only)."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    plan_p = Path(plan_path)
    errors: list[str] = []
    warnings: list[str] = []

    limit_cfg = TelegramApprovalConfig(
        output_dir=str(out),
        max_single_order_value=float(max_single_order_value or tg_config.max_single_order_value),
        max_total_order_value=float(max_total_order_value or tg_config.max_total_order_value),
        max_orders=int(max_orders or tg_config.max_orders),
    )
    errors.extend(validate_plan_limits(plan_p, limit_cfg))

    exec_result: dict[str, Any] = {}
    live_audit_path: str | None = None
    if not errors:
        try:
            cfg = load_kis_config_from_env()
            verr, vwarn = validate_kis_config(cfg)
            warnings.extend(vwarn)
            errors.extend(verr)
            if not errors:
                broker = KISBroker(cfg, safe_mode=False)
                policy = LiveExecutionPolicy(
                    max_total_order_value=limit_cfg.max_total_order_value,
                    max_single_order_value=limit_cfg.max_single_order_value,
                    max_orders=limit_cfg.max_orders,
                    allow_live_env=True,
                )
                exec_result = execute_live_order_plan(
                    plan_p,
                    broker,
                    approved=True,
                    execute=True,
                    dry_run=False,
                    final_confirm=FINAL_CONFIRM_TEXT,
                    live_policy=policy,
                    db_path=db_path,
                    output_dir=str(out),
                    require_pre_trade_runbook=False,
                )
                channel = _kis_auto_approval_channel()
                live_payload = {
                    **exec_result,
                    "approval_channel": channel,
                    "operator_inactive_window": channel == "inactive_auto_execute",
                    "kis_stock_auto_execute": channel == "kis_stock_auto_execute",
                    "telegram_approval_skipped": True,
                }
                live_audit_path = write_live_approval_audit_log(live_payload, output_dir=out).as_posix()
        except Exception as exc:
            errors.append(f"비활동 자동 실행 중 예외: {exc!r}")

    success = bool(
        not errors
        and exec_result.get("success")
        and exec_result.get("status") == "KIS_LIVE_ORDER_COMPLETED"
    )
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_json = out / f"inactive_auto_execute_audit_{ts}.json"
    payload = stamp_daily_ai_payload(
        {
            "request_id": "inactive_auto",
            "success": success,
            "status": "INACTIVE_AUTO_COMPLETED" if success else "INACTIVE_AUTO_BLOCKED",
            "errors": errors,
            "warnings": warnings,
            "plan_path": plan_p.as_posix(),
            "execution_result": exec_result,
            "live_approval_audit_path": live_audit_path,
            "executed_at": now_kst_iso(),
        }
    )
    audit_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return ApprovedExecutionResult(
        request_id="inactive_auto",
        success=success,
        status=str(payload["status"]),
        errors=errors,
        warnings=warnings,
        audit_json_path=audit_json.as_posix(),
        audit_markdown_path="",
        live_approval_audit_path=live_audit_path,
        execution_result=exec_result,
    )


def notify_inactive_kis_execution(
    *,
    execution: ApprovedExecutionResult,
    plan_path: str | Path,
    tg_config: TelegramApprovalConfig,
    inactive_cfg: OperatorInactiveConfig | None = None,
) -> dict[str, Any]:
    """Telegram: execution result only (no approval buttons)."""
    plan_ctx = load_plan_order_context(plan_path)
    body = format_operator_execution_result_text(execution=execution, plan_context=plan_ctx)
    text = format_kis_stock_execution_preamble(inactive_cfg=inactive_cfg) + body
    return send_runner_telegram(text=text, config=tg_config, reply_markup=False)


def try_execute_pending_kis_in_inactive_window(
    output_dir: str | Path,
    *,
    db_path: str,
    tg_config: TelegramApprovalConfig,
    max_single_order_value: float | None = None,
    max_total_order_value: float | None = None,
    max_orders: int | None = None,
    inactive_cfg: OperatorInactiveConfig | None = None,
) -> ApprovedExecutionResult | None:
    """If a pending Telegram approval exists, execute it without waiting for callback."""
    from deepsignal.live_trading.kis_stock_auto_execute_policy import is_kis_stock_auto_execute_without_approval

    cfg = inactive_cfg or load_operator_inactive_config_from_env()
    inactive_on = is_inactive_auto_execute_active(config=cfg)
    stock_auto = is_kis_stock_auto_execute_without_approval()
    if not inactive_on and not stock_auto:
        return None
    if stock_auto and not inactive_on:
        from deepsignal.live_trading.trading_session import (
            is_trading_session_open,
            load_trading_session_policy_from_env,
        )

        session = is_trading_session_open(policy=load_trading_session_policy_from_env())
        if not session.is_open:
            return None
    state = load_latest_request(output_dir)
    if not state or str(state.get("status") or "") != APPROVAL_STATUS_PENDING:
        return None
    plan_path = str(state.get("plan_path") or "").strip()
    if not plan_path or not Path(plan_path).is_file():
        return None
    result = execute_kis_plan_inactive_auto(
        plan_path,
        db_path=db_path,
        output_dir=output_dir,
        tg_config=tg_config,
        max_single_order_value=max_single_order_value,
        max_total_order_value=max_total_order_value,
        max_orders=max_orders,
    )
    notify_inactive_kis_execution(
        execution=result,
        plan_path=plan_path,
        tg_config=tg_config,
        inactive_cfg=cfg,
    )
    return result


def execute_crypto_plan_inactive_auto(
    broker: Any,
    plan: Any,
    *,
    tg_cfg: Any,
    output_dir: str | Path,
    wait_fill_seconds: float = 0.0,
    fill_poll_interval: float = 3.0,
    inactive_cfg: OperatorInactiveConfig | None = None,
    outcome_id: int | None = None,
) -> dict[str, Any]:
    """Upbit buy/sell without Telegram approval during inactive window."""
    from dataclasses import asdict

    from deepsignal.crypto_trading.telegram.flow import (
        STATUS_APPROVED,
        _write_audit,
        execute_approved_crypto_order,
        follow_up_order_fill,
        format_execution_report,
        telegram_send_plain,
    )

    from deepsignal.crypto_trading.crypto_auto_execute_policy import is_crypto_auto_execute_without_approval

    op_cfg = inactive_cfg or load_operator_inactive_config_from_env()
    inactive_on = is_inactive_auto_execute_active(config=op_cfg)
    crypto_24h = is_crypto_auto_execute_without_approval()
    channel = "crypto_auto_execute_24h" if crypto_24h else "inactive_auto_execute"
    do_exec = not broker.config.dry_run and not broker.config.paper_mode
    from deepsignal.crypto_trading.crypto_auto_runner import load_runner_state

    runner_state = load_runner_state(output_dir)
    frac = 1.0
    bd = plan.score_breakdown if isinstance(getattr(plan, "score_breakdown", None), dict) else {}
    if isinstance(bd, dict) and bd.get("sell_volume_fraction") is not None:
        try:
            frac = float(bd["sell_volume_fraction"])
        except (TypeError, ValueError):
            frac = 1.0
    if plan.side.lower() == "sell" and float(plan.volume or 0) <= 0:
        audit = {
            "status": "skipped",
            "channel": channel,
            "operator_inactive_window": inactive_on,
            "crypto_auto_execute_24h": crypto_24h,
            "telegram_approval_skipped": True,
            "executed": False,
            "reason": "sell_volume_zero",
            "plan": plan.to_dict() if hasattr(plan, "to_dict") else {},
        }
        audit_path = _write_audit(output_dir, audit)
        audit["audit_path"] = audit_path.as_posix()
        return audit

    # 동일 마켓 미체결 매도 주문이 있으면 먼저 취소 (locked 수량 해제)
    if plan.side.lower() == "sell" and do_exec:
        try:
            open_orders = broker.get_open_orders(market=plan.market)
            for o in open_orders:
                if o.get("side") == "ask":
                    broker.cancel_order(str(o["uuid"]))
                    import logging as _log
                    _log.getLogger(__name__).info(
                        "exec: 기존 매도 주문 취소 후 재접수 %s uuid=%s",
                        plan.market, str(o["uuid"])[:8],
                    )
        except Exception as _ce:
            import logging as _log
            _log.getLogger(__name__).warning("exec: 기존 주문 취소 실패 %s: %s", plan.market, _ce)

    order_result = execute_approved_crypto_order(
        broker,
        plan,
        execute=do_exec,
        output_dir=output_dir,
        runner_state=runner_state,
        sell_volume_fraction=frac,
    )
    audit: dict[str, Any] = {
        "status": STATUS_APPROVED,
        "channel": channel,
        "operator_inactive_window": inactive_on,
        "crypto_auto_execute_24h": crypto_24h,
        "telegram_approval_skipped": True,
        "executed": do_exec,
        "plan": plan.to_dict() if hasattr(plan, "to_dict") else {},
        "result": asdict(order_result),
    }
    audit_path = _write_audit(output_dir, audit)
    audit["audit_path"] = audit_path.as_posix()

    if tg_cfg.bot_token and tg_cfg.allowed_chat_id:
        # 체결 폴링이 따라오면 접수 메시지 생략 — 체결 결과 메시지만 전송
        wait_will_follow = wait_fill_seconds > 0 and bool(order_result.uuid)
        report = format_execution_report(plan, order_result, skip_if_fill_follows=wait_will_follow)
        if report:
            audit["telegram"] = telegram_send_plain(tg_cfg, report)

    if wait_fill_seconds > 0 and order_result.uuid:
        audit["fill_follow_up"] = follow_up_order_fill(
            tg_cfg,
            broker,
            plan,
            order_result,
        )

    from deepsignal.crypto_trading.crypto_recommendation_outcomes import apply_crypto_trade_pipeline

    ff = audit.get("fill_follow_up") or {}
    audit["outcome_tracking"] = apply_crypto_trade_pipeline(
        plan,
        order_result,
        outcomes_db=output_dir,
        fill_status=ff.get("order_status"),
        fill_outcome=ff.get("fill_outcome"),
        outcome_id=outcome_id,
    )
    if outcome_id is not None:
        audit["outcome_id"] = outcome_id

    try:
        from deepsignal.crypto_trading.crypto_auto_runner import save_runner_state

        save_runner_state(output_dir, runner_state)
    except Exception:
        pass

    try:
        from deepsignal.crypto_trading.runner.auto_runner import _persist_trade_state

        _persist_trade_state(output_dir, plan)
    except Exception:
        pass

    return audit


def try_execute_pending_crypto_in_inactive_window(
    broker: Any,
    *,
    output_dir: str | Path,
    tg_cfg: Any,
    wait_fill_seconds: float = 0.0,
    fill_poll_interval: float = 3.0,
    inactive_cfg: OperatorInactiveConfig | None = None,
) -> dict[str, Any] | None:
    """Execute pending crypto approval plan without Telegram callback."""
    from deepsignal.crypto_trading.crypto_order_plan import CRYPTO_PLAN_JSON, load_crypto_plan
    from deepsignal.crypto_trading.telegram.flow import (
        STATUS_APPROVED,
        STATUS_PENDING,
        STATUS_REJECTED,
        load_crypto_approval_request,
        _save_request,
    )
    from deepsignal.crypto_trading.crypto_execution_quality import effective_min_order_krw
    from deepsignal.crypto_trading.upbit_broker import UpbitBrokerError

    from deepsignal.crypto_trading.crypto_auto_execute_policy import should_auto_execute_crypto_on_runner_tick

    if not should_auto_execute_crypto_on_runner_tick():
        return None
    req = load_crypto_approval_request(output_dir)
    if req is None or req.status != STATUS_PENDING:
        return None
    plan_path = Path(req.plan_path)
    if not plan_path.is_file():
        plan_path = Path(output_dir) / CRYPTO_PLAN_JSON
    if not plan_path.is_file():
        return None
    plan = load_crypto_plan(plan_path)
    policy_min = effective_min_order_krw()
    if plan.side.lower() == "buy" and float(plan.krw_amount or 0) < float(policy_min):
        req.status = STATUS_REJECTED
        _save_request(output_dir, req)
        return {
            "status": STATUS_REJECTED,
            "reason": f"under_min_order_krw:{float(plan.krw_amount or 0):,.0f}<{policy_min:,.0f}",
            "plan": plan.to_dict() if hasattr(plan, "to_dict") else {},
        }
    try:
        audit = execute_crypto_plan_inactive_auto(
            broker,
            plan,
            tg_cfg=tg_cfg,
            output_dir=output_dir,
            wait_fill_seconds=wait_fill_seconds,
            fill_poll_interval=fill_poll_interval,
            inactive_cfg=inactive_cfg,
        )
    except UpbitBrokerError as exc:
        req.status = STATUS_REJECTED
        _save_request(output_dir, req)
        return {
            "status": STATUS_REJECTED,
            "reason": str(exc),
            "plan": plan.to_dict() if hasattr(plan, "to_dict") else {},
        }
    req.status = STATUS_APPROVED
    _save_request(output_dir, req)
    return audit
