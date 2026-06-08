"""archive_viewer.py — read-only local archive viewer."""

from __future__ import annotations

import json
import os
import csv
from pathlib import Path
from datetime import date, timedelta

from deepsignal.live_trading.archive_viewer import build_archive_viewer, load_archive_viewer_link_info, run_archive_viewer
from deepsignal.live_trading.report_index import run_report_index


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, ensure_ascii=False, indent=2))


def _mtime(path: Path, value: int) -> None:
    os.utime(path, (value, value))


def test_archive_viewer_generates_html_and_json(tmp_path: Path) -> None:
    _write_json(tmp_path / "safety_audit_20260517_160000.json", {"status": "SAFETY_AUDIT_WARNING", "issues": [{"severity": "WARNING"}]})
    _write(tmp_path / "SAFETY_AUDIT.md", "# safety")
    _write_json(tmp_path / "ai_daily_trade_plan_20260517_160100.json", {"status": "AI_DAILY_TRADE_PLAN_READY"})
    _write(tmp_path / "AI_DAILY_TRADE_PLAN.md", "# daily ai plan")
    _write_json(tmp_path / "live_order_plan_ai_latest.json", {"status": "PENDING_APPROVAL", "orders": []})

    result, html_path, json_path = run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")

    assert html_path.is_file()
    assert (tmp_path / "ARCHIVE_VIEWER.csv").is_file()
    assert (tmp_path / "ARCHIVE_VIEWER_SUMMARY.md").is_file()
    assert (tmp_path / "ARCHIVE_VIEWER_PRESETS.json").is_file()
    assert json_path.is_file()
    html = html_path.read_text(encoding="utf-8")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert "DeepSignal 리포트 보관함" in html
    assert "안전 점검 경고" in html
    assert "AI 일일 매매 계획" in html
    assert 'data-status="SAFETY_AUDIT_WARNING"' in html
    assert data["network_called"] is False
    assert data["files_deleted"] is False
    assert "summary" in data
    assert "filters_available" in data
    assert "entries" in data
    assert "needs_attention" in data
    assert "latest_by_type" in data
    assert "trend_analytics" in data
    assert data["summary"]["total_warning"] >= 1
    assert data["summary"]["total_blocked_or_error"] >= 0
    assert data["export_files"] == {
        "html": "ARCHIVE_VIEWER.html",
        "csv": "ARCHIVE_VIEWER.csv",
        "summary_md": "ARCHIVE_VIEWER_SUMMARY.md",
        "presets": "ARCHIVE_VIEWER_PRESETS.json",
    }
    assert data["preset_file"] == "ARCHIVE_VIEWER_PRESETS.json"
    assert data["presets"]
    assert result.summary["total_reports"] >= 2
    assert "ai_daily_trade_plan" in data["filters_available"]["report_type"]
    assert data["latest_by_type"]["ai_live_order_plan_latest"]["report_type"] == "ai_live_order_plan_latest"


def test_archive_viewer_trend_analytics_fields(tmp_path: Path) -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    older = today - timedelta(days=2)
    warning = tmp_path / f"risk_alert_{today.strftime('%Y%m%d')}_100000.json"
    blocked = tmp_path / f"risk_alert_{yesterday.strftime('%Y%m%d')}_100000.json"
    ok = tmp_path / f"reconcile_live_account_{older.strftime('%Y%m%d')}_100000.json"
    _write_json(warning, {"status": "WARNING"})
    _write_json(blocked, {"status": "RISK_ALERT", "blocked_count": 1})
    _write_json(ok, {"success": True})

    result, _html_path, json_path = run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    trend = result.trend_analytics

    assert "trend_analytics" in data
    assert trend["total_reports"] >= 3
    assert trend["total_warning"] >= 1
    assert trend["total_blocked_or_error"] >= 1
    assert trend["by_day"][today.isoformat()]["warning"] == 1
    assert trend["by_day"][yesterday.isoformat()]["blocked"] == 1
    assert trend["by_report_type"]["risk_alert"]["total"] == 2
    assert trend["by_severity"]["warning"] >= 1
    assert trend["by_status"]["WARNING"] == 1
    assert len(trend["warning_trend_7d"]) == 7
    assert len(trend["blocked_trend_7d"]) == 7
    assert trend["needs_attention_by_type"]["risk_alert"] == 2
    assert trend["repeated_problem_types"][0]["report_type"] == "risk_alert"


