"""main.py notify-alerts CLI smoke."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import main as main_mod


def _write(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def test_notify_alerts_dry_run_smoke(monkeypatch, tmp_path: Path) -> None:
    _write(tmp_path / "risk_alert_20260516_100000.json", {"status": "WARNING", "positions": []})
    post = Mock()
    monkeypatch.setattr("deepsignal.live_trading.notification_center.requests.post", post)
    rc = main_mod.main(["notify-alerts", "--dry-run", "--output-dir", str(tmp_path)])
    assert rc == 0
    audits = list(tmp_path.glob("notification_audit_*.json"))
    assert len(audits) == 1
    body = json.loads(audits[0].read_text(encoding="utf-8"))
    assert body["dry_run"] is True
    assert body["실제_주문_없음"] is True
    assert len(body["messages"]) == 1
    post.assert_not_called()


def test_notify_alerts_without_send_never_calls_network(monkeypatch, tmp_path: Path) -> None:
    _write(tmp_path / "sell_plan_20260516_100000.json", {"status": "EXIT", "items": []})
    post = Mock()
    monkeypatch.setattr("deepsignal.live_trading.notification_center.requests.post", post)
    rc = main_mod.main(["notify-alerts", "--channel", "discord", "--output-dir", str(tmp_path)])
    assert rc == 0
    post.assert_not_called()


def test_notify_alerts_send_missing_env_fails(monkeypatch, tmp_path: Path) -> None:
    _write(tmp_path / "risk_alert_20260516_100000.json", {"status": "WARNING", "positions": []})
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", raising=False)
    rc = main_mod.main(["notify-alerts", "--channel", "telegram", "--send", "--output-dir", str(tmp_path)])
    assert rc == 1
    audits = list(tmp_path.glob("notification_audit_*.json"))
    assert len(audits) == 1
    body = json.loads(audits[0].read_text(encoding="utf-8"))
    assert body["results"][0]["status"] == "missing_config"


def test_notify_alerts_send_discord_mock(monkeypatch, tmp_path: Path) -> None:
    _write(tmp_path / "ops_dashboard_20260516_100000.json", {"status": "WARNING", "positions": [], "warnings": ["review"]})
    monkeypatch.setenv("DEEPSIGNAL_NOTIFY_DISCORD_WEBHOOK_URL", "https://discord.example/webhook")

    class Resp:
        status_code = 204

    post = Mock(return_value=Resp())
    monkeypatch.setattr("deepsignal.live_trading.notification_center.requests.post", post)
    rc = main_mod.main(["notify-alerts", "--channel", "discord", "--send", "--output-dir", str(tmp_path)])
    assert rc == 0
    post.assert_called_once()
    audits = list(tmp_path.glob("notification_audit_*.json"))
    assert len(audits) == 1
    body = json.loads(audits[0].read_text(encoding="utf-8"))
    assert body["dry_run"] is False
    assert body["results"][0]["success"] is True


def test_notify_alerts_include_maintenance_dry_run(monkeypatch, tmp_path: Path) -> None:
    _write(
        tmp_path / "weekly_maintenance_20260517_100000.json",
        {
            "final_status": "WEEKLY_MAINTENANCE_WARNING",
            "warnings": ["cleanup candidates: 12"],
            "next_actions": ["Review WEEKLY_MAINTENANCE.md"],
        },
    )
    post = Mock()
    monkeypatch.setattr("deepsignal.live_trading.notification_center.requests.post", post)

    rc = main_mod.main(["notify-alerts", "--dry-run", "--include-maintenance", "--output-dir", str(tmp_path)])

    assert rc == 0
    post.assert_not_called()
    audits = list(tmp_path.glob("notification_audit_*.json"))
    assert len(audits) == 1
    body = json.loads(audits[0].read_text(encoding="utf-8"))
    assert body["dry_run"] is True
    assert body["actual_order_attempted"] is False
    assert body["실제_주문_없음"] is True
    assert len(body["messages"]) == 1
    assert body["messages"][0]["source"] == "weekly_maintenance"
    assert body["messages"][0]["metadata"]["maintenance_status"] == "WEEKLY_MAINTENANCE_WARNING"
    assert "cleanup candidates: 12" in body["messages"][0]["body"]


def test_notify_alerts_without_include_maintenance_keeps_existing_sources(tmp_path: Path) -> None:
    _write(
        tmp_path / "weekly_maintenance_20260517_100000.json",
        {"final_status": "WEEKLY_MAINTENANCE_CRITICAL", "warnings": ["DB failed"]},
    )

    rc = main_mod.main(["notify-alerts", "--dry-run", "--output-dir", str(tmp_path)])

    assert rc == 0
    audits = list(tmp_path.glob("notification_audit_*.json"))
    body = json.loads(audits[0].read_text(encoding="utf-8"))
    assert body["messages"] == []
