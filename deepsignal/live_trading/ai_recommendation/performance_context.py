"""Local report context for AI live recommendations.

Only selected metadata/status fields are read. Full report bodies are not exported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deepsignal.live_trading.ai_recommendation.recommendation_model import OperationalRiskContext


def _latest_file(root: Path, pattern: str) -> Path | None:
    files = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _safe_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _status(data: dict[str, Any]) -> str:
    for key in ("status", "overall_status"):
        if data.get(key) is not None:
            return str(data.get(key))
    summary = data.get("summary")
    if isinstance(summary, dict):
        for key in ("status", "overall_status", "result_status"):
            if summary.get(key) is not None:
                return str(summary.get(key))
    if data.get("success") is not None:
        return f"success={data.get('success')}"
    return "NOT_AVAILABLE"


def load_operational_risk_context(output_dir: str | Path = "outputs") -> OperationalRiskContext:
    root = Path(output_dir)
    safety = _safe_json(_latest_file(root, "safety_audit_*.json"))
    reconcile = _safe_json(_latest_file(root, "reconcile_live_account_*.json"))
    fill = _safe_json(_latest_file(root, "live_fill_summary_*.json"))
    risk = _safe_json(_latest_file(root, "risk_alert_*.json"))
    archive = _safe_json(_latest_file(root, "archive_viewer_*.json"))

    safety_status = _status(safety)
    reconcile_status = _status(reconcile)
    risk_status = _status(risk)

    blocked: list[str] = []
    warnings: list[str] = []
    if "BLOCKED" in safety_status.upper():
        blocked.append(f"safety_audit={safety_status}")
    elif "WARNING" in safety_status.upper():
        warnings.append(f"safety_audit={safety_status}")

    rec_upper = reconcile_status.upper()
    if "MISMATCH" in rec_upper or reconcile.get("success") is False:
        blocked.append(f"reconcile={reconcile_status}")

    partial_open = False
    summary = fill.get("summary") if isinstance(fill.get("summary"), dict) else {}
    for key in ("partial_fill_open", "has_open_partial", "open_partial_count"):
        value = fill.get(key, summary.get(key))
        if value is True:
            partial_open = True
        if isinstance(value, (int, float)) and value > 0:
            partial_open = True
    if "PARTIAL" in _status(fill).upper():
        partial_open = True
    if partial_open:
        blocked.append("partial_fill_open")

    repeated: list[str] = []
    trend = archive.get("trend_analytics") if isinstance(archive.get("trend_analytics"), dict) else {}
    raw_repeated = trend.get("repeated_problem_types") if isinstance(trend, dict) else []
    if isinstance(raw_repeated, list):
        for item in raw_repeated[:10]:
            if isinstance(item, dict) and item.get("report_type"):
                repeated.append(str(item.get("report_type")))

    return OperationalRiskContext(
        safety_audit_status=safety_status,
        reconcile_status=reconcile_status,
        risk_status=risk_status,
        partial_fill_open=partial_open,
        archive_repeated_problem_types=repeated,
        blocked_reasons=blocked,
        warnings=warnings,
    )
