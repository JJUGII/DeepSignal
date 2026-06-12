"""Telegram 승인 즉시 실주문 자동 실행 ([실전-최종UX]).

승인 callback 처리 후 ``execute_live_order_plan``까지 수행하고 결과를 Telegram으로 전송한다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from deepsignal.live_trading.approved_execution import (
    ApprovedExecutionResult,
    execute_request_from_audit,
    run_approved_execution,
)
from deepsignal.live_trading.telegram.approval import (
    ACTION_APPROVE,
    ACTION_REJECT,
    APPROVAL_STATUS_APPROVED,
    TELEGRAM_APPROVED_AUDIT_STATUS,
    TELEGRAM_REJECTED_AUDIT_STATUS,
    TelegramApprovalConfig,
    find_action_from_updates,
    handle_telegram_action,
    load_latest_request,
    load_telegram_config_from_env,
    telegram_answer_callback,
    telegram_api_post,
    telegram_get_updates,
    _state_expired,
)

from deepsignal.live_trading.telegram_operator_messages import (
    format_operator_approval_request_text,
    format_operator_daily_report_text,
    format_operator_execution_result_text,
    format_operator_no_orders_text,
    format_operator_plan_blocked_text,
    load_plan_order_context,
)

@dataclass
class TelegramAutoExecuteOutcome:
    outcome: str
    message: str
    audit: dict[str, Any] | None = None
    audit_path: str | None = None
    execution: ApprovedExecutionResult | None = None


def _load_plan_context(plan_path: str | Path) -> dict[str, Any]:
    ctx = load_plan_order_context(plan_path)
    orders = []
    first = ctx.get("first_order") if isinstance(ctx.get("first_order"), dict) else {}
    if first:
        orders = [first]
    return {
        "plan_path": ctx.get("plan_path"),
        "plan_name": Path(plan_path).name if plan_path else "",
        "orders": orders,
        "first_order": first,
        "order_count": int(ctx.get("order_count") or 0),
        "total_order_value": float(first.get("estimated_order_value") or 0) if first else 0.0,
    }


def format_compact_approval_request_text(request: Any, *, plan_path: str | Path) -> str:
    return format_operator_approval_request_text(request, plan_path=plan_path)


def format_approval_request_telegram_text(request: Any, *, plan_path: str | Path) -> str:
    return format_operator_approval_request_text(request, plan_path=plan_path)


def format_compact_execution_result_text(
    *,
    execution: ApprovedExecutionResult,
    plan_context: dict[str, Any],
) -> str:
    return format_operator_execution_result_text(execution=execution, plan_context=plan_context)


def format_execution_result_telegram_text(
    *,
    execution: ApprovedExecutionResult,
    plan_context: dict[str, Any],
) -> str:
    return format_operator_execution_result_text(execution=execution, plan_context=plan_context)


def format_no_orders_today_text() -> str:
    return format_operator_no_orders_text()


def format_compact_daily_report_text(report: Any) -> str:
    return format_operator_daily_report_text(report)


def _next_update_offset(updates: list[dict[str, Any]], current: int | None) -> int | None:
    ids = [int(u["update_id"]) for u in updates if u.get("update_id") is not None]
    if not ids:
        return current
    return max(max(ids) + 1, int(current or 0))


def send_runner_telegram(
    *,
    text: str,
    config: TelegramApprovalConfig,
    reply_markup: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not config.allowed_chat_id:
        return {"ok": False, "status": "missing_chat_id"}
    payload: dict[str, Any] = {
        "chat_id": config.allowed_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    # HTML 태그(<b> 등)가 든 메시지는 HTML 파스모드로 — 아니면 태그가 그대로 노출됨
    if "<b>" in text or "<code>" in text or "<i>" in text:
        payload["parse_mode"] = "HTML"
    if reply_markup and token:
        from deepsignal.live_trading.telegram.approval import _reply_markup

        payload["reply_markup"] = _reply_markup(token)
    return telegram_api_post("sendMessage", payload, bot_token=config.bot_token, timeout_seconds=config.timeout_seconds)


def try_resume_approved_execution(
    output_dir: str | Path,
    *,
    db_path: str,
    config: TelegramApprovalConfig,
    format_result: Callable[..., str] | None = None,
    execute_runner: Callable[..., ApprovedExecutionResult] | None = None,
) -> ApprovedExecutionResult | None:
    state = load_latest_request(output_dir)
    if str(state.get("status") or "") != APPROVAL_STATUS_APPROVED:
        return None
    token = str(state.get("token") or "")
    audit_path, audit = _find_approved_audit_for_token(output_dir, token)
    if not audit_path or not audit or audit.get("auto_executed"):
        return None
    result = auto_execute_after_telegram_approval(
        state=state,
        audit=audit,
        audit_path=audit_path,
        output_dir=output_dir,
        db_path=db_path,
        config=config,
        execute_runner=execute_runner,
    )
    fmt = format_result or format_compact_execution_result_text
    plan_ctx = _load_plan_context(str(state.get("plan_path") or ""))
    _notify_chat(
        chat_id=str(state.get("allowed_chat_id") or config.allowed_chat_id or ""),
        bot_token=config.bot_token,
        text=fmt(execution=result, plan_context=plan_ctx),
        timeout_seconds=config.timeout_seconds,
    )
    return result


def poll_telegram_approval_once(
    output_dir: str | Path,
    *,
    db_path: str,
    config: TelegramApprovalConfig,
    poll_interval: float = 2.0,
    update_offset: int | None = None,
    auto_execute: bool = True,
    execute_runner: Callable[..., ApprovedExecutionResult] | None = None,
    format_result: Callable[..., str] | None = None,
) -> tuple[TelegramAutoExecuteOutcome, int | None]:
    state = load_latest_request(output_dir)
    if not state or not str(state.get("token") or "").strip():
        return TelegramAutoExecuteOutcome(outcome="idle", message="no pending request"), update_offset
    if _state_expired(state):
        return TelegramAutoExecuteOutcome(outcome="expired", message="승인 만료"), update_offset

    updates_payload = telegram_get_updates(
        bot_token=config.bot_token,
        timeout_seconds=min(float(config.timeout_seconds), max(float(poll_interval), 1.0)),
        offset=update_offset,
    )
    updates = list(updates_payload.get("result") or [])
    new_offset = _next_update_offset(updates, update_offset)

    action, action_token, chat_id, callback_id = find_action_from_updates(updates, state)
    if not action:
        return TelegramAutoExecuteOutcome(outcome="idle", message="no callback"), new_offset

    return (
        _process_callback(
            state=load_latest_request(output_dir) or state,
            action=action,
            token=action_token,
            chat_id=chat_id,
            callback_id=callback_id,
            output_dir=output_dir,
            config=config,
            db_path=db_path,
            auto_execute=auto_execute,
            execute_runner=execute_runner,
            format_result=format_result,
        ),
        new_offset,
    )


def _notify_chat(
    *,
    chat_id: str | None,
    bot_token: str | None,
    text: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if not chat_id:
        return {"ok": False, "status": "missing_chat_id", "network_called": False}
    return telegram_api_post(
        "sendMessage",
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        bot_token=bot_token,
        timeout_seconds=timeout_seconds,
    )


def auto_execute_after_telegram_approval(
    *,
    state: dict[str, Any],
    audit: dict[str, Any],
    audit_path: Path,
    output_dir: str | Path,
    db_path: str,
    config: TelegramApprovalConfig,
    execute_runner: Callable[..., ApprovedExecutionResult] | None = None,
) -> ApprovedExecutionResult:
    token = str(state.get("token") or audit.get("token") or "")
    req = execute_request_from_audit(
        output_dir,
        request_id=token,
        approval_audit_path=audit_path,
        approval_audit=audit,
    )
    runner = execute_runner or run_approved_execution
    result = runner(
        req,
        output_dir=output_dir,
        db_path=db_path,
        bot_token=config.bot_token,
        timeout_seconds=config.timeout_seconds,
        send=False,
    )
    audit["manual_live_approve_required"] = False
    audit["auto_executed"] = True
    audit["live_execution_result"] = result.execution_result
    audit["execute_approved_audit_path"] = result.audit_json_path
    audit["kis_post_called"] = bool(result.success and result.execution_result.get("actual_order_attempted"))
    audit["final_confirm_supplied"] = True
    audit["final_confirm_source"] = "telegram_auto_execute_wrapper"
    if result.success:
        audit["status"] = "TELEGRAM_APPROVAL_AUTO_EXECUTED"
    else:
        audit["status"] = "TELEGRAM_APPROVAL_AUTO_EXECUTION_FAILED"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _process_callback(
    *,
    state: dict[str, Any],
    action: str,
    token: str,
    chat_id: str | None,
    callback_id: str | None,
    output_dir: str | Path,
    config: TelegramApprovalConfig,
    db_path: str,
    auto_execute: bool,
    execute_runner: Callable[..., ApprovedExecutionResult] | None = None,
    format_result: Callable[..., str] | None = None,
) -> TelegramAutoExecuteOutcome:
    audit, audit_path = handle_telegram_action(
        state=state,
        action=action,
        token=token,
        chat_id=chat_id,
        output_dir=output_dir,
    )
    if callback_id:
        ack = "처리 완료"
        if action == ACTION_APPROVE and not audit.get("errors"):
            ack = "승인 확인 · 주문 실행 중"
        if action == ACTION_REJECT and not audit.get("errors"):
            ack = "거부 처리"
        telegram_answer_callback(callback_id, bot_token=config.bot_token, text=ack, timeout_seconds=config.timeout_seconds)

    if audit.get("errors"):
        return TelegramAutoExecuteOutcome(
            outcome="blocked",
            message="Telegram 승인 검증 실패",
            audit=audit,
            audit_path=audit_path.as_posix(),
        )

    if action == ACTION_REJECT and str(audit.get("status") or "") == TELEGRAM_REJECTED_AUDIT_STATUS:
        _notify_chat(
            chat_id=chat_id or str(state.get("allowed_chat_id") or ""),
            bot_token=config.bot_token,
            text="[DeepSignal] 승인 거부됨. 주문이 실행되지 않았습니다.",
            timeout_seconds=config.timeout_seconds,
        )
        return TelegramAutoExecuteOutcome(
            outcome="rejected",
            message="Telegram 거부 처리 완료",
            audit=audit,
            audit_path=audit_path.as_posix(),
        )

    if action != ACTION_APPROVE or str(audit.get("status") or "") != TELEGRAM_APPROVED_AUDIT_STATUS:
        return TelegramAutoExecuteOutcome(
            outcome="ignored",
            message=f"처리되지 않은 action: {action}",
            audit=audit,
            audit_path=audit_path.as_posix(),
        )

    if not auto_execute:
        return TelegramAutoExecuteOutcome(
            outcome="approved_manual",
            message="Telegram 승인 확인 완료 (수동 실행 필요)",
            audit=audit,
            audit_path=audit_path.as_posix(),
        )

    execution = auto_execute_after_telegram_approval(
        state=state,
        audit=audit,
        audit_path=audit_path,
        output_dir=output_dir,
        db_path=db_path,
        config=config,
        execute_runner=execute_runner,
    )
    plan_ctx = _load_plan_context(str(state.get("plan_path") or ""))
    fmt = format_result or format_compact_execution_result_text
    result_text = fmt(execution=execution, plan_context=plan_ctx)
    _notify_chat(
        chat_id=chat_id or str(state.get("allowed_chat_id") or ""),
        bot_token=config.bot_token,
        text=result_text,
        timeout_seconds=config.timeout_seconds,
    )
    if execution.success:
        return TelegramAutoExecuteOutcome(
            outcome="executed",
            message="Telegram 승인 후 실주문 실행 완료",
            audit=audit,
            audit_path=audit_path.as_posix(),
            execution=execution,
        )
    return TelegramAutoExecuteOutcome(
        outcome="execution_failed",
        message="Telegram 승인 후 실주문 실행 실패",
        audit=audit,
        audit_path=audit_path.as_posix(),
        execution=execution,
    )


def poll_telegram_approval_until_done(
    output_dir: str | Path = "outputs",
    *,
    db_path: str,
    wait_seconds: float | None = None,
    poll_interval: float = 2.0,
    allowed_chat_id: str | None = None,
    timeout_seconds: float = 5.0,
    auto_execute: bool = True,
    execute_runner: Callable[..., ApprovedExecutionResult] | None = None,
) -> TelegramAutoExecuteOutcome:
    state = load_latest_request(output_dir)
    if not state or not str(state.get("token") or "").strip():
        return TelegramAutoExecuteOutcome(outcome="no_request", message="Telegram 승인 요청이 없습니다.")

    if _state_expired(state):
        return TelegramAutoExecuteOutcome(outcome="expired", message="Telegram 승인 만료됨")

    token = str(state["token"])
    cfg = load_telegram_config_from_env(
        output_dir=str(output_dir),
        timeout_seconds=timeout_seconds,
        allowed_chat_id=allowed_chat_id,
    )

    approved_audit_path, approved_audit = _find_approved_audit_for_token(output_dir, token)
    if approved_audit and str(state.get("status") or "") == "APPROVED":
        if auto_execute and approved_audit.get("auto_executed"):
            return TelegramAutoExecuteOutcome(
                outcome="already_executed",
                message="이미 자동 실행된 승인입니다",
                audit=approved_audit,
                audit_path=approved_audit_path.as_posix() if approved_audit_path else None,
            )
        if not auto_execute:
            return TelegramAutoExecuteOutcome(
                outcome="approved_manual",
                message="이미 승인됨",
                audit=approved_audit,
                audit_path=approved_audit_path.as_posix() if approved_audit_path else None,
            )

    expires = state.get("expires_at")
    from deepsignal.live_trading.time_utils import parse_datetime_with_default_tz, now_kst

    deadline = time.monotonic() + float(wait_seconds if wait_seconds is not None else 600.0)
    if expires:
        exp_dt = parse_datetime_with_default_tz(expires)
        if exp_dt is not None:
            remain = (exp_dt - now_kst()).total_seconds()
            if remain > 0:
                deadline = min(deadline, time.monotonic() + remain)

    processed: set[str] = set()
    interval = max(float(poll_interval), 0.5)

    while time.monotonic() < deadline:
        if _state_expired(load_latest_request(output_dir)):
            return TelegramAutoExecuteOutcome(outcome="expired", message="Telegram 승인 만료됨")

        updates_payload = telegram_get_updates(bot_token=cfg.bot_token, timeout_seconds=cfg.timeout_seconds)
        updates = list(updates_payload.get("result") or [])
        action, action_token, chat_id, callback_id = find_action_from_updates(updates, state)
        if action:
            dedupe = callback_id or f"update:{updates[-1].get('update_id') if updates else ''}"
            if dedupe not in processed:
                processed.add(dedupe)
                current_state = load_latest_request(output_dir)
                return _process_callback(
                    state=current_state,
                    action=action,
                    token=action_token,
                    chat_id=chat_id,
                    callback_id=callback_id,
                    output_dir=output_dir,
                    config=cfg,
                    db_path=db_path,
                    auto_execute=auto_execute,
                    execute_runner=execute_runner,
                )
        time.sleep(interval)

    return TelegramAutoExecuteOutcome(outcome="pending_timeout", message="Telegram 승인 대기 중입니다")


def _find_approved_audit_for_token(output_dir: str | Path, token: str) -> tuple[Path | None, dict[str, Any] | None]:
    for path in sorted(Path(output_dir).glob("telegram_approval_audit_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(data.get("token") or "") != token:
            continue
        if str(data.get("status") or "") in {
            TELEGRAM_APPROVED_AUDIT_STATUS,
            "TELEGRAM_APPROVAL_AUTO_EXECUTED",
            "TELEGRAM_APPROVAL_AUTO_EXECUTION_FAILED",
        }:
            return path, data
    return None, None