def test_archive_viewer_presets_json_created(tmp_path: Path) -> None:
    _write_json(tmp_path / "risk_alert_20260517_100000.json", {"status": "RISK_ALERT"})

    run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    presets = json.loads((tmp_path / "ARCHIVE_VIEWER_PRESETS.json").read_text(encoding="utf-8"))
    ids = {preset["id"] for preset in presets}

    assert {"needs_attention", "latest_only", "safety_audit", "risk_and_reconcile", "live_order_audit"}.issubset(ids)
    assert presets[0]["label"] == "주의 필요 항목"


def test_archive_viewer_print_css_included(tmp_path: Path) -> None:
    _write_json(tmp_path / "risk_alert_20260517_100000.json", {"status": "RISK_ALERT"})

    _result, html_path, _json_path = run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    html = html_path.read_text(encoding="utf-8")

    assert "@media print" in html
    assert ".filters,script,button{display:none!important}" in html
    assert "a[href]::after" in html


def test_archive_viewer_csv_export_columns(tmp_path: Path) -> None:
    _write_json(tmp_path / "safety_audit_20260517_100000.json", {"status": "SAFETY_AUDIT_WARNING"})

    run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    with (tmp_path / "ARCHIVE_VIEWER.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    assert rows
    assert list(rows[0].keys()) == [
        "report_type",
        "report_type_label",
        "status",
        "status_label",
        "severity",
        "severity_label",
        "generated_at",
        "generated_date",
        "timezone",
        "freshness_source",
        "freshness_status",
        "modified_time",
        "size_bytes",
        "relative_path",
        "title",
    ]
    assert rows[0]["report_type"] == "safety_audit"
    assert rows[0]["report_type_label"] == "안전 점검"
    assert rows[0]["status"] == "SAFETY_AUDIT_WARNING"
    assert rows[0]["status_label"] == "안전 점검 경고"


def test_archive_viewer_markdown_summary_sections(tmp_path: Path) -> None:
    _write_json(tmp_path / "risk_alert_20260517_100000.json", {"status": "RISK_ALERT"})

    run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    md = (tmp_path / "ARCHIVE_VIEWER_SUMMARY.md").read_text(encoding="utf-8")

    assert "# DeepSignal 리포트 보관함 요약" in md
    assert "## 운영 요약" in md
    assert "## 최근 상태" in md
    assert "## 유형별 최신 리포트" in md
    assert "## 주의 필요 항목" in md
    assert "## 주요 리포트 링크" in md
    assert "## 사용 가능한 필터 프리셋" in md
    assert "최신 리포트만" in md
    assert "## 운영 추세" in md
    assert "## Freshness 기준 요약" in md
    assert "JSON generated_at" in md
    assert "### 반복 문제 유형" in md
    assert "### 유형별 주의 항목 Top 5" in md


def test_archive_viewer_filter_ui_elements_are_rendered(tmp_path: Path) -> None:
    _write_json(tmp_path / "risk_alert_20260517_100000.json", {"status": "RISK_ALERT"})

    _result, html_path, _json_path = run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    html = html_path.read_text(encoding="utf-8")

    assert "운영 요약" in html
    assert "주의 필요 항목" in html
    assert "리포트 목록" in html
    assert "필터 프리셋" in html
    assert "운영 추세" in html
    assert "최근 7일 경고 추세" in html
    assert "최근 7일 차단/오류 추세" in html
    assert "반복 문제 유형" in html
    assert "유형별 주의 항목" in html
    assert "일자별 요약" in html
    assert 'id="presetSelect"' in html
    assert "프리셋 적용" in html
    assert "필터 초기화" in html
    assert "ARCHIVE_VIEWER_PRESETS" in html
    assert '"id": "needs_attention"' in html
    assert '<option value="needs_attention">주의 필요 항목</option>' in html
    assert 'id="filterType"' in html
    assert 'id="filterStatus"' in html
    assert 'id="filterSeverity"' in html
    assert 'id="filterText"' in html
    assert 'id="filterFrom"' in html
    assert 'id="filterTo"' in html
    assert 'id="filterAttention"' in html
    assert 'id="filterLatest"' in html
    assert "sortTable('modified')" in html
    assert "sortTable('generated')" in html
    assert "생성 시각" in html
    assert "기준 소스" in html
    assert "Freshness 기준 요약" in html
    assert "cdn" not in html.lower()
    assert "https://" not in html.lower()


def test_archive_viewer_keeps_raw_filter_values_but_shows_korean_labels(tmp_path: Path) -> None:
    _write_json(tmp_path / "safety_audit_20260517_100000.json", {"status": "SAFETY_AUDIT_WARNING"})

    _result, html_path, _json_path = run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    html = html_path.read_text(encoding="utf-8")

    assert '<option value="safety_audit">안전 점검</option>' in html
    assert '<option value="SAFETY_AUDIT_WARNING">안전 점검 경고</option>' in html
    assert '<option value="warning">경고</option>' in html
    assert 'data-status="SAFETY_AUDIT_WARNING"' in html
    assert ">안전 점검<" in html
    assert "badge-warning" in html


def test_archive_viewer_classifies_report_types_and_status(tmp_path: Path) -> None:
    _write_json(tmp_path / "risk_alert_20260517_100000.json", {"status": "STOP_LOSS_ALERT", "alerts": ["x"]})
    _write_json(tmp_path / "archive" / "weekly_maintenance_20260517_110000.json", {"final_status": "WEEKLY_MAINTENANCE_WARNING"})
    _write(tmp_path / "weekly_bundles" / "weekly_bundle_20260517_120000" / "BUNDLE_INDEX.html", "<html></html>")

    result = build_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    by_name = {Path(entry.relative_path).name: entry for entry in result.entries}

    assert by_name["risk_alert_20260517_100000.json"].report_type == "risk_alert"
    assert by_name["risk_alert_20260517_100000.json"].status == "STOP_LOSS_ALERT"
    assert by_name["weekly_maintenance_20260517_110000.json"].report_type == "weekly_maintenance"
    assert by_name["BUNDLE_INDEX.html"].report_type == "bundle"


def test_archive_viewer_excludes_sensitive_and_source_files(tmp_path: Path) -> None:
    _write(tmp_path / ".env", "SECRET=1")
    _write(tmp_path / ".kis_token_cache.json", "{}")
    _write(tmp_path / "deepsignal.db", "db")
    _write(tmp_path / "script.py", "print('no')")
    _write_json(tmp_path / "report_health_20260517_100000.json", {"status": "HEALTH_OK"})

    result = build_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    names = {Path(entry.relative_path).name for entry in result.entries}

    assert "report_health_20260517_100000.json" in names
    assert ".env" not in names
    assert ".kis_token_cache.json" not in names
    assert "deepsignal.db" not in names
    assert "script.py" not in names
    run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    csv_text = (tmp_path / "ARCHIVE_VIEWER.csv").read_text(encoding="utf-8-sig")
    md_text = (tmp_path / "ARCHIVE_VIEWER_SUMMARY.md").read_text(encoding="utf-8")
    assert "SECRET=1" not in csv_text
    assert "SECRET=1" not in md_text
    assert ".kis_token_cache" not in csv_text
    assert "deepsignal.db" not in md_text


def test_archive_viewer_missing_archive_dir_is_ok(tmp_path: Path) -> None:
    _write_json(tmp_path / "live_fill_summary_20260517_100000.json", {"status": "OK"})

    result = build_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "missing")

    assert len(result.entries) == 1
    assert result.warnings == []


