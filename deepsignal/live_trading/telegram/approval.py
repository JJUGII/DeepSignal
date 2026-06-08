"""Telegram approval workflow for live order plans.

승인 callback은 ``telegram_auto_execute``에서 즉시 ``execute_live_order_plan``까지
연결할 수 있다. 이 모듈 단독으로는 KIS 주문을 보내지 않는다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any

import requests

from deepsignal.live_trading.live_order_executor import load_live_order_plan
from deepsignal.live_trading.time_utils import (
    markdown_timestamp_block,
    now_kst,
    now_kst_iso,
    parse_datetime_with_default_tz,
    stamp_daily_ai_payload,
)

APPROVAL_STATUS_PENDING = "PENDING"
APPROVAL_STATUS_APPROVED = "APPROVED"
APPROVAL_STATUS_REJECTED = "REJECTED"
APPROVAL_STATUS_EXPIRED = "EXPIRED"
APPROVAL_STATUS_BLOCKED = "BLOCKED"

ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_DETAILS = "details"
ACTION_HALT_TODAY = "halt_today"

TELEGRAM_APPROVED_AUDIT_STATUS = "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED"
TELEGRAM_REJECTED_AUDIT_STATUS = "TELEGRAM_APPROVAL_REJECTED"


@dataclass
class TelegramApprovalConfig:
    output_dir: str = "outputs"
    expires_minutes: int = 10
    max_total_order_value: float = 100_000.0
    max_single_order_value: float = 50_000.0
    max_orders: int = 1
    allowed_chat_id: str | None = None
    bot_token: str | None = None
    send: bool = False
    timeout_seconds: float = 5.0


@dataclass
class TelegramApprovalRequest:
    token: str
    plan_path: str
    plan_hash: str
    created_at: str
    expires_at: str
    status: str
    order_count: int
    total_order_value: float
    max_total_order_value: float
    max_single_order_value: float
    max_orders: int
    allowed_chat_id: str | None
    request_json: str = ""
    request_markdown: str = ""
    telegram_result: dict[str, Any] = field(default_factory=dict)
    consumed_at: str | None = None
    consumed_by_chat_id: str | None = None
    action: str | None = None
    manual_live_approve_command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> datetime:
    return now_kst()


def plan_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _root(output_dir: str | Path) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_path(output_dir: str | Path, token: str) -> Path:
    return _root(output_dir) / f"telegram_approval_state_{token}.json"


def _latest_state_path(output_dir: str | Path) -> Path:
    return _root(output_dir) / "TELEGRAM_APPROVAL_STATE.json"


def _halt_path(output_dir: str | Path, day: str | None = None) -> Path:
    ymd = (day or _now().strftime("%Y%m%d"))[:8]
    return _root(output_dir) / f"telegram_approval_halt_{ymd}.json"


def _request_paths(output_dir: str | Path) -> tuple[Path, Path]:
    root = _root(output_dir)
    ts = _now().strftime("%Y%m%d_%H%M%S")
    return root / f"telegram_approval_request_{ts}.json", root / "TELEGRAM_APPROVAL_REQUEST.md"


def _audit_path(output_dir: str | Path) -> Path:
    return _root(output_dir) / f"telegram_approval_audit_{_now().strftime('%Y%m%d_%H%M%S')}.json"


def load_telegram_config_from_env(output_dir: str = "outputs", **overrides: Any) -> TelegramApprovalConfig:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")
    return TelegramApprovalConfig(
        output_dir=output_dir,
        allowed_chat_id=str(overrides.get("allowed_chat_id") or os.getenv("DEEPSIGNAL_TELEGRAM_APPROVER_CHAT_ID") or os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID") or "").strip() or None,
        bot_token=str(overrides.get("bot_token") or os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN") or "").strip() or None,
        expires_minutes=int(overrides.get("expires_minutes", 10) or 10),
        max_total_order_value=float(overrides.get("max_total_order_value", 100_000.0) or 100_000.0),
        max_single_order_value=float(overrides.get("max_single_order_value", 50_000.0) or 50_000.0),
        max_orders=int(overrides.get("max_orders", 1) or 1),
        send=bool(overrides.get("send", False)),
        timeout_seconds=float(overrides.get("timeout_seconds", 5.0) or 5.0),
    )


def _plan_summary(plan_path: str | Path) -> tuple[int, float, list[str], list[str]]:
    plan = load_live_order_plan(plan_path)
    values = [float(o.estimated_order_value) for o in plan.orders]
    symbols = [str(o.symbol) for o in plan.orders]
    return len(plan.orders), sum(values), symbols, list(plan.warnings)


def validate_plan_limits(plan_path: str | Path, config: TelegramApprovalConfig) -> list[str]:
    plan = load_live_order_plan(plan_path)
    errors: list[str] = []
    if len(plan.orders) > int(config.max_orders):
        errors.append(f"order count {len(plan.orders)} exceeds max_orders={config.max_orders}")
    total = 0.0
    for i, order in enumerate(plan.orders):
        value = float(order.estimated_order_value)
        total += value
        if value > float(config.max_single_order_value):
            errors.append(f"orders[{i}] value {value} exceeds max_single_order_value={config.max_single_order_value}")
        if str(order.side).upper() != "BUY":
            errors.append(f"orders[{i}] only BUY allowed, got {order.side}")
        if int(order.estimated_qty) <= 0:
            errors.append(f"orders[{i}] quantity must be > 0")
        if float(order.estimated_price) <= 0:
            errors.append(f"orders[{i}] estimated_price must be > 0")
    if total > float(config.max_total_order_value):
        errors.append(f"total order value {total} exceeds max_total_order_value={config.max_total_order_value}")
    return errors


def render_request_markdown(request: TelegramApprovalRequest, *, symbols: list[str], warnings: list[str]) -> str:
    manual_command = build_manual_live_approve_command(request.plan_path)
    created = parse_datetime_with_default_tz(request.created_at) or now_kst()
    lines = [
        "# DeepSignal Telegram Approval Request",
        "",
        *markdown_timestamp_block(created),
        "",
        f"- Status: {request.status}",
        f"- Plan: `{request.plan_path}`",
        f"- Plan SHA256: `{request.plan_hash}`",
        f"- Token: `{request.token}`",
        f"- Expires at: {request.expires_at}",
        f"- Order count: {request.order_count}",
        f"- Total order value: {request.total_order_value:,.2f}",
        f"- Max total order value: {request.max_total_order_value:,.2f}",
        f"- Max single order value: {request.max_single_order_value:,.2f}",
        f"- Max orders: {request.max_orders}",
        f"- Allowed chat id configured: {bool(request.allowed_chat_id)}",
        "",
        "## Symbols",
        "",
    ]
    lines.extend(f"- {symbol}" for symbol in symbols)
    if warnings:
        lines.extend(["", "## Plan Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- Telegram approval token is one-time.",
            "- Plan hash is verified before approval audit is written.",
            "- Telegram approval does not replace live-approve final confirmation.",
            "- This workflow does not execute live-approve, --execute, --allow-live-env, or KIS POST.",
            "- Market orders remain prohibited.",
            "",
            "## Manual Execution After Approval",
            "",
            "After Telegram approval, an operator must run the terminal execution command manually:",
            "",
            "```bash",
            manual_command,
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def build_manual_live_approve_command(plan_path: str | Path, *, output_dir: str | Path = "outputs") -> str:
    out = Path(output_dir).as_posix()
    return f"python main.py execute-last-approved --output-dir {out}"


def _telegram_text(request: TelegramApprovalRequest, symbols: list[str], *, output_dir: str | Path = "outputs") -> str:
    _ = symbols
    _ = output_dir
    from deepsignal.live_trading.telegram_auto_execute import format_approval_request_telegram_text

    return format_approval_request_telegram_text(request, plan_path=request.plan_path)


def _reply_markup(token: str, webapp_url: str | None = None) -> dict[str, Any]:
    buttons: list[list[dict[str, Any]]] = [
        [
            {"text": "✅ 승인", "callback_data": f"tgapprove:{ACTION_APPROVE}:{token}"},
            {"text": "❌ 거부", "callback_data": f"tgapprove:{ACTION_REJECT}:{token}"},
        ],
    ]
    url = (webapp_url or os.getenv("DEEPSIGNAL_WEBUI_PUBLIC_URL", "")).strip()
    if url:
        buttons.append([
            {"text": "📱 웹에서 상세 보기", "web_app": {"url": url}}
        ])
    return {"inline_keyboard": buttons}


def telegram_api_post(method: str, payload: dict[str, Any], *, bot_token: str | None, timeout_seconds: float = 5.0) -> dict[str, Any]:
    token = (bot_token or "").strip()
    if not token:
        return {"ok": False, "status": "missing_config", "network_called": False}
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=float(timeout_seconds))
        data: dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            pass
        ok = bool(data.get("ok")) if data else 200 <= response.status_code < 300
        return {
            "ok": ok,
            "status_code": response.status_code,
            "network_called": True,
            "description": data.get("description"),
        }
    except requests.RequestException as exc:
        return {"ok": False, "status": "request_error", "exception_type": type(exc).__name__, "network_called": True}


def telegram_answer_callback(
    callback_query_id: str,
    *,
    bot_token: str | None,
    text: str = "",
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    if not str(callback_query_id or "").strip():
        return {"ok": False, "status": "missing_callback_id", "network_called": False}
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return telegram_api_post("answerCallbackQuery", payload, bot_token=bot_token, timeout_seconds=timeout_seconds)


def telegram_get_updates(*, bot_token: str | None, timeout_seconds: float = 5.0, offset: int | None = None) -> dict[str, Any]:
    token = (bot_token or "").strip()
    if not token:
        return {"ok": False, "status": "missing_config", "network_called": False, "result": []}
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    payload: dict[str, Any] = {"timeout": 0}
    if offset is not None:
        payload["offset"] = int(offset)
    try:
        response = requests.get(url, params=payload, timeout=float(timeout_seconds))
        if not (200 <= response.status_code < 300):
            return {"ok": False, "status_code": response.status_code, "network_called": True, "result": []}
        data = response.json()
        return {"ok": bool(data.get("ok")), "network_called": True, "result": data.get("result") or []}
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "status": "request_error", "exception_type": type(exc).__name__, "network_called": True, "result": []}


def create_telegram_approval_request(plan_path: str | Path, config: TelegramApprovalConfig) -> tuple[TelegramApprovalRequest, Path, Path]:
    count, total, symbols, warnings = _plan_summary(plan_path)
    if count <= 0:
        warnings.append("Plan Orders가 0건입니다. daily-ai-trade-plan --debug-plan 으로 확인하세요.")
    token = secrets.token_urlsafe(18)
    created = _now()
    request = TelegramApprovalRequest(
        token=token,
        plan_path=Path(plan_path).as_posix(),
        plan_hash=plan_sha256(plan_path),
        created_at=created.isoformat(timespec="seconds"),
        expires_at=(created + timedelta(minutes=int(config.expires_minutes))).isoformat(timespec="seconds"),
        status=APPROVAL_STATUS_PENDING,
        order_count=count,
        total_order_value=total,
        max_total_order_value=float(config.max_total_order_value),
        max_single_order_value=float(config.max_single_order_value),
        max_orders=int(config.max_orders),
        allowed_chat_id=config.allowed_chat_id,
    )
    if count <= 0:
        request.status = APPROVAL_STATUS_BLOCKED
    limit_errors = validate_plan_limits(plan_path, config)
    if limit_errors:
        request.status = APPROVAL_STATUS_BLOCKED
        warnings.extend(limit_errors)

    json_path, md_path = _request_paths(config.output_dir)
    request.request_json = json_path.name
    request.request_markdown = md_path.name
    payload = stamp_daily_ai_payload(request.to_dict(), dt=created)
    payload["plan_symbols"] = symbols
    payload["plan_warnings"] = warnings
    daily_plan_path = Path(config.output_dir) / "AI_DAILY_TRADE_PLAN.md"
    payload["daily_ai_trade_plan_markdown"] = daily_plan_path.as_posix() if daily_plan_path.exists() else None
    payload["limit_errors"] = limit_errors
    payload["safety_boundary"] = {
        "telegram_approval_is_pre_execution_record_only": True,
        "one_time_token": True,
        "plan_hash_verified_before_manual_instruction": True,
        "telegram_approval_does_not_replace_final_confirm": True,
        "auto_execute_on_approve_via_telegram_auto_execute": True,
        "kis_post_not_called": True,
    }
    request.manual_live_approve_command = build_manual_live_approve_command(plan_path, output_dir=config.output_dir)
    payload["manual_live_approve_command"] = request.manual_live_approve_command
    if config.send and request.status == APPROVAL_STATUS_PENDING:
        send_payload = {
            "chat_id": config.allowed_chat_id,
            "text": _telegram_text(request, symbols, output_dir=config.output_dir),
            "disable_web_page_preview": True,
            "reply_markup": _reply_markup(token),
        }
        request.telegram_result = telegram_api_post("sendMessage", send_payload, bot_token=config.bot_token, timeout_seconds=config.timeout_seconds)
        payload["telegram_result"] = request.telegram_result
    else:
        request.telegram_result = {"status": "dry_run", "network_called": False, "would_send": request.status == APPROVAL_STATUS_PENDING}
        payload["telegram_result"] = request.telegram_result

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_request_markdown(request, symbols=symbols, warnings=warnings), encoding="utf-8")
    _state_path(config.output_dir, token).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _latest_state_path(config.output_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request, json_path, md_path


def load_latest_request(output_dir: str | Path = "outputs") -> dict[str, Any]:
    latest = _latest_state_path(output_dir)
    if latest.exists():
        return json.loads(latest.read_text(encoding="utf-8"))
    paths = sorted(Path(output_dir).glob("telegram_approval_request_*.json"))
    if not paths:
        return {}
    return json.loads(paths[-1].read_text(encoding="utf-8"))


def write_audit(output_dir: str | Path, payload: dict[str, Any]) -> Path:
    path = _audit_path(output_dir)
    body = stamp_daily_ai_payload({"timestamp": now_kst_iso(), **payload})
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _save_state(output_dir: str | Path, state: dict[str, Any]) -> None:
    token = str(state.get("token") or "")
    if token:
        _state_path(output_dir, token).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _latest_state_path(output_dir).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_update_action(update: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
    cb = update.get("callback_query") if isinstance(update, dict) else None
    if isinstance(cb, dict):
        data = str(cb.get("data") or "")
        chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id") or "")
        callback_id = str(cb.get("id") or "") or None
        if data.startswith("tgapprove:"):
            _, action, token = (data.split(":", 2) + [""])[:3]
            return action, token, chat_id, callback_id
    msg = update.get("message") if isinstance(update, dict) else None
    if isinstance(msg, dict):
        text = str(msg.get("text") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        parts = text.split()
        if parts:
            cmd = parts[0].lower()
            token = parts[1] if len(parts) > 1 else ""
            mapping = {"/approve": ACTION_APPROVE, "/stop": ACTION_REJECT, "/details": ACTION_DETAILS, "/halt": ACTION_HALT_TODAY}
            if cmd in mapping:
                return mapping[cmd], token, chat_id, None
    return "", "", None, None


def _halt_active(output_dir: str | Path) -> bool:
    path = _halt_path(output_dir)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return bool(data.get("active", True))


def verify_approval_action(state: dict[str, Any], *, action: str, token: str, chat_id: str | None, output_dir: str | Path) -> list[str]:
    errors: list[str] = []
    if _halt_active(output_dir) and action != ACTION_DETAILS:
        errors.append("today halt is active")
    if token != str(state.get("token") or ""):
        errors.append("approval token mismatch")
    if state.get("status") != APPROVAL_STATUS_PENDING:
        errors.append(f"approval state is not pending: {state.get('status')}")
    allowed = str(state.get("allowed_chat_id") or "").strip()
    if allowed and str(chat_id or "").strip() != allowed:
        errors.append("telegram chat_id is not authorized")
    expires = parse_datetime_with_default_tz(state.get("expires_at"))
    if expires is None:
        errors.append("approval expiry timestamp invalid")
    elif _now() > expires:
        errors.append("approval token expired")
    plan_path = str(state.get("plan_path") or "")
    if plan_path:
        try:
            if plan_sha256(plan_path) != str(state.get("plan_hash") or ""):
                errors.append("plan hash mismatch")
        except OSError as exc:
            errors.append(f"plan hash read failed: {exc}")
    else:
        errors.append("plan path missing")
    if plan_path:
        cfg = TelegramApprovalConfig(
            output_dir=str(output_dir),
            max_total_order_value=float(state.get("max_total_order_value") or 0.0),
            max_single_order_value=float(state.get("max_single_order_value") or 0.0),
            max_orders=int(state.get("max_orders") or 0),
            allowed_chat_id=allowed or None,
        )
        errors.extend(validate_plan_limits(plan_path, cfg))
    return errors


def handle_telegram_action(
    *,
    state: dict[str, Any],
    action: str,
    token: str,
    chat_id: str | None,
    output_dir: str | Path,
) -> tuple[dict[str, Any], Path]:
    errors = verify_approval_action(state, action=action, token=token, chat_id=chat_id, output_dir=output_dir)
    audit: dict[str, Any] = {
        "approval_channel": "telegram",
        "action": action,
        "token": token,
        "chat_id": str(chat_id or ""),
        "telegram_chat_id_verified": not any("chat_id" in e for e in errors),
        "plan_hash_verified": not any("hash" in e for e in errors),
        "one_time_token_consumed": False,
        "final_confirm_supplied": False,
        "manual_live_approve_required": True,
        "manual_live_approve_command": build_manual_live_approve_command(str(state.get("plan_path") or ""), output_dir=output_dir),
        "errors": list(errors),
        "live_execution_result": None,
        "kis_post_called": False,
        "status": "APPROVAL_ACTION_BLOCKED" if errors else "APPROVAL_ACTION_ACCEPTED",
    }
    if action == ACTION_DETAILS:
        audit["status"] = "DETAILS_SENT"
        path = write_audit(output_dir, audit)
        return audit, path
    if action == ACTION_HALT_TODAY:
        _halt_path(output_dir).write_text(json.dumps({"active": True, "timestamp": _now().isoformat(timespec="seconds"), "chat_id": str(chat_id or "")}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state["status"] = APPROVAL_STATUS_BLOCKED
        state["action"] = ACTION_HALT_TODAY
        state["consumed_at"] = _now().isoformat(timespec="seconds")
        state["consumed_by_chat_id"] = str(chat_id or "")
        _save_state(output_dir, state)
        audit["one_time_token_consumed"] = True
        audit["status"] = "TODAY_HALTED" if not errors else audit["status"]
        path = write_audit(output_dir, audit)
        return audit, path
    if errors:
        path = write_audit(output_dir, audit)
        return audit, path
    state["consumed_at"] = _now().isoformat(timespec="seconds")
    state["consumed_by_chat_id"] = str(chat_id or "")
    state["action"] = action
    audit["one_time_token_consumed"] = True
    if action == ACTION_REJECT:
        state["status"] = APPROVAL_STATUS_REJECTED
        audit["status"] = "TELEGRAM_APPROVAL_REJECTED"
    elif action == ACTION_APPROVE:
        state["status"] = APPROVAL_STATUS_APPROVED
        state["manual_live_approve_command"] = audit["manual_live_approve_command"]
        audit["status"] = "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED"
    else:
        audit["status"] = "UNKNOWN_ACTION"
        state["status"] = APPROVAL_STATUS_BLOCKED
    _save_state(output_dir, state)
    path = write_audit(output_dir, audit)
    return audit, path


def find_action_from_updates(updates: list[dict[str, Any]], state: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
    expected = str(state.get("token") or "")
    for update in updates:
        action, token, chat_id, callback_id = parse_update_action(update)
        if action and (not expected or token == expected or action == ACTION_HALT_TODAY):
            return action, token or expected, chat_id, callback_id
    return "", "", None, None


def _find_approved_audit(output_dir: str | Path, token: str) -> tuple[Path | None, dict[str, Any] | None]:
    for path in sorted(Path(output_dir).glob("telegram_approval_audit_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(data.get("token") or "") != token:
            continue
        if str(data.get("status") or "") == TELEGRAM_APPROVED_AUDIT_STATUS and not data.get("errors"):
            return path, data
    return None, None


def _state_expired(state: dict[str, Any]) -> bool:
    expires = parse_datetime_with_default_tz(state.get("expires_at"))
    if expires is None:
        return False
    return _now() > expires


@dataclass
class TelegramResolveResult:
    outcome: str
    message: str
    state: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] | None = None
    audit_path: str | None = None


def resolve_telegram_approval_for_execute(
    output_dir: str | Path = "outputs",
    *,
    wait_seconds: float = 60.0,
    poll_interval: float = 2.0,
    bot_token: str | None = None,
    timeout_seconds: float = 5.0,
    allowed_chat_id: str | None = None,
) -> TelegramResolveResult:
    """Poll Telegram updates and resolve approve/reject for execute-last-approved."""
    state = load_latest_request(output_dir)
    if not state or not str(state.get("token") or "").strip():
        return TelegramResolveResult(outcome="no_request", message="Telegram 승인 요청이 없습니다. telegram-approval-request를 먼저 실행하세요.")

    token = str(state["token"])

    if _state_expired(state):
        return TelegramResolveResult(outcome="expired", message="Telegram 승인 만료됨", state=state)

    if str(state.get("status") or "") == APPROVAL_STATUS_REJECTED:
        return TelegramResolveResult(outcome="rejected", message="Telegram 중단 처리됨", state=state)

    audit_path, audit = _find_approved_audit(output_dir, token)
    if str(state.get("status") or "") == APPROVAL_STATUS_APPROVED and audit and audit_path:
        return TelegramResolveResult(
            outcome="approved",
            message="Telegram 승인 확인 완료",
            state=load_latest_request(output_dir),
            audit=audit,
            audit_path=audit_path.as_posix(),
        )

    if str(state.get("status") or "") != APPROVAL_STATUS_PENDING:
        return TelegramResolveResult(
            outcome="blocked",
            message=f"Telegram 승인 상태가 실행 가능하지 않습니다: {state.get('status')}",
            state=state,
        )

    cfg = load_telegram_config_from_env(
        output_dir=str(output_dir),
        timeout_seconds=timeout_seconds,
        allowed_chat_id=allowed_chat_id,
    )
    token_value = bot_token or cfg.bot_token
    processed_callbacks: set[str] = set()
    deadline = time.monotonic() + max(float(wait_seconds), 0.0)
    interval = max(float(poll_interval), 0.1)

    while True:
        updates_payload = telegram_get_updates(bot_token=token_value, timeout_seconds=cfg.timeout_seconds)
        updates = list(updates_payload.get("result") or [])
        for update in updates:
            action, action_token, chat_id, callback_id = parse_update_action(update)
            if not action or action_token != token:
                continue
            dedupe_key = callback_id or f"update:{update.get('update_id')}"
            if dedupe_key in processed_callbacks:
                continue
            processed_callbacks.add(dedupe_key)

            current_state = load_latest_request(output_dir)
            audit_body, audit_file = handle_telegram_action(
                state=current_state,
                action=action,
                token=token,
                chat_id=chat_id,
                output_dir=output_dir,
            )
            if callback_id:
                ack = "승인 확인" if action == ACTION_APPROVE and not audit_body.get("errors") else "처리 완료"
                if action == ACTION_REJECT and not audit_body.get("errors"):
                    ack = "중단 처리"
                telegram_answer_callback(callback_id, bot_token=token_value, text=ack, timeout_seconds=cfg.timeout_seconds)

            refreshed = load_latest_request(output_dir)
            if action == ACTION_REJECT and str(audit_body.get("status") or "") == TELEGRAM_REJECTED_AUDIT_STATUS:
                return TelegramResolveResult(
                    outcome="rejected",
                    message="Telegram 중단 처리됨",
                    state=refreshed,
                    audit=audit_body,
                    audit_path=audit_file.as_posix(),
                )
            if (
                action == ACTION_APPROVE
                and str(audit_body.get("status") or "") == TELEGRAM_APPROVED_AUDIT_STATUS
                and not audit_body.get("errors")
            ):
                return TelegramResolveResult(
                    outcome="approved",
                    message="Telegram 승인 확인 완료",
                    state=refreshed,
                    audit=audit_body,
                    audit_path=audit_file.as_posix(),
                )

        if time.monotonic() >= deadline:
            break
        if float(wait_seconds) <= 0:
            break
        time.sleep(interval)

    return TelegramResolveResult(outcome="pending_timeout", message="Telegram 승인 대기 중입니다", state=load_latest_request(output_dir))


def render_status(state: dict[str, Any]) -> str:
    if not state:
        return "No Telegram approval request found."
    return "\n".join(
        [
            "DeepSignal Telegram approval status",
            f"Status: {state.get('status')}",
            f"Plan: {state.get('plan_path')}",
            f"Token: {state.get('token')}",
            f"Expires: {state.get('expires_at')}",
            f"Orders: {state.get('order_count')}",
            f"Total order value: {state.get('total_order_value')}",
        ]
    )
