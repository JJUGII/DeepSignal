"""Execute a Telegram-approved live order plan.

기본 UX는 ``telegram-approval-request --send`` 가 승인 callback 후 즉시
``execute_live_order_plan``까지 수행한다. ``execute-last-approved``는 legacy/복구용이다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

from deepsignal.live_trading.kis_broker import KISBroker
from deepsignal.live_trading.kis_config import KISConfig, load_kis_config_from_env, validate_kis_config
from deepsignal.live_trading.live_execution_guard import LiveExecutionPolicy
from deepsignal.live_trading.live_order_executor import (
    execute_live_order_plan,
    load_live_order_plan,
    write_live_approval_audit_log,
)
from deepsignal.live_trading.telegram_approval import plan_sha256, telegram_api_post
from deepsignal.live_trading.time_utils import (
    ensure_timezone_aware,
    markdown_timestamp_block,
    now_kst,
    now_kst_iso,
    parse_datetime_with_default_tz,
    stamp_daily_ai_payload,
)

FINAL_CONFIRM_TEXT = "I_UNDERSTAND_REAL_ORDER"
APPROVED_STATUS = "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED"


@dataclass
class ApprovedExecutionRequest:
    request_id: str
    approval_audit_path: str
    state_path: str
    plan_path: str
    plan_hash: str
    expires_at: str
    max_total_order_value: float
    max_single_order_value: float
    max_orders: int
    telegram_chat_id_verified: bool
    one_time_token_consumed: bool
    approval_status: str
    approval_audit: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovedExecutionResult:
    request_id: str
    success: bool
    status: str
    errors: list[str]
    warnings: list[str]
    audit_json_path: str
    audit_markdown_path: str
    live_approval_audit_path: str | None = None
    execution_result: dict[str, Any] = field(default_factory=dict)


def _root(output_dir: str | Path) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_json(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return data


def _state_path(output_dir: str | Path, request_id: str) -> Path:
    return _root(output_dir) / f"telegram_approval_state_{request_id}.json"


def _halt_path(output_dir: str | Path, day: str | None = None) -> Path:
    ymd = (day or datetime.now().strftime("%Y%m%d"))[:8]
    return _root(output_dir) / f"telegram_approval_halt_{ymd}.json"


def _audit_paths(output_dir: str | Path) -> tuple[Path, Path]:
    root = _root(output_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return root / f"execute_approved_audit_{ts}.json", root / "EXECUTE_APPROVED_AUDIT.md"


def _latest_json_path(output_dir: str | Path, pattern: str) -> Path | None:
    paths = sorted(_root(output_dir).glob(pattern))
    return paths[-1] if paths else None


def _is_successful_execute_audit(data: dict[str, Any], request_id: str) -> bool:
    return (
        str(data.get("request_id") or "") == request_id
        and bool(data.get("success"))
        and str(data.get("status") or "") == "EXECUTE_APPROVED_COMPLETED"
    )


def execution_already_completed(output_dir: str | Path, request_id: str) -> str | None:
    for path in sorted(_root(output_dir).glob("execute_approved_audit_*.json")):
        try:
            data = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if _is_successful_execute_audit(data, request_id):
            return path.as_posix()
    return None


def load_latest_telegram_approval(output_dir: str | Path = "outputs") -> ApprovedExecutionRequest:
    latest = _latest_json_path(output_dir, "telegram_approval_audit_*.json")
    if latest is None:
        raise ValueError(
            "승인 audit을 찾을 수 없습니다. telegram-approval-request 후 "
            "execute-last-approved를 실행하세요."
        )
    audit = _read_json(latest)
    request_id = str(audit.get("token") or "").strip()
    if not request_id:
        raise ValueError("최신 승인 audit에 request_id(token)가 없습니다.")
    return execute_request_from_audit(output_dir, request_id=request_id, approval_audit_path=latest, approval_audit=audit)


def execute_request_from_audit(
    output_dir: str | Path,
    *,
    request_id: str,
    approval_audit_path: str | Path,
    approval_audit: dict[str, Any] | None = None,
) -> ApprovedExecutionRequest:
    audit = approval_audit or _read_json(approval_audit_path)
    state_path = _state_path(output_dir, request_id)
    if not state_path.exists():
        raise ValueError(f"승인 state 파일을 찾을 수 없습니다: {state_path.as_posix()}")
    state = _read_json(state_path)
    plan_path = str(state.get("plan_path") or audit.get("plan_path") or "").strip()
    return ApprovedExecutionRequest(
        request_id=request_id,
        approval_audit_path=Path(approval_audit_path).as_posix(),
        state_path=state_path.as_posix(),
        plan_path=plan_path,
        plan_hash=str(state.get("plan_hash") or ""),
        expires_at=str(state.get("expires_at") or ""),
        max_total_order_value=float(state.get("max_total_order_value") or 0.0),
        max_single_order_value=float(state.get("max_single_order_value") or 0.0),
        max_orders=int(state.get("max_orders") or 0),
        telegram_chat_id_verified=bool(audit.get("telegram_chat_id_verified")),
        one_time_token_consumed=bool(audit.get("one_time_token_consumed")),
        approval_status=str(audit.get("status") or ""),
        approval_audit=audit,
        state=state,
    )


def execute_approved_request(output_dir: str | Path = "outputs", *, request_id: str) -> ApprovedExecutionRequest:
    state_path = _state_path(output_dir, request_id)
    if not state_path.exists():
        raise ValueError(f"request-id에 해당하는 승인 state가 없습니다: {request_id}")
    audit_path: Path | None = None
    audit_data: dict[str, Any] | None = None
    for path in sorted(_root(output_dir).glob("telegram_approval_audit_*.json"), reverse=True):
        try:
            data = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if str(data.get("token") or "") == request_id:
            audit_path = path
            audit_data = data
            break
    if audit_path is None or audit_data is None:
        raise ValueError(f"request-id에 해당하는 승인 audit이 없습니다: {request_id}")
    return execute_request_from_audit(output_dir, request_id=request_id, approval_audit_path=audit_path, approval_audit=audit_data)


def validate_plan_hash(req: ApprovedExecutionRequest) -> list[str]:
    if not req.plan_path:
        return ["주문 plan 경로가 없습니다."]
    path = Path(req.plan_path)
    if not path.exists():
        return [f"주문 plan 파일이 없습니다: {req.plan_path}"]
    try:
        actual = plan_sha256(path)
    except OSError as exc:
        return [f"주문 plan hash를 계산할 수 없습니다: {exc}"]
    if actual != req.plan_hash:
        return ["주문 plan hash가 Telegram 승인 시점과 다릅니다. 실행을 중단합니다."]
    return []


def validate_not_expired(req: ApprovedExecutionRequest, *, now: datetime | None = None) -> list[str]:
    expires = parse_datetime_with_default_tz(req.expires_at)
    if expires is None:
        return ["승인 만료 시각을 해석할 수 없습니다."]
    compare_now = now_kst() if now is None else ensure_timezone_aware(now)
    if compare_now > expires:
        return ["Telegram 승인이 만료되었습니다. 새 승인 요청을 생성하세요."]
    return []


def validate_not_halted(output_dir: str | Path) -> list[str]:
    path = _halt_path(output_dir)
    if not path.exists():
        return []
    try:
        data = _read_json(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return ["오늘 전체 중단 상태입니다. halt 파일을 확인하세요."]
    if bool(data.get("active", True)):
        return ["오늘 전체 중단 상태입니다. 실행을 중단합니다."]
    return []


def validate_chat_id_verified(req: ApprovedExecutionRequest) -> list[str]:
    return [] if req.telegram_chat_id_verified else ["Telegram chat_id 검증이 통과하지 않았습니다."]


def validate_telegram_approval(
    req: ApprovedExecutionRequest,
    output_dir: str | Path,
    *,
    freshness_date: str | None = None,
) -> tuple[bool, list[str], dict[str, bool]]:
    checks: dict[str, bool] = {}
    errors: list[str] = []

    checks["approval_status"] = req.approval_status == APPROVED_STATUS
    if not checks["approval_status"]:
        errors.append(f"승인 상태가 실행 가능 상태가 아닙니다: {req.approval_status}")

    checks["state_approved"] = str(req.state.get("status") or "") == "APPROVED"
    if not checks["state_approved"]:
        errors.append(f"승인 state가 APPROVED가 아닙니다: {req.state.get('status')}")

    checks["token_consumed"] = bool(req.one_time_token_consumed)
    if not checks["token_consumed"]:
        errors.append("승인 token이 정상 소비 상태가 아닙니다.")

    checks["chat_id_verified"] = not validate_chat_id_verified(req)
    errors.extend(validate_chat_id_verified(req))

    expiry_errors = validate_not_expired(req)
    checks["not_expired"] = not expiry_errors
    errors.extend(expiry_errors)

    halt_errors = validate_not_halted(output_dir)
    checks["not_halted"] = not halt_errors
    errors.extend(halt_errors)

    hash_errors = validate_plan_hash(req)
    checks["plan_hash"] = not hash_errors
    errors.extend(hash_errors)

    already_path = execution_already_completed(output_dir, req.request_id)
    checks["not_already_executed"] = already_path is None
    if already_path:
        errors.append(f"이미 승인 실행이 완료된 request입니다: {already_path}")

    if req.plan_path and Path(req.plan_path).exists():
        from deepsignal.live_trading.telegram_approval import TelegramApprovalConfig, validate_plan_limits

        limit_errors = validate_plan_limits(
            req.plan_path,
            TelegramApprovalConfig(
                output_dir=str(output_dir),
                max_total_order_value=req.max_total_order_value,
                max_single_order_value=req.max_single_order_value,
                max_orders=req.max_orders,
            ),
        )
        checks["order_limits"] = not limit_errors
        errors.extend(limit_errors)
    else:
        checks["order_limits"] = False

    from deepsignal.live_trading.daily_ai_freshness import validate_execution_freshness

    freshness_errors, freshness_meta = validate_execution_freshness(
        output_dir=output_dir,
        plan_path=req.plan_path,
        freshness_date=freshness_date,
    )
    checks["plan_freshness"] = freshness_meta.get("plan_freshness", {}).get("status") == "FRESH"
    checks["latest_order_plan_freshness"] = freshness_meta.get("latest_order_plan_freshness", {}).get("status") == "FRESH"
    checks["freshness_validation"] = freshness_meta
    errors.extend(freshness_errors)

    return (len(errors) == 0, errors, checks)


def _order_count(plan_path: str | Path) -> int:
    try:
        return len(load_live_order_plan(plan_path).orders)
    except Exception:
        return 0


_EXEC_STATUS_KO: dict[str, str] = {
    "EXECUTE_APPROVED_COMPLETED": "✅ 주문 실행 완료",
    "EXECUTE_APPROVED_BLOCKED": "🚫 안전 검사로 차단됨",
    "EXECUTE_APPROVED_ERROR": "❌ 실행 중 오류 발생",
    "EXECUTE_APPROVED_SKIPPED": "건너뜀",
}

_CHECK_KEY_KO: dict[str, str] = {
    "approval_status": "승인 상태",
    "state_approved": "상태 승인됨",
    "plan_freshness": "계획 파일 최신 여부",
    "latest_order_plan_freshness": "최신 주문안 최신 여부",
    "freshness_validation": "신선도 검증",
    "order_limits": "주문 한도",
    "has_orders": "주문 존재",
    "broker_ok": "브로커 연결",
    "paper_mode": "모의투자 모드",
}


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    raw_status = str(payload.get("status") or "")
    status_ko = _EXEC_STATUS_KO.get(raw_status, raw_status)
    success = payload.get("success")
    success_ko = "✅ 성공" if success is True else ("❌ 실패" if success is False else str(success))
    lines = [
        "# DeepSignal — 주문 실행 이력",
        "",
        *markdown_timestamp_block(),
        "",
        f"- 요청 ID: `{payload.get('request_id')}`",
        f"- 상태: {status_ko}",
        f"- 결과: {success_ko}",
        f"- 주문안 파일: `{payload.get('plan_path')}`",
        f"- 승인 감사 파일: `{payload.get('approval_audit_path')}`",
        f"- 실거래 승인 감사: `{payload.get('live_approval_audit_path') or '-'}`",
        f"- 브로커: {payload.get('broker')}",
        f"- 주문 수: {payload.get('order_count')}",
        f"- 실행 시각: {payload.get('executed_at')}",
        "",
        "## 안전 검사 항목",
        "",
    ]
    checks = payload.get("validation_checks") or {}
    if isinstance(checks, dict):
        for key, value in checks.items():
            if isinstance(value, dict):
                continue  # 중첩 딕셔너리는 생략
            label = _CHECK_KEY_KO.get(key, key)
            val_ko = "✅" if value is True else ("❌" if value is False else str(value))
            lines.append(f"- {label}: {val_ko}")
    errors = payload.get("errors") or []
    if errors:
        lines.extend(["", "## 오류 내용", ""])
        lines.extend(f"- {e}" for e in errors)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_execute_audit(output_dir: str | Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    json_path, md_path = _audit_paths(output_dir)
    stamped = stamp_daily_ai_payload(payload)
    json_path.write_text(json.dumps(stamped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(md_path, stamped)
    return json_path, md_path


def _notify_telegram_result(
    *,
    send: bool,
    bot_token: str | None,
    chat_id: str | None,
    result: ApprovedExecutionResult,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    if not send:
        return {"status": "dry_run", "network_called": False}
    if not chat_id:
        return {"status": "missing_chat_id", "network_called": False}
    text = "\n".join(
        [
            "[DeepSignal Execute Approved]",
            f"Request: {result.request_id}",
            f"Status: {result.status}",
            f"Success: {result.success}",
            f"Audit: {result.audit_json_path}",
        ]
    )
    return telegram_api_post(
        "sendMessage",
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        bot_token=bot_token,
        timeout_seconds=timeout_seconds,
    )


def run_approved_execution(
    req: ApprovedExecutionRequest,
    *,
    output_dir: str | Path = "outputs",
    db_path: str | None = None,
    broker_factory: Callable[[KISConfig], KISBroker] | None = None,
    execute_func: Callable[..., dict[str, Any]] = execute_live_order_plan,
    kis_config_loader: Callable[[], KISConfig] = load_kis_config_from_env,
    send: bool = False,
    bot_token: str | None = None,
    timeout_seconds: float = 5.0,
    freshness_date: str | None = None,
) -> ApprovedExecutionResult:
    executed_at = now_kst_iso()
    ok, errors, checks = validate_telegram_approval(req, output_dir, freshness_date=freshness_date)
    freshness_meta = checks.get("freshness_validation") if isinstance(checks.get("freshness_validation"), dict) else {}
    live_audit_path: str | None = None
    exec_result: dict[str, Any] = {}
    warnings: list[str] = []

    if ok:
        try:
            cfg = kis_config_loader()
            verr, vwarn = validate_kis_config(cfg)
            warnings.extend(vwarn)
            if verr:
                errors.extend(verr)
                ok = False
            else:
                broker = broker_factory(cfg) if broker_factory is not None else KISBroker(cfg, safe_mode=True)
                policy = LiveExecutionPolicy(
                    max_total_order_value=req.max_total_order_value,
                    max_single_order_value=req.max_single_order_value,
                    max_orders=req.max_orders,
                    allow_live_env=True,
                )
                exec_result = execute_func(
                    req.plan_path,
                    broker,
                    approved=True,
                    execute=True,
                    dry_run=True,
                    final_confirm=FINAL_CONFIRM_TEXT,
                    live_policy=policy,
                    db_path=db_path,
                    output_dir=str(output_dir),
                    require_pre_trade_runbook=True,
                )
                live_payload = {
                    **exec_result,
                    "approval_channel": "telegram_terminal_wrapper",
                    "request_id": req.request_id,
                    "telegram_approval_audit_path": req.approval_audit_path,
                    "telegram_state_path": req.state_path,
                    "final_confirm_internal_wrapper": True,
                    "operator_terminal_command_required": True,
                }
                live_audit = write_live_approval_audit_log(live_payload, output_dir=output_dir)
                live_audit_path = live_audit.as_posix()
        except Exception as exc:
            errors.append(f"승인 실행 중 예외가 발생했습니다: {exc!r}")
            ok = False

    success = bool(ok and exec_result.get("success") and exec_result.get("status") == "KIS_LIVE_ORDER_COMPLETED")
    status = "EXECUTE_APPROVED_COMPLETED" if success else "EXECUTE_APPROVED_BLOCKED"
    payload: dict[str, Any] = {
        "request_id": req.request_id,
        "success": success,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "approval_audit_path": req.approval_audit_path,
        "telegram_state_path": req.state_path,
        "plan_path": req.plan_path,
        "plan_hash": req.plan_hash,
        "approval_status": req.approval_status,
        "expiry_validation": checks.get("not_expired"),
        "halt_validation": checks.get("not_halted"),
        "plan_hash_validation": checks.get("plan_hash"),
        "validation_checks": checks,
        "freshness_validation": freshness_meta,
        "stale_reasons": [e for e in errors if "오래" in e or "오늘 기준" in e],
        "execution_result": exec_result,
        "broker": "KISBroker",
        "order_count": _order_count(req.plan_path),
        "executed_at": executed_at,
        "telegram_approval_linkage": {
            "request_id": req.request_id,
            "approval_audit_path": req.approval_audit_path,
            "state_path": req.state_path,
            "chat_id_verified": req.telegram_chat_id_verified,
            "token_consumed": req.one_time_token_consumed,
        },
        "live_approval_audit_path": live_audit_path,
    }
    json_path, md_path = _write_execute_audit(output_dir, payload)
    result = ApprovedExecutionResult(
        request_id=req.request_id,
        success=success,
        status=status,
        errors=errors,
        warnings=warnings,
        audit_json_path=json_path.as_posix(),
        audit_markdown_path=md_path.as_posix(),
        live_approval_audit_path=live_audit_path,
        execution_result=exec_result,
    )
    notify = _notify_telegram_result(
        send=send,
        bot_token=bot_token,
        chat_id=str(req.state.get("allowed_chat_id") or req.approval_audit.get("chat_id") or ""),
        result=result,
        timeout_seconds=timeout_seconds,
    )
    if notify.get("network_called"):
        payload["telegram_result_notification"] = notify
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _blocked_from_telegram_resolve(
    resolved: Any,
    *,
    output_dir: str | Path,
) -> ApprovedExecutionResult:
    errors = [str(resolved.message)]
    if resolved.outcome == "rejected":
        errors.append("Telegram 중단으로 주문 실행이 차단되었습니다.")
    elif resolved.outcome == "expired":
        errors.append("새 telegram-approval-request를 생성하세요.")
    elif resolved.outcome == "pending_timeout":
        errors.append("Telegram에서 승인 버튼을 누른 뒤 다시 실행하세요.")
    request_id = str((resolved.state or {}).get("token") or "unknown")
    payload: dict[str, Any] = {
        "request_id": request_id,
        "success": False,
        "status": "EXECUTE_APPROVED_BLOCKED",
        "errors": errors,
        "warnings": [],
        "approval_audit_path": resolved.audit_path,
        "telegram_state_path": (_root(output_dir) / "TELEGRAM_APPROVAL_STATE.json").as_posix(),
        "plan_path": str((resolved.state or {}).get("plan_path") or ""),
        "telegram_resolve_outcome": resolved.outcome,
        "executed_at": now_kst_iso(),
        "execution_result": {},
        "broker": "KISBroker",
        "order_count": 0,
    }
    json_path, md_path = _write_execute_audit(output_dir, payload)
    return ApprovedExecutionResult(
        request_id=request_id,
        success=False,
        status="EXECUTE_APPROVED_BLOCKED",
        errors=errors,
        warnings=[],
        audit_json_path=json_path.as_posix(),
        audit_markdown_path=md_path.as_posix(),
    )


def execute_last_approved(
    output_dir: str | Path = "outputs",
    *,
    freshness_date: str | None = None,
    wait_seconds: float = 60.0,
    poll_interval: float = 2.0,
    **kwargs: Any,
) -> ApprovedExecutionResult:
    from deepsignal.live_trading.telegram_approval import resolve_telegram_approval_for_execute

    resolved = resolve_telegram_approval_for_execute(
        output_dir,
        wait_seconds=wait_seconds,
        poll_interval=poll_interval,
        bot_token=kwargs.get("bot_token"),
        timeout_seconds=float(kwargs.get("timeout_seconds", 5.0) or 5.0),
    )
    if resolved.outcome != "approved" or not resolved.audit_path or not resolved.audit:
        return _blocked_from_telegram_resolve(resolved, output_dir=output_dir)

    req = execute_request_from_audit(
        output_dir,
        request_id=str(resolved.state.get("token") or ""),
        approval_audit_path=resolved.audit_path,
        approval_audit=resolved.audit,
    )
    return run_approved_execution(
        req,
        output_dir=output_dir,
        freshness_date=freshness_date,
        **kwargs,
    )


def execute_approved_by_request_id(output_dir: str | Path = "outputs", *, request_id: str, **kwargs: Any) -> ApprovedExecutionResult:
    req = execute_approved_request(output_dir, request_id=request_id)
    return run_approved_execution(req, output_dir=output_dir, **kwargs)
