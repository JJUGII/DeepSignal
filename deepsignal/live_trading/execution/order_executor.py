"""실전 주문 계획 승인·검증·dry-run / [실전-4] KIS 단발 실매수."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from deepsignal.live_trading.trading_session import TradingSessionPolicy

from deepsignal.live_trading.broker_interface import (
    BrokerInterface,
    BrokerOrderRequest,
    BrokerOrderResult,
)
from deepsignal.live_trading.dry_run_broker import DryRunBroker
from deepsignal.live_trading.kis_broker import KISBroker
from deepsignal.live_trading.kis_config import KISConfig
from deepsignal.live_trading.live_execution_guard import LiveExecutionPolicy, validate_live_execution
from deepsignal.live_trading.live_order_plan import LiveOrderPlan, live_order_plan_from_dict


def load_live_order_plan(path: str | Path) -> LiveOrderPlan:
    """JSON 계획 파일을 읽어 `LiveOrderPlan`으로 복원."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("live order plan root must be a JSON object")
    return live_order_plan_from_dict(data)


def validate_live_order_plan(plan: LiveOrderPlan) -> tuple[bool, list[str]]:
    """계획·주문 검증. 통과 시 (True, [])."""
    errs: list[str] = []
    if plan.status != "PENDING_APPROVAL":
        errs.append(f"plan.status must be PENDING_APPROVAL, got {plan.status!r}")
    if not plan.approval_required:
        errs.append("plan.approval_required must be true for this approval flow")
    if not plan.orders:
        errs.append("orders is empty — nothing to execute")
        return False, errs
    for i, o in enumerate(plan.orders):
        prefix = f"orders[{i}]"
        if (o.side or "").strip().upper() != "BUY":
            errs.append(f"{prefix}: only BUY is allowed, got {o.side!r}")
        if o.estimated_qty <= 0:
            errs.append(f"{prefix}: estimated_qty must be > 0, got {o.estimated_qty}")
        if o.estimated_price <= 0:
            errs.append(f"{prefix}: estimated_price must be > 0, got {o.estimated_price}")
        if o.estimated_order_value <= 0:
            errs.append(
                f"{prefix}: estimated_order_value must be > 0, got {o.estimated_order_value}"
            )
    return (len(errs) == 0, errs)


def build_broker_order_requests(
    plan: LiveOrderPlan,
    *,
    source_plan_id: str | None = None,
) -> list[BrokerOrderRequest]:
    """계획 주문을 `BrokerOrderRequest` 목록으로 변환."""
    sid = source_plan_id or plan.date.replace("-", "")[:8] or "unknown_plan"
    out: list[BrokerOrderRequest] = []
    for o in plan.orders:
        cid = f"lopt_{plan.date}_{o.symbol}_{uuid.uuid4().hex[:8]}"
        out.append(
            BrokerOrderRequest(
                symbol=o.symbol,
                side="BUY",
                quantity=int(o.estimated_qty),
                order_type="LIMIT",
                limit_price=float(o.estimated_price),
                estimated_value=float(o.estimated_order_value),
                client_order_id=cid,
                source_plan_id=sid,
            )
        )
    return out


def _result_to_dict(r: BrokerOrderResult) -> dict[str, Any]:
    return asdict(r)


def _allowed_broker(broker: BrokerInterface) -> bool:
    return isinstance(broker, (DryRunBroker, KISBroker))


