"""Alert-only notification center ([실전-16]). 주문 실행 없음."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_RISK_ALERT = "RISK_ALERT"
SEVERITY_CRITICAL = "CRITICAL"

RISK_ALERT_STATUSES = {"STOP_LOSS_ALERT", "TAKE_PROFIT_ALERT", "MIXED_ALERT"}
OPS_ALERT_STATUSES = {"WARNING", "RISK_ALERT", "RECONCILE_MISMATCH"}
SELL_PLAN_ALERT_STATUSES = {"REVIEW", "REDUCE", "EXIT"}
MAINTENANCE_WARNING_STATUSES = {"WEEKLY_MAINTENANCE_WARNING"}
MAINTENANCE_CRITICAL_STATUSES = {"WEEKLY_MAINTENANCE_CRITICAL"}
MAINTENANCE_OK_STATUSES = {"WEEKLY_MAINTENANCE_OK"}
HEALTH_WARNING_STATUSES = {"HEALTH_WARNING", "HEALTH_NO_DATA"}
HEALTH_CRITICAL_STATUSES = {"HEALTH_CRITICAL"}
HEALTH_OK_STATUSES = {"HEALTH_OK"}


@dataclass
class AlertMessage:
    title: str
    severity: str
    body: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotificationResult:
    channel: str
    success: bool
    status: str
    message: str
    raw: dict[str, Any] = field(default_factory=dict)


def _latest_json(output_dir: str | Path, pattern: str) -> dict[str, Any]:
    paths = sorted(Path(output_dir).glob(pattern))
    if not paths:
        return {}
    path = paths[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"_parse_error": True, "_path": path.as_posix()}
    if isinstance(data, dict):
        data["_path"] = path.as_posix()
        return data
    return {"_non_object_json": True, "_path": path.as_posix()}


def load_latest_alert_sources(
    output_dir: str | Path = "outputs",
    *,
    include_maintenance: bool = False,
) -> dict[str, dict[str, Any]]:
    """최신 alert source JSON 파일들을 읽는다. 네트워크 호출 없음."""
    sources = {
        "risk": _latest_json(output_dir, "risk_alert_*.json"),
        "ops": _latest_json(output_dir, "ops_dashboard_*.json"),
        "sell_plan": _latest_json(output_dir, "sell_plan_*.json"),
        "reconcile": _latest_json(output_dir, "reconcile_live_account_*.json"),
    }
    if include_maintenance:
        sources["weekly_maintenance"] = _latest_json(output_dir, "weekly_maintenance_*.json")
        sources["report_health"] = _latest_json(output_dir, "report_health_*.json")
    return sources


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _risk_lines(risk: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for p in risk.get("positions") or []:
        if not isinstance(p, dict):
            continue
        risk_level = str(p.get("risk_level") or "")
        alerts = p.get("alerts") or []
        if risk_level == "OK" and not alerts:
            continue
        sym = str(p.get("symbol") or "")
        lines.append(f"{sym} {risk_level} ({_fmt_pct(p.get('unrealized_pnl_pct'))})")
        for alert in alerts[:3]:
            lines.append(f"- {alert}")
    for alert in risk.get("alerts") or []:
        if str(alert) not in "\n".join(lines):
            lines.append(str(alert))
    return lines


def _message(title: str, severity: str, source: str, lines: list[str], metadata: dict[str, Any]) -> AlertMessage:
    body = "\n".join(
        [
            f"[DeepSignal {severity}]",
            *lines,
            "",
            "This is alert-only. No orders were placed.",
        ]
    ).strip()
    return AlertMessage(title=title, severity=severity, body=body, source=source, metadata=metadata)


def _maintenance_issue_lines(data: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for warning in data.get("warnings") or []:
        lines.append(str(warning))
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "")
        if status in {"OK", "HEALTH_OK"} and not step.get("warnings"):
            continue
        name = str(step.get("name") or "step")
        msg = str(step.get("message") or status)
        if status:
            lines.append(f"{name}: {status} - {msg}")
        for warning in step.get("warnings") or []:
            lines.append(f"- {warning}")
    issues = data.get("issues") or []
    for issue in issues:
        if isinstance(issue, dict):
            severity = str(issue.get("severity") or "").strip()
            category = str(issue.get("category") or "").strip()
            message = str(issue.get("message") or "").strip()
            if message:
                prefix = " ".join(p for p in (severity, category) if p)
                lines.append(f"{prefix}: {message}" if prefix else message)
        else:
            lines.append(str(issue))
    return lines[:12]


def _next_action_lines(data: dict[str, Any], *, limit: int = 5) -> list[str]:
    actions = data.get("next_actions") or []
    return [str(a) for a in actions[:limit]]


def build_alert_messages(
    sources: dict[str, dict[str, Any]],
    *,
    include_ok: bool = False,
) -> list[AlertMessage]:
    """risk/ops/sell/reconcile source에서 전송 대상 alert message를 만든다."""
    messages: list[AlertMessage] = []

    risk = sources.get("risk") or {}
    risk_status = str(risk.get("status") or risk.get("risk_status") or "")
    if risk_status in RISK_ALERT_STATUSES or risk_status == SEVERITY_WARNING or (include_ok and risk_status):
        severity = SEVERITY_RISK_ALERT if risk_status in RISK_ALERT_STATUSES else (SEVERITY_WARNING if risk_status == "WARNING" else SEVERITY_INFO)
        lines = [f"Risk status: {risk_status}"] + _risk_lines(risk)
        messages.append(
            _message(
                "DeepSignal risk alert",
                severity,
                "risk",
                lines,
                {"status": risk_status, "path": risk.get("_path")},
            )
        )

    ops = sources.get("ops") or {}
    ops_status = str(ops.get("status") or "")
    if ops_status in OPS_ALERT_STATUSES or (include_ok and ops_status):
        severity = SEVERITY_CRITICAL if ops_status == "RECONCILE_MISMATCH" else (SEVERITY_RISK_ALERT if ops_status == "RISK_ALERT" else (SEVERITY_WARNING if ops_status == "WARNING" else SEVERITY_INFO))
        lines = [
            f"Ops status: {ops_status}",
            f"Positions: {len(ops.get('positions') or [])}",
            f"Warnings: {len(ops.get('warnings') or [])}",
        ]
        messages.append(
            _message(
                "DeepSignal ops dashboard alert",
                severity,
                "ops_dashboard",
                lines,
                {"status": ops_status, "path": ops.get("_path")},
            )
        )

    sell = sources.get("sell_plan") or {}
    sell_status = str(sell.get("status") or "")
    if sell_status in SELL_PLAN_ALERT_STATUSES or (include_ok and sell_status):
        severity = SEVERITY_RISK_ALERT if sell_status == "EXIT" else (SEVERITY_WARNING if sell_status in SELL_PLAN_ALERT_STATUSES else SEVERITY_INFO)
        lines = [
            f"Sell plan: {sell_status}",
            f"Items: {len(sell.get('items') or [])}",
        ]
        for item in (sell.get("items") or [])[:5]:
            if isinstance(item, dict):
                lines.append(
                    f"{item.get('symbol')} action={item.get('suggested_action')} "
                    f"suggested_sell_qty={item.get('suggested_sell_quantity')}"
                )
        messages.append(
            _message(
                "DeepSignal sell plan alert",
                severity,
                "sell_plan",
                lines,
                {"status": sell_status, "path": sell.get("_path")},
            )
        )

    rec = sources.get("reconcile") or {}
    if rec and rec.get("success") is False:
        lines = [
            "Reconcile success: False",
            f"missing_in_db: {len(rec.get('missing_in_db') or [])}",
            f"missing_in_broker: {len(rec.get('missing_in_broker') or [])}",
            f"quantity_mismatch: {len(rec.get('quantity_mismatch') or [])}",
        ]
        messages.append(
            _message(
                "DeepSignal reconcile mismatch",
                SEVERITY_CRITICAL,
                "reconcile",
                lines,
                {"success": False, "path": rec.get("_path")},
            )
        )
    elif include_ok and rec:
        messages.append(
            _message(
                "DeepSignal reconcile ok",
                SEVERITY_INFO,
                "reconcile",
                ["Reconcile success: True"],
                {"success": True, "path": rec.get("_path")},
            )
        )

    maintenance = sources.get("weekly_maintenance") or {}
    maintenance_status = str(maintenance.get("final_status") or maintenance.get("status") or "")
    if maintenance_status in MAINTENANCE_CRITICAL_STATUSES | MAINTENANCE_WARNING_STATUSES or (include_ok and maintenance_status):
        if maintenance_status in MAINTENANCE_CRITICAL_STATUSES:
            severity = SEVERITY_CRITICAL
        elif maintenance_status in MAINTENANCE_WARNING_STATUSES:
            severity = SEVERITY_WARNING
        elif maintenance_status in MAINTENANCE_OK_STATUSES:
            severity = SEVERITY_INFO
        else:
            severity = SEVERITY_INFO
        lines = [f"Status: {maintenance_status}", "Issues:"]
        issue_lines = _maintenance_issue_lines(maintenance)
        lines.extend([f"- {line}" for line in issue_lines] if issue_lines else ["- (none)"])
        actions = _next_action_lines(maintenance)
        if actions:
            lines.append("Next actions:")
            lines.extend(f"- {action}" for action in actions)
        messages.append(
            _message(
                "DeepSignal weekly maintenance alert",
                severity,
                "weekly_maintenance",
                lines,
                {
                    "source_file": maintenance.get("_path"),
                    "maintenance_status": maintenance_status,
                    "path": maintenance.get("_path"),
                },
            )
        )

    health = sources.get("report_health") or {}
    health_status = str(health.get("status") or "")
    if health_status in HEALTH_CRITICAL_STATUSES | HEALTH_WARNING_STATUSES or (include_ok and health_status):
        if health_status in HEALTH_CRITICAL_STATUSES:
            severity = SEVERITY_CRITICAL
        elif health_status in HEALTH_WARNING_STATUSES:
            severity = SEVERITY_WARNING
        elif health_status in HEALTH_OK_STATUSES:
            severity = SEVERITY_INFO
        else:
            severity = SEVERITY_INFO
        lines = [f"Health status: {health_status}", "Issues:"]
        issue_lines = _maintenance_issue_lines(health)
        lines.extend([f"- {line}" for line in issue_lines] if issue_lines else ["- (none)"])
        actions = _next_action_lines(health)
        if actions:
            lines.append("Next actions:")
            lines.extend(f"- {action}" for action in actions)
        messages.append(
            _message(
                "DeepSignal report health alert",
                severity,
                "report_health",
                lines,
                {
                    "source_file": health.get("_path"),
                    "health_status": health_status,
                    "path": health.get("_path"),
                },
            )
        )

    return messages


def render_alert_body(messages: list[AlertMessage]) -> str:
    if not messages:
        return "[DeepSignal INFO]\nNo alert messages.\n\nThis is alert-only. No orders were placed."
    return "\n\n---\n\n".join(m.body for m in messages)


def send_telegram_alert(
    messages: list[AlertMessage],
    *,
    bot_token: str | None = None,
    chat_id: str | None = None,
    timeout_seconds: float = 5.0,
) -> NotificationResult:
    token = (bot_token or os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (chat_id or os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return NotificationResult(
            channel="telegram",
            success=False,
            status="missing_config",
            message="Telegram bot token or chat id is missing.",
            raw={},
        )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": render_alert_body(messages), "disable_web_page_preview": True}
    try:
        resp = requests.post(url, json=payload, timeout=float(timeout_seconds))
        ok = 200 <= resp.status_code < 300
        return NotificationResult(
            channel="telegram",
            success=ok,
            status="sent" if ok else "http_error",
            message=f"telegram status_code={resp.status_code}",
            raw={"status_code": resp.status_code},
        )
    except requests.RequestException as exc:
        return NotificationResult(
            channel="telegram",
            success=False,
            status="request_error",
            message=str(exc),
            raw={"exception_type": type(exc).__name__},
        )


def send_discord_alert(
    messages: list[AlertMessage],
    *,
    webhook_url: str | None = None,
    timeout_seconds: float = 5.0,
) -> NotificationResult:
    url = (webhook_url or os.getenv("DEEPSIGNAL_NOTIFY_DISCORD_WEBHOOK_URL") or "").strip()
    if not url:
        return NotificationResult(
            channel="discord",
            success=False,
            status="missing_config",
            message="Discord webhook URL is missing.",
            raw={},
        )
    payload = {"content": render_alert_body(messages)}
    try:
        resp = requests.post(url, json=payload, timeout=float(timeout_seconds))
        ok = 200 <= resp.status_code < 300
        return NotificationResult(
            channel="discord",
            success=ok,
            status="sent" if ok else "http_error",
            message=f"discord status_code={resp.status_code}",
            raw={"status_code": resp.status_code},
        )
    except requests.RequestException as exc:
        return NotificationResult(
            channel="discord",
            success=False,
            status="request_error",
            message=str(exc),
            raw={"exception_type": type(exc).__name__},
        )


def write_notification_audit(
    *,
    output_dir: str | Path,
    dry_run: bool,
    channel: str,
    messages: list[AlertMessage],
    results: list[NotificationResult],
) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = root / f"notification_audit_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    body: dict[str, Any] = {
        "generated_at": now.isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "channel": channel,
        "messages": [asdict(m) for m in messages],
        "results": [asdict(r) for r in results],
        "actual_order_attempted": False,
        "no_orders_placed": True,
        "실제_주문_없음": True,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def notify_alerts(
    *,
    output_dir: str | Path = "outputs",
    channel: str | None = None,
    dry_run: bool = True,
    include_ok: bool = False,
    include_maintenance: bool = False,
    timeout_seconds: float = 5.0,
) -> tuple[list[AlertMessage], list[NotificationResult], Path]:
    """최신 위험 리포트를 alert-only로 알림. dry_run이면 네트워크 호출 없음."""
    selected = (channel or os.getenv("DEEPSIGNAL_NOTIFY_DEFAULT_CHANNEL") or "telegram").strip().lower()
    if selected not in {"telegram", "discord"}:
        results = [
            NotificationResult(
                channel=selected,
                success=False,
                status="unsupported_channel",
                message=f"unsupported channel: {selected}",
                raw={},
            )
        ]
        messages = build_alert_messages(
            load_latest_alert_sources(output_dir, include_maintenance=include_maintenance),
            include_ok=include_ok,
        )
        audit = write_notification_audit(output_dir=output_dir, dry_run=dry_run, channel=selected, messages=messages, results=results)
        return messages, results, audit

    sources = load_latest_alert_sources(output_dir, include_maintenance=include_maintenance)
    messages = build_alert_messages(sources, include_ok=include_ok)
    if dry_run:
        results = [
            NotificationResult(
                channel=selected,
                success=True,
                status="dry_run",
                message=f"dry-run: {len(messages)} alert message(s) would be sent",
                raw={"network_called": False},
            )
        ]
    elif selected == "telegram":
        results = [send_telegram_alert(messages, timeout_seconds=timeout_seconds)]
    else:
        results = [send_discord_alert(messages, timeout_seconds=timeout_seconds)]
    audit = write_notification_audit(output_dir=output_dir, dry_run=dry_run, channel=selected, messages=messages, results=results)
    return messages, results, audit
