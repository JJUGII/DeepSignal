"""daily_ops_summary: 일일 운영 상태 통합 요약."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.daily_ops_summary import (
    STATUS_NO_DATA,
    STATUS_OK,
    STATUS_RECONCILE_MISMATCH,
    STATUS_RISK_ALERT,
    STATUS_WARNING,
    build_daily_ops_summary,
    write_daily_ops_summary,
)


def _write(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed_all_ok(root: Path, token: str = "20260516") -> None:
    _write(root / f"live_account_snapshot_{token}_100000.json", {"timestamp": "t", "cash": {"cash": 1}, "positions": [{"symbol": "005930"}]})
    _write(root / f"reconcile_live_account_{token}_100000.json", {"success": True, "matched": ["005930"], "missing_in_db": [], "missing_in_broker": [], "quantity_mismatch": []})
    _write(root / f"risk_alert_{token}_100000.json", {"status": "OK", "alerts": [], "warnings": [], "positions": []})
    _write(root / f"ops_dashboard_{token}_100000.json", {"status": "OK", "positions": [{"symbol": "005930"}], "warnings": []})
    _write(root / f"sell_plan_{token}_100000.json", {"status": "HOLD", "items": []})
    _write(root / f"notification_audit_{token}_100000.json", {"dry_run": True, "channel": "telegram", "messages": [], "results": []})


def test_all_ok_status(tmp_path: Path) -> None:
    _seed_all_ok(tmp_path)
    summary = build_daily_ops_summary(output_dir=tmp_path, target_date="2026-05-16")
    assert summary.status == STATUS_OK
    assert summary.next_actions == ["No critical action. Continue monitoring."]


def test_risk_alert_status(tmp_path: Path) -> None:
    _seed_all_ok(tmp_path)
    _write(tmp_path / "risk_alert_20260516_110000.json", {"status": "STOP_LOSS_ALERT", "alerts": ["stop"], "warnings": []})
    summary = build_daily_ops_summary(output_dir=tmp_path, target_date="2026-05-16")
    assert summary.status == STATUS_RISK_ALERT
    assert "RISK_ALERT.md" in summary.next_actions[0]


def test_reconcile_mismatch_status(tmp_path: Path) -> None:
    _seed_all_ok(tmp_path)
    _write(tmp_path / "reconcile_live_account_20260516_110000.json", {"success": False, "missing_in_db": [{"symbol": "005930"}]})
    summary = build_daily_ops_summary(output_dir=tmp_path, target_date="2026-05-16")
    assert summary.status == STATUS_RECONCILE_MISMATCH


def test_sell_plan_review_warning(tmp_path: Path) -> None:
    _seed_all_ok(tmp_path)
    _write(tmp_path / "sell_plan_20260516_110000.json", {"status": "REVIEW", "items": [{"symbol": "005930"}]})
    summary = build_daily_ops_summary(output_dir=tmp_path, target_date="2026-05-16")
    assert summary.status == STATUS_WARNING


def test_no_data_status(tmp_path: Path) -> None:
    summary = build_daily_ops_summary(output_dir=tmp_path, target_date="2026-05-16", include_latest_fallback=False)
    assert summary.status == STATUS_NO_DATA
    assert summary.warnings


def test_fallback_warning(tmp_path: Path) -> None:
    _seed_all_ok(tmp_path, token="20260515")
    summary = build_daily_ops_summary(output_dir=tmp_path, target_date="2026-05-16")
    assert summary.status == STATUS_OK
    assert any("fallback" in w for w in summary.warnings)


def test_markdown_and_json_generation(tmp_path: Path) -> None:
    _seed_all_ok(tmp_path)
    summary = build_daily_ops_summary(output_dir=tmp_path, target_date="2026-05-16")
    jp, mp = write_daily_ops_summary(summary, output_dir=tmp_path)
    assert jp.is_file()
    assert mp.is_file()
    body = json.loads(jp.read_text(encoding="utf-8"))
    assert body["status"] == STATUS_OK
    assert body["no_orders_placed"] is True
    text = mp.read_text(encoding="utf-8")
    assert "# DeepSignal Daily Operations Summary" in text
    assert "No critical action" in text
    assert "does not place orders" in text
