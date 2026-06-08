"""일일 실전 운영 상태 통합 요약 ([실전-17]). 조회/리포트 전용."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

STATUS_OK = "OK"
STATUS_WARNING = "WARNING"
STATUS_RISK_ALERT = "RISK_ALERT"
STATUS_RECONCILE_MISMATCH = "RECONCILE_MISMATCH"
STATUS_NO_DATA = "NO_DATA"

RISK_ALERT_STATUSES = {"STOP_LOSS_ALERT", "TAKE_PROFIT_ALERT", "MIXED_ALERT"}


@dataclass
class DailyOpsSummary:
    date: str
    generated_at: str
    status: str
    account: dict[str, Any]
    reconcile: dict[str, Any]
    risk: dict[str, Any]
    ops_dashboard: dict[str, Any]
    sell_plan: dict[str, Any]
    notification: dict[str, Any]
    next_actions: list[str]
    warnings: list[str]


def _date_token(date_str: str) -> str:
    return date_str.replace("-", "")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"_parse_error": True, "_path": path.as_posix()}
    if isinstance(data, dict):
        data["_path"] = path.as_posix()
        return data
    return {"_non_object_json": True, "_path": path.as_posix()}


def _latest_for_date(
    output_dir: str | Path,
    pattern: str,
    *,
    target_date: str,
    include_latest_fallback: bool = True,
) -> tuple[dict[str, Any], str | None]:
    root = Path(output_dir)
    paths = sorted(root.glob(pattern))
    if not paths:
        return {}, f"No file found for {pattern}"
    token = _date_token(target_date)
    today_paths = [p for p in paths if token in p.name]
    if today_paths:
        return _read_json(today_paths[-1]), None
    if include_latest_fallback:
        path = paths[-1]
        return _read_json(path), f"No {target_date} file for {pattern}; using latest fallback {path.name}"
    return {}, f"No {target_date} file for {pattern}; fallback disabled"


def load_daily_ops_sources(
    *,
    output_dir: str | Path = "outputs",
    target_date: str | None = None,
    include_latest_fallback: bool = True,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    date = target_date or datetime.now().date().isoformat()
    specs = {
        "account": "live_account_snapshot_*.json",
        "reconcile": "reconcile_live_account_*.json",
        "risk": "risk_alert_*.json",
        "ops_dashboard": "ops_dashboard_*.json",
        "sell_plan": "sell_plan_*.json",
        "notification": "notification_audit_*.json",
    }
    sources: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for key, pattern in specs.items():
        data, warning = _latest_for_date(
            output_dir,
            pattern,
            target_date=date,
            include_latest_fallback=include_latest_fallback,
        )
        sources[key] = data
        if warning:
            warnings.append(warning)
    return sources, warnings


def _risk_status(risk: dict[str, Any]) -> str:
    return str(risk.get("status") or risk.get("risk_status") or "")


def decide_daily_ops_status(sources: dict[str, dict[str, Any]]) -> str:
    account = sources.get("account") or {}
    reconcile = sources.get("reconcile") or {}
    risk = sources.get("risk") or {}
    ops = sources.get("ops_dashboard") or {}
    sell = sources.get("sell_plan") or {}

    if reconcile and reconcile.get("success") is False:
        return STATUS_RECONCILE_MISMATCH

    risk_status = _risk_status(risk)
    if risk_status in RISK_ALERT_STATUSES:
        return STATUS_RISK_ALERT

    ops_status = str(ops.get("status") or "")
    if ops_status == STATUS_RECONCILE_MISMATCH:
        return STATUS_RECONCILE_MISMATCH
    if ops_status == STATUS_RISK_ALERT:
        return STATUS_RISK_ALERT

    sell_status = str(sell.get("status") or "")
    if sell_status == "EXIT":
        return STATUS_RISK_ALERT
    if sell_status == "REDUCE":
        return STATUS_WARNING

    if risk_status == STATUS_WARNING or sell_status == "REVIEW" or ops_status == STATUS_WARNING:
        return STATUS_WARNING

    required = (account, reconcile, risk, ops, sell)
    if any(not x for x in required):
        return STATUS_NO_DATA

    return STATUS_OK


def build_next_actions(status: str) -> list[str]:
    if status == STATUS_RECONCILE_MISMATCH:
        return ["Run live-sync-account and reconcile-live-account before any order."]
    if status == STATUS_RISK_ALERT:
        return ["Review RISK_ALERT.md and SELL_PLAN.md manually. No automated SELL is available."]
    if status == STATUS_WARNING:
        return ["Review warnings before adding positions."]
    if status == STATUS_NO_DATA:
        return ["Run live-sync-account, reconcile-live-account, risk-check, ops-dashboard."]
    return ["No critical action. Continue monitoring."]


def build_daily_ops_summary(
    *,
    output_dir: str | Path = "outputs",
    target_date: str | None = None,
    include_latest_fallback: bool = True,
    notify_dry_run: bool = False,
) -> DailyOpsSummary:
    date = target_date or datetime.now().date().isoformat()
    if notify_dry_run:
        from deepsignal.live_trading.notification_center import notify_alerts

        notify_alerts(output_dir=output_dir, dry_run=True)
    sources, warnings = load_daily_ops_sources(
        output_dir=output_dir,
        target_date=date,
        include_latest_fallback=include_latest_fallback,
    )
    status = decide_daily_ops_status(sources)
    return DailyOpsSummary(
        date=date,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        status=status,
        account=sources.get("account") or {},
        reconcile=sources.get("reconcile") or {},
        risk=sources.get("risk") or {},
        ops_dashboard=sources.get("ops_dashboard") or {},
        sell_plan=sources.get("sell_plan") or {},
        notification=sources.get("notification") or {},
        next_actions=build_next_actions(status),
        warnings=warnings,
    )


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return 0


def write_daily_ops_summary(
    summary: DailyOpsSummary,
    *,
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    json_path = root / f"daily_ops_summary_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    md_path = root / "DAILY_OPS_SUMMARY.md"
    body = asdict(summary)
    body["actual_order_attempted"] = False
    body["no_orders_placed"] = True
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    account = summary.account
    reconcile = summary.reconcile
    risk = summary.risk
    ops = summary.ops_dashboard
    sell = summary.sell_plan
    notification = summary.notification
    recon_ok = reconcile.get("success")
    recon_ko = "✅ 일치" if recon_ok is True else ("❌ 불일치" if recon_ok is False else str(recon_ok) if recon_ok is not None else "-")
    dry_run = notification.get("dry_run")
    dry_run_ko = "시뮬레이션" if dry_run else "실제 발송"

    lines = [
        "# DeepSignal — 일일 운영 요약",
        "",
        "## 전체 상태",
        "",
        f"- 상태: **{summary.status}**",
        f"- 날짜: {summary.date}",
        f"- 생성 시각: {summary.generated_at}",
        "- 모드: 읽기 전용 요약 (주문·매도 자동화·KIS POST 없음)",
        "",
        "## 계좌 현황",
        "",
        f"- 현금: {_fmt((account.get('cash') or {}).get('cash') if isinstance(account.get('cash'), dict) else account.get('cash'))}",
        f"- 보유 종목 수: {_count(account.get('positions'))}",
        f"- 스냅샷 파일: `{_fmt(account.get('_path'))}`",
        "",
        "## 잔고 대사",
        "",
        f"- 결과: {recon_ko}",
        f"- 일치 종목: {_count(reconcile.get('matched'))}",
        f"- DB 누락: {_count(reconcile.get('missing_in_db'))}",
        f"- 증권사 누락: {_count(reconcile.get('missing_in_broker'))}",
        f"- 수량 불일치: {_count(reconcile.get('quantity_mismatch'))}",
        f"- 리포트 파일: `{_fmt(reconcile.get('_path'))}`",
        "",
        "## 위험 점검",
        "",
        f"- 상태: {_fmt(_risk_status(risk))}",
        f"- 경보: {_count(risk.get('alerts'))}건",
        f"- 경고: {_count(risk.get('warnings'))}건",
        f"- 리포트 파일: `{_fmt(risk.get('_path'))}`",
        "",
        "## 운영 대시보드",
        "",
        f"- 상태: {_fmt(ops.get('status'))}",
        f"- 보유 종목 수: {_count(ops.get('positions'))}",
        f"- 경고: {_count(ops.get('warnings'))}건",
        f"- 리포트 파일: `{_fmt(ops.get('_path'))}`",
        "",
        "## 매도 계획",
        "",
        f"- 상태: {_fmt(sell.get('status'))}",
        f"- 항목 수: {_count(sell.get('items'))}",
        f"- 리포트 파일: `{_fmt(sell.get('_path'))}`",
        "",
        "## 알림 발송",
        "",
        f"- 발송 모드: {dry_run_ko}",
        f"- 채널: {_fmt(notification.get('channel'))}",
        f"- 메시지 수: {_count(notification.get('messages'))}",
        f"- 결과 수: {_count(notification.get('results'))}",
        f"- 리포트 파일: `{_fmt(notification.get('_path'))}`",
        "",
        "## 다음 할 일",
        "",
    ]
    for action in summary.next_actions:
        lines.append(f"- {action}")
    lines.extend(["", "## 경고", ""])
    for warning in summary.warnings:
        lines.append(f"- {warning}")
    if not summary.warnings:
        lines.append("- (없음)")
    lines.extend(
        [
            "",
            "## 안전 안내",
            "",
            "- 이 요약은 주문을 실행하지 않습니다.",
            "- 매도 자동화, 시장가 주문, 취소, KIS POST 요청을 수행하지 않습니다.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def run_daily_ops_summary(
    *,
    output_dir: str | Path = "outputs",
    target_date: str | None = None,
    include_latest_fallback: bool = True,
    notify_dry_run: bool = False,
) -> tuple[DailyOpsSummary, Path, Path]:
    summary = build_daily_ops_summary(
        output_dir=output_dir,
        target_date=target_date,
        include_latest_fallback=include_latest_fallback,
        notify_dry_run=notify_dry_run,
    )
    json_path, md_path = write_daily_ops_summary(summary, output_dir=output_dir)
    return summary, json_path, md_path
