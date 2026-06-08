"""Korean operator-facing labels for live trading reports.

These helpers only affect UI text. Machine-readable JSON/status values remain
unchanged throughout the reporting pipeline.
"""

from __future__ import annotations


REPORT_TYPE_LABELS = {
    "safety_audit": "안전 점검",
    "weekly_maintenance": "주간 점검",
    "report_health": "리포트 상태 점검",
    "reconcile": "계좌 정합성 검사",
    "live_account_snapshot": "실계좌 스냅샷",
    "live_approval_audit": "실거래 승인 감사",
    "live_fill_summary": "체결 요약",
    "risk_alert": "리스크 경고",
    "cleanup_audit": "정리 점검",
    "dashboard": "운영 대시보드",
    "report_index": "리포트 인덱스",
    "ai_daily_trade_plan": "AI 일일 매매 계획",
    "ai_daily_trade_report": "AI 일일 매매 결과",
    "ai_daily_status": "AI 일일 운영 상태",
    "ai_live_order_plan_latest": "최신 AI 주문안",
    "bundle": "주간 번들",
    "unknown": "알 수 없음",
}

SEVERITY_LABELS = {
    "ok": "정상",
    "warning": "경고",
    "blocked": "차단",
    "error": "오류",
    "unknown": "확인 필요",
}

STATUS_LABELS = {
    "SAFETY_AUDIT_OK": "안전 점검 정상",
    "SAFETY_AUDIT_WARNING": "안전 점검 경고",
    "SAFETY_AUDIT_BLOCKED": "안전 점검 차단",
    "KIS_LIVE_ORDER_COMPLETED": "실거래 주문 완료",
    "KIS_LIVE_ORDER_FAILED": "실거래 주문 실패",
    "LIVE_EXECUTION_BLOCKED": "실거래 실행 차단",
    "WARNING": "경고",
    "OK": "정상",
    "FAILED": "실패",
    "ERROR": "오류",
    "success=True": "정합성 정상",
    "success=False": "정합성 실패",
    "NOT_AVAILABLE": "없음",
    "-": "상태 없음",
}

SUMMARY_LABELS = {
    "total_reports": "전체 리포트",
    "warning_count": "경고",
    "blocked_error_count": "차단/오류",
    "latest_safety_audit_status": "최근 안전 점검",
    "latest_weekly_maintenance_status": "최근 주간 점검",
    "latest_risk_alert_status": "최근 리스크 경고",
    "latest_reconcile_status": "최근 계좌 정합성",
    "latest_live_approval_status": "최근 실거래 승인",
    "needs_attention_count": "주의 필요 항목",
}

FRESHNESS_SOURCE_LABELS = {
    "generated_at": "JSON generated_at",
    "markdown_header": "Markdown 헤더",
    "mtime_fallback": "파일 수정시간 fallback",
    "unknown": "알 수 없음",
}

FRESHNESS_STATUS_LABELS = {
    "FRESH": "최신",
    "STALE": "오래됨",
    "MISSING": "없음",
    "UNKNOWN": "알 수 없음",
}

BOOLEAN_STATUS_LABELS = {
    "true": "예",
    "false": "아니오",
    "success=True": "정상",
    "success=False": "실패",
}


def _fallback(value: str | None) -> str:
    if value is None or value == "":
        return "상태 없음"
    return f"{value} (미분류)"


def label_report_type(value: str | None) -> str:
    if value is None or value == "":
        return REPORT_TYPE_LABELS["unknown"]
    return REPORT_TYPE_LABELS.get(str(value), f"{value} (미분류)")


def label_status(value: str | None) -> str:
    if value is None or value == "":
        return STATUS_LABELS["-"]
    return STATUS_LABELS.get(str(value), _fallback(str(value)))


def label_severity(value: str | None) -> str:
    if value is None or value == "":
        return SEVERITY_LABELS["unknown"]
    return SEVERITY_LABELS.get(str(value), f"{value} (미분류)")


def label_summary_key(value: str | None) -> str:
    if value is None or value == "":
        return "요약"
    return SUMMARY_LABELS.get(str(value), f"{value} (미분류)")


def label_freshness_source(value: str | None) -> str:
    if value is None or value == "":
        return FRESHNESS_SOURCE_LABELS["unknown"]
    return FRESHNESS_SOURCE_LABELS.get(str(value), f"{value} (미분류)")


def label_freshness_status(value: str | None) -> str:
    if value is None or value == "":
        return FRESHNESS_STATUS_LABELS["UNKNOWN"]
    return FRESHNESS_STATUS_LABELS.get(str(value), f"{value} (미분류)")


def label_boolean_status(value: str | None) -> str:
    if value is None or value == "":
        return "상태 없음"
    return BOOLEAN_STATUS_LABELS.get(str(value), label_status(str(value)))