def execute_live_order_plan(
    plan_path: str | Path,
    broker: BrokerInterface,
    *,
    approved: bool,
    execute: bool = False,
    dry_run: bool = True,
    final_confirm: str | None = None,
    live_policy: LiveExecutionPolicy | None = None,
    db_path: str | None = None,
    output_dir: str = "outputs",
    stale_snapshot_minutes: int = 10,
    session_now: datetime | None = None,
    session_policy: Any | None = None,
    require_pre_trade_runbook: bool = False,
    pre_trade_runbook_path: str | None = None,
    pre_trade_runbook_max_age_minutes: int = 10,
) -> dict[str, Any]:
    """
    계획 검증 후 브로커 처리.

    - `execute=False`: `DryRunBroker` / `KISBroker`(safe) 기존 경로.
    - `execute=True`: **`KISBroker` + `LiveExecutionGuard` 통과 후에만** `order-cash` POST
      (`KISBroker(..., safe_mode=False).place_order(..., execute=True)`).
    """
    p = Path(plan_path)
    plan_path_str = p.as_posix()
    policy = live_policy or LiveExecutionPolicy()

    base: dict[str, Any] = {
        "plan_path": plan_path_str,
        "approved": approved,
        "dry_run": dry_run,
        "execute": execute,
        "broker": type(broker).__name__,
        "warnings": [],
        "실제_주문_없음": True,
        "final_confirm_matched": (final_confirm or "").strip() == policy.require_final_confirm_text,
        "live_guard_passed": False,
        "preflight_passed": False,
        "live_policy": asdict(policy),
        "kis_env": None,
        "actual_order_attempted": False,
        "actual_order_count": 0,
        "blocked_reason": None,
        "실제_주문_발생_가능성": False,
        "require_pre_trade_runbook": bool(require_pre_trade_runbook),
        "pre_trade_runbook_passed": None,
        "pre_trade_runbook_path": None,
        "pre_trade_runbook_age_seconds": None,
    }

    if not dry_run:
        base.update(
            {
                "success": False,
                "plan_status": None,
                "status": "DRY_RUN_REQUIRED",
                "errors": ["[실전-4]까지도 --no-dry-run 은 거부됩니다."],
                "orders": [],
                "results": [],
            }
        )
        return base

    if not _allowed_broker(broker):
        base.update(
            {
                "success": False,
                "plan_status": None,
                "status": "BROKER_NOT_ALLOWED",
                "errors": ["지원 브로커: dry-run(DryRunBroker) 또는 kis(KISBroker)만 허용됩니다."],
                "orders": [],
                "results": [],
            }
        )
        return base

    if not approved:
        try:
            plan = load_live_order_plan(p)
            ps = plan.status
        except OSError:
            ps = None
        except (json.JSONDecodeError, ValueError):
            ps = None
        base.update(
            {
                "success": False,
                "plan_status": ps,
                "status": "REJECTED_NOT_APPROVED",
                "errors": ["--approved 플래그 없이는 실행할 수 없습니다."],
                "orders": [],
                "results": [],
            }
        )
        return base

    try:
        plan = load_live_order_plan(p)
    except OSError as e:
        base.update(
            {
                "success": False,
                "plan_status": None,
                "status": "LOAD_FAILED",
                "errors": [f"plan file read failed: {e}"],
                "orders": [],
                "results": [],
            }
        )
        return base
    except (json.JSONDecodeError, ValueError) as e:
        base.update(
            {
                "success": False,
                "plan_status": None,
                "status": "LOAD_FAILED",
                "errors": [f"invalid plan JSON: {e}"],
                "orders": [],
                "results": [],
            }
        )
        return base

    base["plan_status"] = plan.status
    ok, verrs = validate_live_order_plan(plan)
    if not ok:
        base.update(
            {
                "success": False,
                "status": "VALIDATION_FAILED",
                "errors": verrs,
                "orders": [],
                "results": [],
            }
        )
        return base

    reqs = build_broker_order_requests(plan, source_plan_id=p.stem)
    base["orders"] = [asdict(r) for r in reqs]

    if execute:
        if not isinstance(broker, KISBroker):
            base.update(
                {
                    "success": False,
                    "status": "EXECUTE_REQUIRES_KIS_BROKER",
                    "errors": ["--execute 실매수는 --broker kis 일 때만 허용됩니다."],
                    "results": [],
                }
            )
            return base

        cfg: KISConfig = broker.config
        base["kis_env"] = cfg.env

        if policy.require_trading_session:
            from deepsignal.live_trading.trading_session import (
                is_trading_session_open,
                load_trading_session_policy_from_env,
                trading_session_result_to_audit_fields,
            )

            sp = session_policy or load_trading_session_policy_from_env()
            sr = is_trading_session_open(now=session_now, policy=sp)
            base.update(trading_session_result_to_audit_fields(sr))

        from deepsignal.live_trading.live_execution_guard import session_blocked_errors_only

        g_ok, g_errs = validate_live_execution(
            plan,
            reqs,
            policy,
            cfg,
            approved=approved,
            execute=execute,
            final_confirm=final_confirm,
            session_now=session_now,
            session_policy=session_policy,
        )
        if not g_ok:
            base["blocked_reason"] = "; ".join(g_errs)
            block_status = (
                "LIVE_EXECUTION_BLOCKED_BY_SESSION"
                if session_blocked_errors_only(g_errs)
                else "LIVE_EXECUTION_BLOCKED"
            )
            base.update(
                {
                    "success": False,
                    "status": block_status,
                    "errors": g_errs,
                    "results": [],
                    "plan_warnings": list(plan.warnings),
                }
            )
            return base

        base["live_guard_passed"] = True

        if require_pre_trade_runbook:
            from deepsignal.live_trading.runbook_guard import (
                runbook_guard_result_to_audit_fields,
                validate_pre_trade_runbook,
            )

            first = reqs[0] if reqs else None
            rg = validate_pre_trade_runbook(
                report_path=pre_trade_runbook_path,
                output_dir=output_dir,
                max_age_minutes=pre_trade_runbook_max_age_minutes,
                expected_plan_path=plan_path_str,
                expected_symbol=first.symbol if first else None,
                expected_quantity=int(first.quantity) if first else None,
                expected_limit_price=float(first.limit_price) if first and first.limit_price is not None else None,
            )
            base.update(runbook_guard_result_to_audit_fields(rg))
            if not rg.passed:
                base["blocked_reason"] = rg.message
                base.update(
                    {
                        "success": False,
                        "status": "LIVE_EXECUTION_BLOCKED_BY_RUNBOOK",
                        "errors": [rg.message],
                        "results": [],
                        "plan_warnings": list(plan.warnings),
                        "preflight_passed": False,
                    }
                )
                return base

        to_send = reqs[: int(policy.max_orders)]
        order_guard_blocked = False
        combined_issues: list[Any] = []
        combined_guard_warnings: list[str] = []
        if db_path:
            from deepsignal.live_trading.order_guard import (
                OrderGuardResult,
                check_duplicate_order_risk,
                load_order_guard_inputs,
                order_guard_result_to_audit_fields,
            )

            for r in to_send:
                recent, reconcile, snap_time, partials = load_order_guard_inputs(
                    db_path,
                    broker="kis",
                    symbol=r.symbol,
                    output_dir=output_dir,
                )
                gr = check_duplicate_order_risk(
                    symbol=r.symbol,
                    side=r.side,
                    quantity=r.quantity,
                    limit_price=r.limit_price,
                    broker="kis",
                    recent_orders=recent,
                    reconcile_result=reconcile,
                    latest_snapshot_time=snap_time,
                    stale_snapshot_minutes=stale_snapshot_minutes,
                    open_partial_fills=partials,
                )
                if gr.blocked:
                    order_guard_blocked = True
                combined_issues.extend(gr.issues)
                for w in gr.warnings:
                    if w not in combined_guard_warnings:
                        combined_guard_warnings.append(w)

            if order_guard_blocked:
                gr_final = OrderGuardResult(
                    blocked=True,
                    issues=combined_issues,
                    warnings=combined_guard_warnings,
                )
                base.update(order_guard_result_to_audit_fields(gr_final))
                base["preflight_passed"] = False
                base["blocked_reason"] = "order guard blocked duplicate or unsafe order risk"
                base.update(
                    {
                        "success": False,
                        "status": "LIVE_ORDER_BLOCKED_BY_GUARD",
                        "errors": [base["blocked_reason"]],
                        "results": [],
                        "plan_warnings": list(plan.warnings),
                    }
                )
                return base

        base["preflight_passed"] = True

        # ── 실시간 호가 괴리 검증 (#5) ──────────────────────────────────
        # yfinance 일봉 종가로 산정된 LIMIT가가 실시간 시세와 크게 벌어지면
        # (갭상승/하락) 미체결·오체결 위험 → KIS POST 직전에 차단한다.
        from deepsignal.live_trading.live_execution_guard import (
            check_order_price_divergence,
            price_divergence_policy_from_env,
        )

        pdp = price_divergence_policy_from_env()
        pd_ok, pd_errs, pd_warns, pd_quotes = check_order_price_divergence(broker, to_send, pdp)
        if pd_quotes:
            base["live_price_quotes"] = pd_quotes
        if pd_warns:
            base.setdefault("price_warnings", []).extend(pd_warns)
        if not pd_ok:
            base["blocked_reason"] = "; ".join(pd_errs)
            base.update(
                {
                    "success": False,
                    "status": "LIVE_ORDER_BLOCKED_PRICE_DIVERGENCE",
                    "errors": pd_errs,
                    "results": [],
                    "plan_warnings": list(plan.warnings),
                }
            )
            return base

        base["actual_order_attempted"] = False
        live_broker = KISBroker(cfg, safe_mode=False, session=broker._session)
        results: list[BrokerOrderResult] = []
        try:
            for r in to_send:
                base["actual_order_attempted"] = True
                results.append(live_broker.place_order(r, execute=True))
        except Exception as e:
            base["results"] = [_result_to_dict(x) for x in results]
            base["actual_order_count"] = len(results)
            base["plan_warnings"] = list(plan.warnings)
            base["blocked_reason"] = f"exception during order send: {e!r}"
            base["실제_주문_발생_가능성"] = False
            base["실제_주문_없음"] = True
            base.update(
                {
                    "success": False,
                    "status": "KIS_LIVE_ORDER_FAILED",
                    "errors": [base["blocked_reason"]],
                }
            )
            return base

        base["actual_order_count"] = len(results)
        base["results"] = [_result_to_dict(x) for x in results]
        base["plan_warnings"] = list(plan.warnings)

        all_ok = all(x.status == "KIS_ORDER_SUBMITTED" for x in results)
        base["실제_주문_발생_가능성"] = all_ok
        base["실제_주문_없음"] = not all_ok
        base.update(
            {
                "success": bool(all_ok),
                "status": "KIS_LIVE_ORDER_COMPLETED" if all_ok else "KIS_LIVE_ORDER_FAILED",
                "errors": [] if all_ok else [x.message for x in results if x.status != "KIS_ORDER_SUBMITTED"],
            }
        )
        return base

    # --- 비 execute: 시뮬레이션 ---
    results = [broker.place_order(r, execute=False) for r in reqs]

    if isinstance(broker, DryRunBroker):
        base.update(
            {
                "success": True,
                "status": "DRY_RUN_COMPLETED",
                "errors": [],
                "plan_warnings": list(plan.warnings),
                "results": [_result_to_dict(x) for x in results],
            }
        )
        return base

    bad = [x for x in results if x.status == "KIS_ORDER_BUILD_FAILED"]
    if bad:
        base.update(
            {
                "success": False,
                "status": "KIS_PLAN_INCOMPATIBLE",
                "errors": [
                    "KIS 국내 현금 LIMIT 경로와 맞지 않는 심볼/계좌 형식이 있습니다. "
                    "종목은 6자리 숫자코드, CANO 8자리·상품코드 2자리를 확인하세요."
                ],
                "plan_warnings": list(plan.warnings),
                "results": [_result_to_dict(x) for x in results],
            }
        )
        return base

    ok_statuses = frozenset({"KIS_SAFE_MODE_BLOCKED"})
    if not results or any(x.status not in ok_statuses for x in results):
        base.update(
            {
                "success": False,
                "status": "KIS_UNEXPECTED_RESULT",
                "errors": ["Unexpected KIS place_order status."],
                "plan_warnings": list(plan.warnings),
                "results": [_result_to_dict(x) for x in results],
            }
        )
        return base

    base.update(
        {
            "success": True,
            "status": "KIS_SAFE_MODE_COMPLETED",
            "errors": [],
            "plan_warnings": list(plan.warnings),
            "results": [_result_to_dict(x) for x in results],
        }
    )
    return base


def write_live_approval_audit_log(
    payload: dict[str, Any],
    *,
    output_dir: str | Path = "outputs",
    audit_filename: str | None = None,
) -> Path:
    """감사 로그 JSON 저장. 타임스탬프는 파일명에 반영 (`audit_filename` 지정 시 그 이름 사용)."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    if audit_filename:
        path = root / audit_filename
    else:
        ymd = now.strftime("%Y%m%d")
        hms = now.strftime("%H%M%S")
        path = root / f"live_approval_audit_{ymd}_{hms}.json"
    body = dict(payload)
    body.setdefault("timestamp", now.isoformat(timespec="seconds"))
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
