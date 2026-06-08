"""실전 운영 runbook — pre/post trade 절차 오케스트레이션 ([실전-10]). 주문 기능 확대 없음."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from deepsignal.live_trading.broker_interface import BrokerInterface
from deepsignal.live_trading.live_order_executor import load_live_order_plan, validate_live_order_plan
from deepsignal.live_trading.live_order_plan import LiveOrderPlan
from deepsignal.live_trading.order_guard import check_duplicate_order_risk, load_order_guard_inputs
from deepsignal.live_trading.reconcile import ReconcileResult, reconcile_real_account, write_reconcile_report_paths
from deepsignal.live_trading.trading_session import (
    TradingSessionPolicy,
    TradingSessionResult,
    is_trading_session_open,
    load_trading_session_policy_from_env,
)


@dataclass
class RunbookStepResult:
    step_name: str
    success: bool
    status: str
    message: str
    started_at: str
    finished_at: str
    duration_ms: int
    warnings: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunbookResult:
    mode: str
    success: bool
    started_at: str
    finished_at: str
    steps: list[RunbookStepResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    final_status: str = ""
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreTradeRunbookParams:
    broker: str = "kis"
    output_dir: str = "outputs"
    db_path: str = ""
    network: bool = True
    plan_path: str = ""
    symbol: str = ""
    quantity: int = 1
    limit_price: float | None = None
    stale_snapshot_minutes: int = 10
    allow_symbols: list[str] | None = None
    max_single_order_value: float = 100_000.0
    max_total_order_value: float = 200_000.0
    session_now: datetime | None = None
    session_policy: TradingSessionPolicy | None = None
    save_db: bool = True


@dataclass
class PostTradeRunbookParams:
    broker: str = "kis"
    output_dir: str = "outputs"
    db_path: str = ""
    network: bool = True
    audit_path: str | None = None
    order_id: str | None = None
    symbol: str | None = None
    save_db: bool = True
    with_summary: bool = False
    generate_html_dashboard: bool = False


def _norm_symbol(sym: str | None) -> str:
    s = (sym or "").strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _run_step(step_name: str, fn: Callable[[], tuple[bool, str, list[str], dict[str, Any]]]) -> RunbookStepResult:
    started = datetime.now()
    started_at = started.isoformat(timespec="seconds")
    try:
        ok, message, warnings, raw = fn()
    except Exception as e:
        finished = datetime.now()
        return RunbookStepResult(
            step_name=step_name,
            success=False,
            status="ERROR",
            message=str(e),
            started_at=started_at,
            finished_at=finished.isoformat(timespec="seconds"),
            duration_ms=int((finished - started).total_seconds() * 1000),
            warnings=[],
            raw={"exception_type": type(e).__name__},
        )
    finished = datetime.now()
    return RunbookStepResult(
        step_name=step_name,
        success=ok,
        status="OK" if ok else "BLOCKED",
        message=message,
        started_at=started_at,
        finished_at=finished.isoformat(timespec="seconds"),
        duration_ms=int((finished - started).total_seconds() * 1000),
        warnings=list(warnings),
        raw=dict(raw),
    )


def validate_plan_for_runbook(
    plan: LiveOrderPlan,
    *,
    target_symbol: str | None,
    allow_symbols: list[str] | None,
    max_single_order_value: float,
    max_total_order_value: float,
) -> tuple[bool, list[str]]:
    """pre-trade STEP 5: 승인·BUY·화이트리스트·금액 한도."""
    ok, errs = validate_live_order_plan(plan)
    if not ok:
        return False, errs

    allowed: set[str] | None = None
    if allow_symbols is not None:
        allowed = {_norm_symbol(s) for s in allow_symbols if str(s).strip()}

    total_val = 0.0
    tgt = _norm_symbol(target_symbol) if target_symbol else None

    for i, o in enumerate(plan.orders):
        prefix = f"orders[{i}]"
        sym = _norm_symbol(o.symbol)
        side_u = (o.side or "").strip().upper()

        if side_u == "SELL":
            errs.append(f"{prefix}: SELL is not allowed in runbook pre-trade")
        if side_u != "BUY":
            errs.append(f"{prefix}: only BUY allowed, got {o.side!r}")

        if tgt and sym != tgt:
            errs.append(f"{prefix}: symbol {sym!r} does not match --symbol {tgt!r}")

        if allowed is not None and sym not in allowed:
            errs.append(f"{prefix}: symbol {sym!r} not in allow_symbols whitelist")

        ov = float(o.estimated_order_value or 0)
        total_val += ov
        if ov > float(max_single_order_value):
            errs.append(
                f"{prefix}: estimated_order_value {ov} exceeds max_single_order_value="
                f"{max_single_order_value}"
            )

        if not re.fullmatch(r"\d{6}", sym):
            errs.append(f"{prefix}: symbol must be domestic 6-digit code, got {o.symbol!r}")

    if total_val > float(max_total_order_value):
        errs.append(
            f"total estimated order value {total_val} exceeds max_total_order_value="
            f"{max_total_order_value}"
        )

    return (len(errs) == 0, errs)


def build_pre_trade_summary(
    *,
    params: PreTradeRunbookParams,
    session: TradingSessionResult | None,
    reconcile: ReconcileResult | None,
    guard_blocked: bool,
    partial_fill_open: bool,
    plan: LiveOrderPlan | None,
) -> dict[str, Any]:
    """STEP 6 pre-trade 요약."""
    sym = _norm_symbol(params.symbol)
    qty = int(params.quantity)
    lp = params.limit_price
    est_val = float(lp or 0) * qty if lp is not None else None
    if plan and plan.orders:
        o0 = plan.orders[0]
        sym = _norm_symbol(o0.symbol)
        qty = int(o0.estimated_qty)
        est_val = float(o0.estimated_order_value)

    plan_posix = ""
    if params.plan_path:
        try:
            plan_posix = Path(params.plan_path).resolve().as_posix()
        except OSError:
            plan_posix = Path(params.plan_path).as_posix()

    return {
        "symbol": sym,
        "quantity": qty,
        "limit_price": lp,
        "estimated_value": est_val,
        "trading_session_open": bool(session.is_open) if session else None,
        "trading_session_reason": session.reason if session else None,
        "reconcile_success": reconcile.success if reconcile else None,
        "reconcile_matched": list(reconcile.matched) if reconcile else [],
        "duplicate_guard_blocked": guard_blocked,
        "partial_fill_open": partial_fill_open,
        "plan_path": plan_posix or params.plan_path,
        "broker": params.broker,
        "allow_symbols": list(params.allow_symbols or []),
    }


def build_post_trade_summary(
    *,
    order_status_rows: list[dict[str, Any]],
    fill_summaries: list[dict[str, Any]],
    reconcile: ReconcileResult | None,
    snapshot_timestamp: str | None,
    audit_path: str | None,
    risk_summary: dict[str, Any] | None = None,
    generated_reports: dict[str, str] | None = None,
) -> dict[str, Any]:
    """post-trade summary (order·fill·reconcile·risk)."""
    submitted = any(
        str(r.get("status") or "").upper() in ("SUBMITTED", "KIS_ORDER_SUBMITTED", "FILLED", "PARTIAL")
        for r in order_status_rows
    )
    partial = any(bool(fs.get("partially_filled")) for fs in fill_summaries)
    out: dict[str, Any] = {
        "order_submitted": submitted,
        "order_status_count": len(order_status_rows),
        "fill_summary_count": len(fill_summaries),
        "partial_fill_detected": partial,
        "latest_snapshot_time": snapshot_timestamp,
        "reconcile_success": reconcile.success if reconcile else None,
        "reconcile_warnings": list(reconcile.warnings) if reconcile else [],
        "audit_path": audit_path,
        "generated_reports": dict(generated_reports or {}),
    }
    if risk_summary:
        out.update(risk_summary)
    return out


def resolve_post_trade_final_status(
    steps: list[RunbookStepResult],
    warnings: list[str],
    risk_status: str,
) -> tuple[str, bool]:
    """POST_TRADE_OK / WARNING / RISK_ALERT / BLOCKED 판정."""
    from deepsignal.live_trading.risk_guard import (
        RISK_STATUS_WARNING,
        is_risk_alert_status,
    )

    any_failed = any(not s.success for s in steps if s.step_name != "summary")
    if any_failed:
        return "POST_TRADE_BLOCKED", False

    if is_risk_alert_status(risk_status):
        return "POST_TRADE_RISK_ALERT", True

    has_warnings = bool(warnings) or any(s.warnings for s in steps)
    if risk_status == RISK_STATUS_WARNING or has_warnings:
        return "POST_TRADE_WARNING", True

    return "POST_TRADE_OK", True


def _run_report_chain_step(
    step_name: str,
    fn: Callable[[], tuple[str, dict[str, str]]],
) -> tuple[RunbookStepResult, dict[str, str]]:
    """Report chain 단계는 실패해도 post-trade critical failure로 승격하지 않는다."""
    started = datetime.now()
    started_at = started.isoformat(timespec="seconds")
    reports: dict[str, str] = {}
    try:
        message, reports = fn()
        finished = datetime.now()
        return (
            RunbookStepResult(
                step_name=step_name,
                success=True,
                status="OK",
                message=message,
                started_at=started_at,
                finished_at=finished.isoformat(timespec="seconds"),
                duration_ms=int((finished - started).total_seconds() * 1000),
                warnings=[],
                raw={"generated_reports": dict(reports)},
            ),
            reports,
        )
    except Exception as e:
        finished = datetime.now()
        warning = f"{step_name} report generation failed: {e}"
        return (
            RunbookStepResult(
                step_name=step_name,
                success=True,
                status="WARNING",
                message=warning,
                started_at=started_at,
                finished_at=finished.isoformat(timespec="seconds"),
                duration_ms=int((finished - started).total_seconds() * 1000),
                warnings=[warning],
                raw={"exception_type": type(e).__name__},
            ),
            {},
        )


def run_pre_trade_runbook(
    params: PreTradeRunbookParams,
    *,
    broker: BrokerInterface | None = None,
) -> RunbookResult:
    """
    pre-trade 운영 runbook. 실패 시 즉시 중단(stop).

    Steps: session → sync → reconcile → guard → plan → summary.
    """
    started_at = _iso_now()
    steps: list[RunbookStepResult] = []
    warnings: list[str] = []
    session_result: TradingSessionResult | None = None
    reconcile_result: ReconcileResult | None = None
    guard_blocked = False
    partial_fill_open = False
    plan: LiveOrderPlan | None = None
    summary: dict[str, Any] = {}

    def _stop() -> RunbookResult:
        finished_at = _iso_now()
        summary = build_pre_trade_summary(
            params=params,
            session=session_result,
            reconcile=reconcile_result,
            guard_blocked=guard_blocked,
            partial_fill_open=partial_fill_open,
            plan=plan,
        )
        return RunbookResult(
            mode="pre_trade",
            success=False,
            started_at=started_at,
            finished_at=finished_at,
            steps=steps,
            warnings=warnings,
            final_status="PRE_TRADE_BLOCKED",
            summary=summary,
        )

    # STEP 1 — trading session
    def _step_session() -> tuple[bool, str, list[str], dict[str, Any]]:
        nonlocal session_result
        sp = params.session_policy or load_trading_session_policy_from_env()
        session_result = is_trading_session_open(now=params.session_now, policy=sp)
        raw = asdict(session_result)
        if session_result.is_open:
            return True, session_result.reason, list(session_result.warnings), raw
        return False, f"trading session closed: {session_result.reason}", [], raw

    s1 = _run_step("trading_session", _step_session)
    steps.append(s1)
    warnings.extend(s1.warnings)
    if not s1.success:
        return _stop()

    if not params.network:
        s_net = RunbookStepResult(
            step_name="network_required",
            success=False,
            status="BLOCKED",
            message="--network is required for account sync and reconcile",
            started_at=_iso_now(),
            finished_at=_iso_now(),
            duration_ms=0,
        )
        steps.append(s_net)
        return _stop()

    if broker is None:
        from deepsignal.live_trading.kis_broker import KISBroker
        from deepsignal.live_trading.kis_config import load_kis_config_from_env

        cfg = load_kis_config_from_env()
        broker = KISBroker(cfg, safe_mode=True)

    db_path = params.db_path
    if not db_path:
        from deepsignal.config.settings import load_settings
        from deepsignal.storage.database import init_database

        db_path = str(init_database(load_settings().db_path))

    # STEP 2 — account sync
    def _step_sync() -> tuple[bool, str, list[str], dict[str, Any]]:
        from deepsignal.live_trading.live_account_sync import (
            build_account_snapshot_payload,
            persist_live_account_snapshot_to_db,
            write_live_account_snapshot_paths,
        )

        payload = build_account_snapshot_payload(broker)  # type: ignore[arg-type]
        jp, mp = write_live_account_snapshot_paths(payload, output_dir=params.output_dir)
        snap_time = str(payload.get("timestamp") or "")
        pos_n = 0
        if params.save_db:
            pos_n, _, snap_time = persist_live_account_snapshot_to_db(
                db_path, payload, broker=params.broker
            )
        return (
            True,
            f"account snapshot saved ({len(payload.get('positions') or [])} positions)",
            [],
            {"json_path": jp.as_posix(), "md_path": mp.as_posix(), "snapshot_time": snap_time, "positions_saved": pos_n},
        )

    s2 = _run_step("account_sync", _step_sync)
    steps.append(s2)
    warnings.extend(s2.warnings)
    if not s2.success:
        return _stop()

    # STEP 3 — reconcile
    def _step_reconcile() -> tuple[bool, str, list[str], dict[str, Any]]:
        nonlocal reconcile_result
        from deepsignal.live_trading.fill_tracker import load_open_partial_fill_statuses
        from deepsignal.storage.database import load_latest_real_positions

        broker_pos = broker.get_positions()
        db_pos = load_latest_real_positions(db_path, broker=params.broker)
        reconcile_result = reconcile_real_account(broker_pos, db_pos)
        step_warnings: list[str] = []
        for pfs in load_open_partial_fill_statuses(db_path, broker=params.broker):
            step_warnings.append(
                f"open partial fill order_id={pfs.order_id} symbol={pfs.symbol} "
                f"remaining_qty={pfs.remaining_quantity}"
            )
        reconcile_result.warnings.extend(step_warnings)
        jp, mp = write_reconcile_report_paths(
            reconcile_result,
            output_dir=params.output_dir,
            extra={"db_path": db_path, "broker": params.broker, "runbook": "pre_trade"},
        )
        raw = {
            "success": reconcile_result.success,
            "json_path": jp.as_posix(),
            "md_path": mp.as_posix(),
            "matched": reconcile_result.matched,
        }
        if reconcile_result.success:
            return True, "reconcile matched", step_warnings, raw
        return False, "reconcile mismatch — do not proceed with live-approve", step_warnings, raw

    s3 = _run_step("reconcile", _step_reconcile)
    steps.append(s3)
    warnings.extend(s3.warnings)
    if not s3.success:
        return _stop()

    # STEP 4 — order guard
    def _step_guard() -> tuple[bool, str, list[str], dict[str, Any]]:
        nonlocal guard_blocked, partial_fill_open
        sym = _norm_symbol(params.symbol)
        if not sym:
            return False, "--symbol is required for order guard check", [], {}
        recent, rec, snap_time, partials = load_order_guard_inputs(
            db_path,
            broker=params.broker,
            symbol=sym,
            output_dir=params.output_dir,
        )
        if rec is None and reconcile_result is not None:
            rec = reconcile_result
        result = check_duplicate_order_risk(
            symbol=sym,
            side="BUY",
            quantity=int(params.quantity),
            limit_price=params.limit_price,
            broker=params.broker,
            recent_orders=recent,
            reconcile_result=rec,
            latest_snapshot_time=snap_time,
            stale_snapshot_minutes=params.stale_snapshot_minutes,
            open_partial_fills=partials,
        )
        guard_blocked = result.blocked
        partial_fill_open = any(i.issue_type == "partial_fill_open" for i in result.issues)
        raw = {"blocked": result.blocked, "issues": [asdict(x) for x in result.issues]}
        if result.blocked:
            msgs = [i.message for i in result.issues if i.severity == "HIGH"]
            return False, "; ".join(msgs) or "order guard blocked", list(result.warnings), raw
        return True, "no duplicate order risk detected", list(result.warnings), raw

    s4 = _run_step("duplicate_guard", _step_guard)
    steps.append(s4)
    warnings.extend(s4.warnings)
    if not s4.success:
        return _stop()

    # STEP 5 — plan validation
    def _step_plan() -> tuple[bool, str, list[str], dict[str, Any]]:
        nonlocal plan
        p = Path(params.plan_path)
        if not p.is_file():
            return False, f"plan file not found: {p}", [], {}
        plan = load_live_order_plan(p)
        ok, errs = validate_plan_for_runbook(
            plan,
            target_symbol=params.symbol,
            allow_symbols=params.allow_symbols,
            max_single_order_value=params.max_single_order_value,
            max_total_order_value=params.max_total_order_value,
        )
        raw = {"plan_path": p.as_posix(), "errors": errs}
        if ok:
            return True, "plan validation passed", [], raw
        return False, "; ".join(errs), [], raw

    s5 = _run_step("plan_validation", _step_plan)
    steps.append(s5)
    warnings.extend(s5.warnings)
    if not s5.success:
        return _stop()

    # STEP 6 — summary
    finished = _iso_now()
    summary = build_pre_trade_summary(
        params=params,
        session=session_result,
        reconcile=reconcile_result,
        guard_blocked=guard_blocked,
        partial_fill_open=partial_fill_open,
        plan=plan,
    )
    summary["generated_at"] = finished
    summary["finished_at"] = finished

    def _step_summary() -> tuple[bool, str, list[str], dict[str, Any]]:
        return True, "PRE_TRADE_READY", [], dict(summary)

    s6 = _run_step("summary", _step_summary)
    steps.append(s6)
    warnings.extend(s6.warnings)

    return RunbookResult(
        mode="pre_trade",
        success=True,
        started_at=started_at,
        finished_at=_iso_now(),
        steps=steps,
        warnings=warnings,
        final_status="PRE_TRADE_READY",
        summary=summary,
    )


def run_post_trade_runbook(
    params: PostTradeRunbookParams,
    *,
    broker: BrokerInterface | None = None,
    risk_policy: Any | None = None,
) -> RunbookResult:
    """post-trade 운영 runbook. 모든 단계 실행 후 OK/WARNING/BLOCKED 판정."""
    started_at = _iso_now()
    steps: list[RunbookStepResult] = []
    warnings: list[str] = []
    order_rows: list[dict[str, Any]] = []
    fill_summaries: list[dict[str, Any]] = []
    reconcile_result: ReconcileResult | None = None
    snapshot_time: str | None = None
    generated_reports: dict[str, str] = {}

    if not params.network:
        s_net = RunbookStepResult(
            step_name="network_required",
            success=False,
            status="BLOCKED",
            message="--network is required for post-trade runbook",
            started_at=_iso_now(),
            finished_at=_iso_now(),
            duration_ms=0,
        )
        steps.append(s_net)
        summary = build_post_trade_summary(
            order_status_rows=[],
            fill_summaries=[],
            reconcile=None,
            snapshot_timestamp=None,
            audit_path=params.audit_path,
            generated_reports=generated_reports,
        )
        return RunbookResult(
            mode="post_trade",
            success=False,
            started_at=started_at,
            finished_at=_iso_now(),
            steps=steps,
            warnings=warnings,
            final_status="POST_TRADE_BLOCKED",
            summary=summary,
        )

    db_path = params.db_path
    if not db_path:
        from deepsignal.config.settings import load_settings
        from deepsignal.storage.database import init_database

        db_path = str(init_database(load_settings().db_path))

    if broker is None:
        from deepsignal.live_trading.kis_broker import KISBroker
        from deepsignal.live_trading.kis_config import load_kis_config_from_env

        cfg = load_kis_config_from_env()
        broker = KISBroker(cfg, safe_mode=True)

    order_ids: list[str] = []
    if params.order_id:
        order_ids.append(str(params.order_id).strip())
    if params.audit_path:
        from deepsignal.live_trading.kis_order_status import extract_order_ids_from_audit, load_audit_log

        audit = load_audit_log(params.audit_path)
        for oid in extract_order_ids_from_audit(audit):
            if oid not in order_ids:
                order_ids.append(oid)

    # STEP 1 — order status
    def _step_order_status() -> tuple[bool, str, list[str], dict[str, Any]]:
        nonlocal order_rows, fill_summaries
        from dataclasses import asdict as dc_asdict

        from deepsignal.live_trading.fill_tracker import (
            extract_fills_from_kis_status_dicts,
            fill_summary_for_display,
            partial_fill_status_from_kis_status,
            persist_fill_records_to_db,
        )

        if not order_ids and not params.symbol:
            return (
                False,
                "--audit or --order-id (or --symbol with network) required for order status",
                [],
                {},
            )

        kis_rows: list[dict[str, Any]] = []
        seen_fill_orders: set[str] = set()
        for oid in order_ids or [None]:
            for st in broker.get_order_status(
                order_id=str(oid).strip() if oid else None,
                symbol=str(params.symbol).strip() if params.symbol else None,
            ):
                d = dc_asdict(st)
                row = {
                    "order_id": d.get("order_id"),
                    "symbol": d.get("symbol"),
                    "side": d.get("side"),
                    "status": d.get("status"),
                    "quantity": d.get("quantity"),
                    "filled_quantity": d.get("filled_quantity"),
                    "remaining_quantity": d.get("remaining_quantity"),
                    "raw": d.get("raw"),
                }
                kis_rows.append(row)
                pfs = partial_fill_status_from_kis_status(row)
                if pfs and str(pfs.order_id or "") not in seen_fill_orders:
                    seen_fill_orders.add(str(pfs.order_id or ""))
                    fill_summaries.append(fill_summary_for_display(pfs))

        order_rows = kis_rows
        fill_recs = extract_fills_from_kis_status_dicts(kis_rows)
        ins, sk = persist_fill_records_to_db(db_path, fill_recs)
        return (
            True,
            f"queried {len(kis_rows)} order status row(s)",
            [],
            {"rows": len(kis_rows), "fills_inserted": ins, "fills_skipped": sk},
        )

    s1 = _run_step("order_status", _step_order_status)
    steps.append(s1)
    warnings.extend(s1.warnings)

    # STEP 2 — fill summary (DB)
    def _step_fill_summary() -> tuple[bool, str, list[str], dict[str, Any]]:
        from deepsignal.live_trading.fill_tracker import (
            build_partial_fill_status,
            fill_summary_for_display,
        )
        from deepsignal.storage.database import aggregate_fill_summary

        if not order_ids:
            if not fill_summaries:
                return True, "no order ids — fill summary skipped", [], {}
            return True, f"{len(fill_summaries)} fill summary from status step", [], {}

        extra: list[dict[str, Any]] = []
        for oid in order_ids:
            agg = aggregate_fill_summary(
                db_path,
                broker=params.broker,
                order_id=oid,
                symbol=str(params.symbol).strip() if params.symbol else None,
            )
            pfs = build_partial_fill_status(agg, order_id=oid, symbol=params.symbol)
            extra.append(fill_summary_for_display(pfs))
        if extra:
            fill_summaries.extend(extra)
        return True, f"{len(fill_summaries)} fill summary record(s)", [], {"count": len(fill_summaries)}

    s2 = _run_step("fill_summary", _step_fill_summary)
    steps.append(s2)
    warnings.extend(s2.warnings)

    # STEP 3 — account sync
    def _step_sync() -> tuple[bool, str, list[str], dict[str, Any]]:
        nonlocal snapshot_time
        from deepsignal.live_trading.live_account_sync import (
            build_account_snapshot_payload,
            persist_live_account_snapshot_to_db,
            write_live_account_snapshot_paths,
        )

        payload = build_account_snapshot_payload(broker)  # type: ignore[arg-type]
        write_live_account_snapshot_paths(payload, output_dir=params.output_dir)
        snapshot_time = str(payload.get("timestamp") or "")
        if params.save_db:
            _, _, snapshot_time = persist_live_account_snapshot_to_db(
                db_path, payload, broker=params.broker
            )
        return True, "account snapshot refreshed", [], {"snapshot_time": snapshot_time}

    s3 = _run_step("account_sync", _step_sync)
    steps.append(s3)
    warnings.extend(s3.warnings)

    # STEP 4 — reconcile
    def _step_reconcile() -> tuple[bool, str, list[str], dict[str, Any]]:
        nonlocal reconcile_result
        from deepsignal.live_trading.fill_tracker import load_open_partial_fill_statuses
        from deepsignal.storage.database import load_latest_real_positions

        broker_pos = broker.get_positions()
        db_pos = load_latest_real_positions(db_path, broker=params.broker)
        reconcile_result = reconcile_real_account(broker_pos, db_pos)
        step_warnings: list[str] = list(reconcile_result.warnings)
        for pfs in load_open_partial_fill_statuses(db_path, broker=params.broker):
            step_warnings.append(
                f"open partial fill order_id={pfs.order_id} symbol={pfs.symbol}"
            )
        reconcile_result.warnings = step_warnings
        write_reconcile_report_paths(
            reconcile_result,
            output_dir=params.output_dir,
            extra={"db_path": db_path, "broker": params.broker, "runbook": "post_trade"},
        )
        if reconcile_result.success:
            return True, "reconcile ok", step_warnings, {"success": True}
        return False, "reconcile mismatch after trade", step_warnings, {"success": False}

    s4 = _run_step("reconcile", _step_reconcile)
    steps.append(s4)
    warnings.extend(s4.warnings)

    risk_summary: dict[str, Any] = {
        "risk_status": "OK",
        "stop_loss_alert_count": 0,
        "take_profit_alert_count": 0,
        "warning_count": 0,
        "risk_report_path": "",
        "risk_alerts": [],
    }

    # STEP 5 — risk check (same logic as `risk-check` CLI)
    def _step_risk_check() -> tuple[bool, str, list[str], dict[str, Any]]:
        from deepsignal.live_trading.risk_guard import (
            RISK_STATUS_OK,
            RISK_STATUS_WARNING,
            is_risk_alert_status,
            run_portfolio_risk_check,
        )

        nonlocal risk_summary
        risk_result, risk_summary, jp, _mp = run_portfolio_risk_check(
            db_path,
            broker=params.broker,
            output_dir=params.output_dir,
            policy=risk_policy,
            write_report=True,
        )
        risk_summary["risk_policy"] = dict(risk_result.policy)
        raw = dict(risk_summary)
        raw["json_path"] = jp.as_posix() if jp else ""
        if jp:
            risk_summary["risk_report_path"] = jp.as_posix()
            generated_reports["risk_report"] = jp.as_posix()
        if _mp:
            generated_reports["risk_report_md"] = _mp.as_posix()
        status = str(risk_summary.get("risk_status") or RISK_STATUS_OK)
        step_warnings = list(risk_result.warnings)
        if is_risk_alert_status(status):
            msg = f"risk alert: {status}"
            return True, msg, step_warnings, raw
        if status == RISK_STATUS_WARNING:
            return True, "risk warning — manual review recommended", step_warnings, raw
        return True, "risk check ok", step_warnings, raw

    started_risk = datetime.now()
    started_risk_at = started_risk.isoformat(timespec="seconds")
    try:
        ok_r, msg_r, warn_r, raw_r = _step_risk_check()
        finished_risk = datetime.now()
        risk_status_val = str(risk_summary.get("risk_status") or "OK")
        from deepsignal.live_trading.risk_guard import is_risk_alert_status

        if not ok_r:
            step_status = "BLOCKED"
        elif is_risk_alert_status(risk_status_val) or risk_status_val == "WARNING":
            step_status = "WARNING"
        else:
            step_status = "OK"
        s_risk = RunbookStepResult(
            step_name="risk_check",
            success=ok_r,
            status=step_status,
            message=msg_r,
            started_at=started_risk_at,
            finished_at=finished_risk.isoformat(timespec="seconds"),
            duration_ms=int((finished_risk - started_risk).total_seconds() * 1000),
            warnings=list(warn_r),
            raw=dict(raw_r),
        )
    except Exception as e:
        finished_risk = datetime.now()
        s_risk = RunbookStepResult(
            step_name="risk_check",
            success=False,
            status="ERROR",
            message=str(e),
            started_at=started_risk_at,
            finished_at=finished_risk.isoformat(timespec="seconds"),
            duration_ms=int((finished_risk - started_risk).total_seconds() * 1000),
            warnings=[],
            raw={"exception_type": type(e).__name__},
        )
    steps.append(s_risk)
    warnings.extend(s_risk.warnings)

    if params.with_summary:
        def _chain_ops_dashboard() -> tuple[str, dict[str, str]]:
            from deepsignal.live_trading.ops_dashboard import run_ops_dashboard

            _result, jp, mp = run_ops_dashboard(
                db_path,
                output_dir=params.output_dir,
                broker=params.broker,
            )
            return "ops dashboard generated", {
                "ops_dashboard_json": jp.as_posix(),
                "ops_dashboard_md": mp.as_posix(),
            }

        s_ops, rep_ops = _run_report_chain_step("ops_dashboard", _chain_ops_dashboard)
        steps.append(s_ops)
        warnings.extend(s_ops.warnings)
        generated_reports.update(rep_ops)

        def _chain_sell_plan() -> tuple[str, dict[str, str]]:
            from deepsignal.live_trading.sell_plan import run_sell_plan

            _result, jp, mp = run_sell_plan(
                db_path,
                output_dir=params.output_dir,
                broker=params.broker,
            )
            return "sell plan generated", {
                "sell_plan_json": jp.as_posix(),
                "sell_plan_md": mp.as_posix(),
            }

        s_sell, rep_sell = _run_report_chain_step("sell_plan", _chain_sell_plan)
        steps.append(s_sell)
        warnings.extend(s_sell.warnings)
        generated_reports.update(rep_sell)

        def _chain_daily_ops_summary() -> tuple[str, dict[str, str]]:
            from deepsignal.live_trading.daily_ops_summary import run_daily_ops_summary

            _result, jp, mp = run_daily_ops_summary(
                output_dir=params.output_dir,
                include_latest_fallback=True,
                notify_dry_run=False,
            )
            return "daily ops summary generated", {
                "daily_ops_summary_json": jp.as_posix(),
                "daily_ops_summary_md": mp.as_posix(),
            }

        s_daily, rep_daily = _run_report_chain_step("daily_ops_summary", _chain_daily_ops_summary)
        steps.append(s_daily)
        warnings.extend(s_daily.warnings)
        generated_reports.update(rep_daily)

        if params.generate_html_dashboard:
            def _chain_html_dashboard() -> tuple[str, dict[str, str]]:
                from deepsignal.live_trading.html_dashboard import write_html_dashboard

                result = write_html_dashboard(output_dir=params.output_dir)
                return "html dashboard generated", {"html_dashboard": result.html_path}

            s_html, rep_html = _run_report_chain_step("html_dashboard", _chain_html_dashboard)
            steps.append(s_html)
            warnings.extend(s_html.warnings)
            generated_reports.update(rep_html)

    summary = build_post_trade_summary(
        order_status_rows=order_rows,
        fill_summaries=fill_summaries,
        reconcile=reconcile_result,
        snapshot_timestamp=snapshot_time,
        audit_path=params.audit_path,
        risk_summary=risk_summary,
        generated_reports=generated_reports,
    )

    def _step_summary() -> tuple[bool, str, list[str], dict[str, Any]]:
        return True, "post-trade summary complete", [], dict(summary)

    s6 = _run_step("summary", _step_summary)
    steps.append(s6)

    risk_status = str(risk_summary.get("risk_status") or "OK")
    final, ok = resolve_post_trade_final_status(steps, warnings, risk_status)

    return RunbookResult(
        mode="post_trade",
        success=ok,
        started_at=started_at,
        finished_at=_iso_now(),
        steps=steps,
        warnings=warnings,
        final_status=final,
        summary=summary,
    )


def write_runbook_report(result: RunbookResult, *, output_dir: str | Path) -> tuple[Path, Path]:
    """`outputs/*_trade_runbook_*.json` 및 `PRE/POST_TRADE_RUNBOOK.md` 저장."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ymd = now.strftime("%Y%m%d")
    hms = now.strftime("%H%M%S")
    prefix = "pre_trade" if result.mode == "pre_trade" else "post_trade"
    json_path = root / f"{prefix}_runbook_{ymd}_{hms}.json"
    md_name = "PRE_TRADE_RUNBOOK.md" if result.mode == "pre_trade" else "POST_TRADE_RUNBOOK.md"
    md_path = root / md_name

    body: dict[str, Any] = {
        "mode": result.mode,
        "success": result.success,
        "final_status": result.final_status,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "warnings": result.warnings,
        "summary": result.summary,
        "steps": [asdict(s) for s in result.steps],
    }
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# DeepSignal — {'Pre' if result.mode == 'pre_trade' else 'Post'}-trade runbook",
        "",
        f"- Generated: {result.finished_at}",
        f"- Final status: **{result.final_status}**",
        f"- Success: **{result.success}**",
        "",
        "## Steps",
        "",
    ]
    for s in result.steps:
        mark = "OK" if s.success else "FAIL"
        lines.append(f"- [{mark}] **{s.step_name}** — {s.message} ({s.duration_ms}ms)")
        for w in s.warnings:
            lines.append(f"  - warning: {w}")
    lines.extend(["", "## Warnings", ""])
    for w in result.warnings:
        lines.append(f"- {w}")
    if not result.warnings:
        lines.append("- (none)")
    if result.mode == "post_trade" and result.summary.get("risk_status") is not None:
        lines.extend(
            [
                "",
                "## Risk Summary",
                "",
                f"- Status: **{result.summary.get('risk_status', 'OK')}**",
                f"- Stop-loss alerts: {result.summary.get('stop_loss_alert_count', 0)}",
                f"- Take-profit alerts: {result.summary.get('take_profit_alert_count', 0)}",
                f"- Warnings: {result.summary.get('warning_count', 0)}",
            ]
        )
        rp = result.summary.get("risk_report_path") or ""
        if rp:
            lines.append(f"- Risk report: `{rp}`")
        risk_alerts = result.summary.get("risk_alerts") or []
        if risk_alerts:
            lines.append("")
            lines.append("### Risk alerts")
            for a in risk_alerts:
                lines.append(f"- {a}")
        elif not risk_alerts and result.summary.get("risk_status") == "OK":
            lines.append("")
            lines.append("- (no risk alerts)")
        risk_policy = result.summary.get("risk_policy") or {}
        if isinstance(risk_policy, dict) and risk_policy:
            def _fmt_pct(value: Any) -> str:
                try:
                    pct = float(value) * 100
                except (TypeError, ValueError):
                    return str(value)
                if pct.is_integer():
                    return f"{pct:.0f}%"
                return f"{pct:.2f}%"

            lines.extend(
                [
                    "",
                    "## Risk Policy",
                    "",
                    f"- Stop loss: {_fmt_pct(risk_policy.get('stop_loss_pct'))}",
                    f"- Take profit: {_fmt_pct(risk_policy.get('take_profit_pct'))}",
                    f"- Warn loss: {_fmt_pct(risk_policy.get('warn_loss_pct'))}",
                    f"- Warn profit: {_fmt_pct(risk_policy.get('warn_profit_pct'))}",
                ]
            )
    generated_reports = result.summary.get("generated_reports") or {}
    if result.mode == "post_trade" and isinstance(generated_reports, dict):
        lines.extend(["", "## Generated Reports", ""])
        report_labels = [
            ("Risk", "risk_report"),
            ("Ops Dashboard", "ops_dashboard_json"),
            ("Sell Plan", "sell_plan_json"),
            ("Daily Summary", "daily_ops_summary_json"),
            ("HTML Dashboard", "html_dashboard"),
        ]
        for label, key in report_labels:
            value = generated_reports.get(key) or ""
            lines.append(f"- {label}: `{value}`" if value else f"- {label}: -")
    lines.extend(["", "## Summary", "", "```json"])
    lines.append(json.dumps(result.summary, ensure_ascii=False, indent=2))
    lines.append("```")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def format_runbook_console(result: RunbookResult) -> str:
    """CLI용 한 줄 요약."""
    title = "pre-trade" if result.mode == "pre_trade" else "post-trade"
    lines = [f"DeepSignal {title} runbook", ""]
    for s in result.steps:
        if s.status == "WARNING":
            mark = "WARNING"
        elif s.success:
            mark = "OK"
        else:
            mark = "FAIL"
        lines.append(f"[{mark}] {s.step_name}")
    lines.append("")
    lines.append(result.final_status)
    if result.mode == "post_trade":
        risk_alerts = result.summary.get("risk_alerts") or []
        if risk_alerts:
            lines.append("")
            lines.append("Risk:")
            for a in risk_alerts:
                lines.append(f"- {a}")
        generated_reports = result.summary.get("generated_reports") or {}
        if isinstance(generated_reports, dict) and generated_reports.get("html_dashboard"):
            lines.append("")
            lines.append(f"HTML Dashboard: {generated_reports.get('html_dashboard')}")
    return "\n".join(lines)
