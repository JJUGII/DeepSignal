"""하루 운영 점검 dry-run 오케스트레이션 ([실전-22]). 주문 기능 없음."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class OpsDryRunStep:
    name: str
    success: bool
    status: str
    message: str
    output_paths: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class OpsDryRunResult:
    generated_at: str
    network: bool
    success: bool
    final_status: str
    steps: list[OpsDryRunStep]
    warnings: list[str]


STATUS_OK = "OPS_DRY_RUN_OK"
STATUS_WARNING = "OPS_DRY_RUN_WARNING"
STATUS_BLOCKED = "OPS_DRY_RUN_BLOCKED"
STATUS_NO_DATA = "OPS_DRY_RUN_NO_DATA"

RISK_ALERT_STATUSES = {"STOP_LOSS_ALERT", "TAKE_PROFIT_ALERT", "MIXED_ALERT"}


def _filter_noncritical_warnings(warnings: list[str]) -> list[str]:
    noncritical_prefixes = (
        "No data for live_fill_summary_",
    )
    return [w for w in warnings if not any(str(w).startswith(prefix) for prefix in noncritical_prefixes)]


def _step(
    name: str,
    fn,
) -> OpsDryRunStep:
    try:
        return fn()
    except Exception as e:
        return OpsDryRunStep(
            name=name,
            success=False,
            status="ERROR",
            message=str(e),
            warnings=[f"{name} failed: {e}"],
        )


def _decide_final_status(steps: list[OpsDryRunStep], warnings: list[str]) -> tuple[str, bool]:
    if any(not s.success for s in steps):
        return STATUS_BLOCKED, False
    statuses = {str(s.status or "") for s in steps}
    if any(s in RISK_ALERT_STATUSES for s in statuses):
        return STATUS_WARNING, True
    if any(s in {"RECONCILE_MISMATCH", "WARNING"} for s in statuses) or warnings or any(s.warnings for s in steps):
        return STATUS_WARNING, True
    if "NO_DATA" in statuses:
        return STATUS_NO_DATA, True
    return STATUS_OK, True


def _path_dict(**kwargs: Path | None) -> dict[str, str]:
    return {k: v.as_posix() for k, v in kwargs.items() if v is not None}


def run_ops_dry_run(
    *,
    db_path: str,
    output_dir: str | Path = "outputs",
    archive_dir: str | Path | None = None,
    broker: str = "kis",
    network: bool = False,
    recent_orders: int = 10,
    kis_broker: Any | None = None,
) -> OpsDryRunResult:
    """실주문 없이 운영 점검 리포트 묶음을 생성한다."""
    from deepsignal.live_trading.daily_ops_summary import run_daily_ops_summary
    from deepsignal.live_trading.html_dashboard import write_html_dashboard
    from deepsignal.live_trading.kis_config import load_kis_config_from_env, validate_kis_config
    from deepsignal.live_trading.ops_dashboard import run_ops_dashboard
    from deepsignal.live_trading.report_index import run_report_index
    from deepsignal.live_trading.risk_guard import run_portfolio_risk_check
    from deepsignal.live_trading.sell_plan import run_sell_plan
    from deepsignal.live_trading.trading_session import is_trading_session_open, load_trading_session_policy_from_env

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    steps: list[OpsDryRunStep] = []
    warnings: list[str] = []

    def step_session() -> OpsDryRunStep:
        result = is_trading_session_open(policy=load_trading_session_policy_from_env())
        st = "OK" if result.is_open else "WARNING"
        msg = result.reason if result.is_open else f"trading session closed: {result.reason}"
        step_warnings = list(result.warnings)
        if not result.is_open:
            step_warnings.append(msg)
        return OpsDryRunStep("trading_session", True, st, msg, warnings=step_warnings)

    steps.append(_step("trading_session", step_session))

    cfg_holder: dict[str, Any] = {}

    def step_kis_offline() -> OpsDryRunStep:
        cfg = load_kis_config_from_env()
        cfg_holder["cfg"] = cfg
        errs, warns = validate_kis_config(cfg)
        if errs:
            return OpsDryRunStep("kis_check_offline", False, "BLOCKED", "; ".join(errs), warnings=warns + errs)
        return OpsDryRunStep("kis_check_offline", True, "OK", "KIS config valid; no network call", warnings=warns)

    steps.append(_step("kis_check_offline", step_kis_offline))

    br = kis_broker
    if network:
        def step_kis_network() -> OpsDryRunStep:
            nonlocal br
            if br is None:
                from deepsignal.live_trading.kis_broker import KISBroker

                br = KISBroker(cfg_holder.get("cfg") or load_kis_config_from_env(), safe_mode=True)
            br.get_access_token()
            return OpsDryRunStep("kis_check_network", True, "OK", "KIS OAuth checked; no order API")

        steps.append(_step("kis_check_network", step_kis_network))

        def step_account_sync() -> OpsDryRunStep:
            nonlocal br
            if br is None:
                from deepsignal.live_trading.kis_broker import KISBroker

                br = KISBroker(cfg_holder.get("cfg") or load_kis_config_from_env(), safe_mode=True)
            from deepsignal.live_trading.live_account_sync import (
                build_account_snapshot_payload,
                persist_live_account_snapshot_to_db,
                write_live_account_snapshot_paths,
            )

            payload = build_account_snapshot_payload(br)
            jp, mp = write_live_account_snapshot_paths(payload, output_dir=out_dir)
            pos_n, _snap_n, snap_time = persist_live_account_snapshot_to_db(db_path, payload, broker=broker)
            return OpsDryRunStep(
                "account_sync",
                True,
                "OK",
                f"account snapshot saved; positions={pos_n} snapshot={snap_time}",
                output_paths=_path_dict(json=jp, markdown=mp),
            )

        steps.append(_step("account_sync", step_account_sync))

        def step_reconcile() -> OpsDryRunStep:
            nonlocal br
            if br is None:
                from deepsignal.live_trading.kis_broker import KISBroker

                br = KISBroker(cfg_holder.get("cfg") or load_kis_config_from_env(), safe_mode=True)
            from deepsignal.live_trading.fill_tracker import load_open_partial_fill_statuses
            from deepsignal.live_trading.reconcile import reconcile_real_account, write_reconcile_report_paths
            from deepsignal.storage.database import load_latest_real_positions

            broker_pos = br.get_positions()
            db_pos = load_latest_real_positions(db_path, broker=broker)
            result = reconcile_real_account(broker_pos, db_pos)
            step_warnings = list(result.warnings)
            for pfs in load_open_partial_fill_statuses(db_path, broker=broker):
                step_warnings.append(f"open partial fill order_id={pfs.order_id} symbol={pfs.symbol}")
            result.warnings = step_warnings
            jp, mp = write_reconcile_report_paths(
                result,
                output_dir=out_dir,
                extra={"db_path": db_path, "broker": broker, "runbook": "ops_dry_run"},
            )
            status = "OK" if result.success else "RECONCILE_MISMATCH"
            return OpsDryRunStep(
                "reconcile",
                True,
                status,
                "reconcile ok" if result.success else "reconcile mismatch; manual review required",
                output_paths=_path_dict(json=jp, markdown=mp),
                warnings=step_warnings,
            )

        steps.append(_step("reconcile", step_reconcile))

    def step_risk() -> OpsDryRunStep:
        result, _summary, jp, mp = run_portfolio_risk_check(db_path, broker=broker, output_dir=out_dir, write_report=True)
        return OpsDryRunStep(
            "risk_check",
            True,
            result.status,
            f"risk status={result.status}",
            output_paths=_path_dict(json=jp, markdown=mp),
            warnings=list(result.warnings) + list(result.alerts),
        )

    steps.append(_step("risk_check", step_risk))

    def step_ops() -> OpsDryRunStep:
        result, jp, mp = run_ops_dashboard(db_path, output_dir=out_dir, broker=broker, recent_orders=recent_orders)
        return OpsDryRunStep(
            "ops_dashboard",
            True,
            result.status,
            f"ops dashboard status={result.status}",
            output_paths=_path_dict(json=jp, markdown=mp),
            warnings=list(result.warnings),
        )

    steps.append(_step("ops_dashboard", step_ops))

    def step_sell() -> OpsDryRunStep:
        result, jp, mp = run_sell_plan(db_path, output_dir=out_dir, broker=broker)
        return OpsDryRunStep(
            "sell_plan",
            True,
            result.status,
            f"sell plan status={result.status}; manual review only",
            output_paths=_path_dict(json=jp, markdown=mp),
            warnings=list(result.warnings),
        )

    steps.append(_step("sell_plan", step_sell))

    def step_daily() -> OpsDryRunStep:
        result, jp, mp = run_daily_ops_summary(output_dir=out_dir, include_latest_fallback=True)
        return OpsDryRunStep(
            "daily_ops_summary",
            True,
            result.status,
            f"daily ops summary status={result.status}",
            output_paths=_path_dict(json=jp, markdown=mp),
            warnings=list(result.warnings),
        )

    steps.append(_step("daily_ops_summary", step_daily))

    def step_html() -> OpsDryRunStep:
        result = write_html_dashboard(output_dir=out_dir)
        return OpsDryRunStep(
            "html_dashboard",
            True,
            result.status,
            f"html dashboard status={result.status}",
            output_paths={"html": result.html_path},
            warnings=_filter_noncritical_warnings(list(result.warnings)),
        )

    steps.append(_step("html_dashboard", step_html))

    def step_index() -> OpsDryRunStep:
        result, hp, mp, jp = run_report_index(output_dir=out_dir, archive_dir=archive_dir)
        return OpsDryRunStep(
            "report_index",
            True,
            "OK",
            f"report index created; reports={len(result.items)}",
            output_paths=_path_dict(html=hp, markdown=mp, json=jp),
            warnings=list(result.warnings),
        )

    steps.append(_step("report_index", step_index))

    for st in steps:
        warnings.extend(st.warnings)
    final_status, success = _decide_final_status(steps, warnings)
    return OpsDryRunResult(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        network=bool(network),
        success=success,
        final_status=final_status,
        steps=steps,
        warnings=warnings,
    )


def write_ops_dry_run_report(
    result: OpsDryRunResult,
    *,
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    jp = root / f"ops_dry_run_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    mp = root / "OPS_DRY_RUN.md"
    body = asdict(result)
    body["actual_order_attempted"] = False
    body["no_orders_placed"] = True
    body["network_called"] = bool(result.network)
    jp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    generated: dict[str, str] = {}
    for step in result.steps:
        for key, value in step.output_paths.items():
            generated[f"{step.name}_{key}"] = value

    lines = [
        "# DeepSignal Ops Dry Run",
        "",
        "## Summary",
        "",
        f"- Final Status: **{result.final_status}**",
        f"- Network Enabled: {result.network}",
        f"- Generated At: {result.generated_at}",
        "- Mode: dry-run operations check; no orders, SELL automation, or KIS order POST.",
        "",
        "## Steps",
        "",
        "| Step | Success | Status | Message |",
        "|------|---------|--------|---------|",
    ]
    for step in result.steps:
        msg = str(step.message).replace("|", "\\|")
        lines.append(f"| {step.name} | {step.success} | {step.status} | {msg} |")
    lines.extend(["", "## Generated Reports", ""])
    for label in ("html_dashboard_html", "report_index_html", "daily_ops_summary_markdown"):
        lines.append(f"- {label}: `{generated.get(label, '')}`")
    lines.extend(["", "## Warnings", ""])
    if result.warnings:
        for warning in result.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Important", "", "- This command never places orders.", "- It does not automate SELL or KIS order POST."])
    mp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jp, mp


def format_ops_dry_run_console(result: OpsDryRunResult) -> str:
    lines = ["DeepSignal ops dry-run", f"Network: {str(result.network).lower()}"]
    for step in result.steps:
        mark = "OK" if step.success and step.status not in {"WARNING", "RECONCILE_MISMATCH"} else step.status
        if not step.success:
            mark = "FAIL"
        lines.append(f"[{mark}] {step.name}")
    lines.append(f"Final: {result.final_status}")
    return "\n".join(lines)
