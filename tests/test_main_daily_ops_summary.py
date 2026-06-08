"""main.py daily-ops-summary CLI smoke."""

from __future__ import annotations

import json
from pathlib import Path

import main as main_mod


def _write(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def test_daily_ops_summary_cli_smoke(tmp_path: Path) -> None:
    token = "20260516"
    _write(tmp_path / f"live_account_snapshot_{token}_100000.json", {"timestamp": "t", "cash": {"cash": 1}, "positions": [{"symbol": "005930"}]})
    _write(tmp_path / f"reconcile_live_account_{token}_100000.json", {"success": True, "matched": ["005930"], "missing_in_db": [], "missing_in_broker": [], "quantity_mismatch": []})
    _write(tmp_path / f"risk_alert_{token}_100000.json", {"status": "WARNING", "alerts": ["loss warning"], "warnings": []})
    _write(tmp_path / f"ops_dashboard_{token}_100000.json", {"status": "WARNING", "positions": [{"symbol": "005930"}], "warnings": ["review"]})
    _write(tmp_path / f"sell_plan_{token}_100000.json", {"status": "REVIEW", "items": [{"symbol": "005930"}]})
    _write(tmp_path / f"notification_audit_{token}_100000.json", {"dry_run": True, "channel": "telegram", "messages": [], "results": []})

    rc = main_mod.main(["daily-ops-summary", "--date", "2026-05-16", "--output-dir", str(tmp_path)])

    assert rc == 0
    reports = list(tmp_path.glob("daily_ops_summary_*.json"))
    assert len(reports) == 1
    body = json.loads(reports[0].read_text(encoding="utf-8"))
    assert body["status"] == "WARNING"
    assert body["next_actions"]
    md = tmp_path / "DAILY_OPS_SUMMARY.md"
    assert md.is_file()
    assert "Review warnings" in md.read_text(encoding="utf-8")


def test_daily_ops_summary_notify_dry_run_creates_audit(tmp_path: Path) -> None:
    token = "20260516"
    _write(tmp_path / f"live_account_snapshot_{token}_100000.json", {"timestamp": "t", "cash": {"cash": 1}, "positions": []})
    _write(tmp_path / f"reconcile_live_account_{token}_100000.json", {"success": True, "matched": [], "missing_in_db": [], "missing_in_broker": [], "quantity_mismatch": []})
    _write(tmp_path / f"risk_alert_{token}_100000.json", {"status": "OK", "alerts": [], "warnings": []})
    _write(tmp_path / f"ops_dashboard_{token}_100000.json", {"status": "OK", "positions": [], "warnings": []})
    _write(tmp_path / f"sell_plan_{token}_100000.json", {"status": "HOLD", "items": []})

    rc = main_mod.main(["daily-ops-summary", "--date", "2026-05-16", "--notify-dry-run", "--output-dir", str(tmp_path)])

    assert rc == 0
    audits = list(tmp_path.glob("notification_audit_*.json"))
    assert len(audits) == 1
    reports = list(tmp_path.glob("daily_ops_summary_*.json"))
    body = json.loads(reports[0].read_text(encoding="utf-8"))
    assert body["notification"]["dry_run"] is True