def test_archive_viewer_summary_counts_warning_and_blocked(tmp_path: Path) -> None:
    _write_json(tmp_path / "safety_audit_20260517_100000.json", {"status": "SAFETY_AUDIT_BLOCKED", "issues": [{"severity": "BLOCKED"}]})
    _write_json(tmp_path / "weekly_maintenance_20260517_110000.json", {"final_status": "WEEKLY_MAINTENANCE_WARNING", "warnings": ["x"]})

    result = build_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")

    assert result.summary["blocked_error_count"] >= 1
    assert result.summary["warning_count"] >= 1
    assert result.summary["latest_safety_audit_status"] == "SAFETY_AUDIT_BLOCKED"


def test_archive_viewer_default_sort_modified_desc(tmp_path: Path) -> None:
    old = tmp_path / "risk_alert_20260517_100000.json"
    new = tmp_path / "risk_alert_20260517_110000.json"
    _write_json(old, {"status": "OK"})
    _write_json(new, {"status": "RISK_ALERT"})
    _mtime(old, 1_700_000_000)
    _mtime(new, 1_700_000_100)

    result = build_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")

    assert result.entries[0].relative_path == "risk_alert_20260517_110000.json"
    assert result.entries[1].relative_path == "risk_alert_20260517_100000.json"


