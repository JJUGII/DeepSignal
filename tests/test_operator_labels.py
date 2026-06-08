"""Korean operator UI labels."""

from __future__ import annotations

from deepsignal.live_trading.operator_labels import (
    label_boolean_status,
    label_report_type,
    label_severity,
    label_status,
    label_summary_key,
)


def test_report_type_labels() -> None:
    assert label_report_type("safety_audit") == "안전 점검"
    assert label_report_type("reconcile") == "계좌 정합성 검사"
    assert label_report_type("unknown") == "알 수 없음"


def test_status_labels_keep_unknown_fallback() -> None:
    assert label_status("SAFETY_AUDIT_WARNING") == "안전 점검 경고"
    assert label_status("KIS_LIVE_ORDER_COMPLETED") == "실거래 주문 완료"
    assert label_status("success=True") == "정합성 정상"
    assert label_status("NOT_AVAILABLE") == "없음"
    assert label_status("CUSTOM_STATUS") == "CUSTOM_STATUS (미분류)"


def test_severity_and_summary_labels() -> None:
    assert label_severity("ok") == "정상"
    assert label_severity("warning") == "경고"
    assert label_severity("blocked") == "차단"
    assert label_severity("unknown") == "확인 필요"
    assert label_summary_key("latest_risk_alert_status") == "최근 리스크 경고"


def test_boolean_status_labels() -> None:
    assert label_boolean_status("true") == "예"
    assert label_boolean_status("false") == "아니오"
    assert label_boolean_status("success=False") == "실패"