def test_archive_viewer_needs_attention_and_latest_by_type(tmp_path: Path) -> None:
    ok = tmp_path / "report_health_20260517_090000.json"
    warn = tmp_path / "weekly_maintenance_20260517_100000.json"
    blocked = tmp_path / "safety_audit_20260517_110000.json"
    failed = tmp_path / "live_approval_audit_20260517_120000.json"
    partial = tmp_path / "live_fill_summary_20260517_130000.json"
    stale = tmp_path / "live_account_snapshot_20260517_140000.json"
    mismatch = tmp_path / "reconcile_live_account_20260517_150000.json"
    _write_json(ok, {"status": "HEALTH_OK"})
    _write_json(warn, {"final_status": "WEEKLY_MAINTENANCE_WARNING"})
    _write_json(blocked, {"status": "SAFETY_AUDIT_BLOCKED", "blocked_count": 1})
    _write_json(failed, {"status": "APPROVAL_FAILED"})
    _write_json(partial, {"status": "OK", "partial_fill_open": True})
    _write_json(stale, {"status": "OK", "stale_snapshot": True})
    _write_json(mismatch, {"status": "RECONCILE_OK", "reconcile_mismatch": True})

    result = build_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    attention_paths = {item["relative_path"] for item in result.needs_attention}

    assert "weekly_maintenance_20260517_100000.json" in attention_paths
    assert "safety_audit_20260517_110000.json" in attention_paths
    assert "live_approval_audit_20260517_120000.json" in attention_paths
    assert "live_fill_summary_20260517_130000.json" in attention_paths
    assert "live_account_snapshot_20260517_140000.json" in attention_paths
    assert "reconcile_live_account_20260517_150000.json" in attention_paths
    assert "report_health_20260517_090000.json" not in attention_paths
    assert result.latest_by_type["safety_audit"].relative_path == "safety_audit_20260517_110000.json"
    assert result.summary["latest_risk_alert_status"] == "NOT_AVAILABLE"
    assert result.summary["latest_reconcile_status"] == "RECONCILE_OK"
    assert result.summary["latest_live_approval_status"] == "APPROVAL_FAILED"


def test_archive_viewer_json_export_has_ux_fields(tmp_path: Path) -> None:
    _write_json(tmp_path / "risk_alert_20260517_100000.json", {"status": "RISK_ALERT"})

    _result, _html_path, json_path = run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    data = json.loads(json_path.read_text(encoding="utf-8"))

    assert data["filters_available"]["text_search"] is True
    assert "modified_at" in data["filters_available"]["sortable_columns"]
    assert data["needs_attention"]
    assert data["latest_by_type"]["risk_alert"]["relative_path"] == "risk_alert_20260517_100000.json"


def test_report_index_links_archive_viewer(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "archive_viewer_20260517_100000.json",
        {
            "generated_at": "2026-05-17T10:00:00",
            "summary": {
                "total_reports": 3,
                "freshness_source_summary": {"generated_at": 2, "mtime_fallback": 1, "markdown_header": 0, "unknown": 0},
            },
        },
    )
    _write(tmp_path / "ARCHIVE_VIEWER.html", "<html>archive</html>")
    _write(tmp_path / "ARCHIVE_VIEWER.csv", "report_type\n")
    _write(tmp_path / "ARCHIVE_VIEWER_SUMMARY.md", "# summary\n")
    _write_json(tmp_path / "ARCHIVE_VIEWER_PRESETS.json", [{"id": "needs_attention"}])

    _result, html_path, md_path, _json_path = run_report_index(output_dir=tmp_path, archive_dir=tmp_path / "archive")

    html = html_path.read_text(encoding="utf-8")
    md = md_path.read_text(encoding="utf-8")
    assert "리포트 보관함" in html
    assert "ARCHIVE_VIEWER.html" in html
    assert "ARCHIVE_VIEWER.csv" in html
    assert "ARCHIVE_VIEWER_SUMMARY.md" in html
    assert "ARCHIVE_VIEWER_PRESETS.json" in html
    assert "archive_viewer_20260517_100000.json" in html
    assert "CSV Export" in md
    assert "Markdown Summary" in md
    assert "Filter Presets" in md
    assert "Archive viewer status: AVAILABLE" in md
    assert "JSON generated_at: 2" in md


def test_archive_viewer_link_info_not_available(tmp_path: Path) -> None:
    info = load_archive_viewer_link_info(tmp_path)

    assert info.status == "NOT_AVAILABLE"
    assert info.html_rel is None
    assert info.json_rel is None


def test_archive_viewer_json_generated_at_freshness(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "ai_daily_trade_plan_20260519_100000.json",
        {
            "status": "AI_DAILY_TRADE_PLAN_READY",
            "generated_at": "2026-05-19T10:30:00+09:00",
            "generated_date": "2026-05-19",
            "timezone": "Asia/Seoul",
        },
    )

    result = build_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    entry = next(e for e in result.entries if e.report_type == "ai_daily_trade_plan")

    assert entry.freshness_source == "generated_at"
    assert entry.generated_at == "2026-05-19T10:30:00+09:00"
    assert entry.generated_date == "2026-05-19"
    assert entry.timezone == "Asia/Seoul"
    assert result.summary["freshness_source_summary"]["generated_at"] >= 1


def test_archive_viewer_markdown_header_freshness(tmp_path: Path) -> None:
    _write(
        tmp_path / "AI_DAILY_TRADE_PLAN.md",
        "# plan\n\n- 생성 시각: 2026-05-19T09:00:00+09:00\n- 기준 날짜: 2026-05-19\n- 타임존: Asia/Seoul\n",
    )

    result = build_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    entry = next(e for e in result.entries if e.relative_path == "AI_DAILY_TRADE_PLAN.md")

    assert entry.freshness_source == "markdown_header"
    assert entry.generated_at.startswith("2026-05-19T09:00:00")
    assert result.summary["freshness_source_summary"]["markdown_header"] >= 1


def test_archive_viewer_mtime_fallback_freshness(tmp_path: Path) -> None:
    path = tmp_path / "risk_alert_20260517_100000.json"
    _write_json(path, {"status": "RISK_ALERT"})

    result = build_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    entry = next(e for e in result.entries if e.relative_path == path.name)

    assert entry.freshness_source == "mtime_fallback"
    assert entry.generated_at
    assert result.summary["freshness_source_summary"]["mtime_fallback"] >= 1


def test_archive_viewer_json_entries_include_freshness_fields(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "safety_audit_20260517_100000.json",
        {"status": "SAFETY_AUDIT_OK", "generated_at": "2026-05-17T10:00:00+09:00"},
    )

    _result, _html_path, json_path = run_archive_viewer(output_dir=tmp_path, archive_dir=tmp_path / "archive")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    entry = data["entries"][0]

    assert entry["freshness_source"] == "generated_at"
    assert entry["generated_at"]
    assert "freshness_status" in entry
    assert data["summary"]["freshness_source_summary"]["generated_at"] >= 1


def test_archive_viewer_link_info_freshness_summary(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "archive_viewer_20260519_100000.json",
        {
            "generated_at": "2026-05-19T10:00:00",
            "summary": {
                "total_reports": 2,
                "freshness_source_summary": {
                    "generated_at": 1,
                    "markdown_header": 0,
                    "mtime_fallback": 1,
                    "unknown": 0,
                },
            },
        },
    )
    _write(tmp_path / "ARCHIVE_VIEWER.html", "<html></html>")

    info = load_archive_viewer_link_info(tmp_path)

    assert info.freshness_source_summary == {
        "generated_at": 1,
        "markdown_header": 0,
        "mtime_fallback": 1,
        "unknown": 0,
    }
