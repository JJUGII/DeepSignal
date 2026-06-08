"""notification_center: alert-only 알림."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from deepsignal.live_trading.notification_center import (
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    build_alert_messages,
    load_latest_alert_sources,
    notify_alerts,
    send_discord_alert,
    send_telegram_alert,
)


def _write(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def test_risk_warning_builds_alert_message(tmp_path: Path) -> None:
    _write(
        tmp_path / "risk_alert_20260516_100000.json",
        {
            "status": "WARNING",
            "positions": [{"symbol": "005930", "risk_level": "WARNING", "unrealized_pnl_pct": -0.0339, "alerts": ["loss warning"]}],
        },
    )
    messages = build_alert_messages(load_latest_alert_sources(tmp_path))
    assert len(messages) == 1
    assert messages[0].severity == SEVERITY_WARNING
    assert "005930" in messages[0].body
    assert "No orders were placed" in messages[0].body


def test_reconcile_mismatch_is_critical(tmp_path: Path) -> None:
    _write(
        tmp_path / "reconcile_live_account_20260516_100000.json",
        {"success": False, "missing_in_db": [{"symbol": "005930"}], "missing_in_broker": [], "quantity_mismatch": []},
    )
    messages = build_alert_messages(load_latest_alert_sources(tmp_path))
    assert len(messages) == 1
    assert messages[0].severity == SEVERITY_CRITICAL
    assert messages[0].source == "reconcile"


def test_sell_plan_review_builds_warning(tmp_path: Path) -> None:
    _write(
        tmp_path / "sell_plan_20260516_100000.json",
        {"status": "REVIEW", "items": [{"symbol": "005930", "suggested_action": "REVIEW", "suggested_sell_quantity": 0}]},
    )
    messages = build_alert_messages(load_latest_alert_sources(tmp_path))
    assert len(messages) == 1
    assert messages[0].severity == SEVERITY_WARNING
    assert "Sell plan: REVIEW" in messages[0].body


def test_ok_status_excluded_by_default_and_included_when_requested(tmp_path: Path) -> None:
    _write(tmp_path / "risk_alert_20260516_100000.json", {"status": "OK", "positions": []})
    messages = build_alert_messages(load_latest_alert_sources(tmp_path))
    assert messages == []
    messages_ok = build_alert_messages(load_latest_alert_sources(tmp_path), include_ok=True)
    assert len(messages_ok) == 1
    assert messages_ok[0].severity == SEVERITY_INFO


def test_weekly_maintenance_warning_builds_alert_message(tmp_path: Path) -> None:
    _write(
        tmp_path / "weekly_maintenance_20260517_100000.json",
        {
            "final_status": "WEEKLY_MAINTENANCE_WARNING",
            "warnings": ["AppleDouble files found", "cleanup candidates: 12"],
            "next_actions": ["Review WEEKLY_MAINTENANCE.md", "Run cleanup-reports --dry-run before applying cleanup"],
        },
    )

    messages = build_alert_messages(load_latest_alert_sources(tmp_path, include_maintenance=True))

    assert len(messages) == 1
    assert messages[0].source == "weekly_maintenance"
    assert messages[0].severity == SEVERITY_WARNING
    assert "WEEKLY_MAINTENANCE_WARNING" in messages[0].body
    assert "AppleDouble files found" in messages[0].body
    assert messages[0].metadata["maintenance_status"] == "WEEKLY_MAINTENANCE_WARNING"
    assert messages[0].metadata["source_file"].endswith("weekly_maintenance_20260517_100000.json")


def test_weekly_maintenance_critical_builds_critical_message(tmp_path: Path) -> None:
    _write(
        tmp_path / "weekly_maintenance_20260517_100000.json",
        {"final_status": "WEEKLY_MAINTENANCE_CRITICAL", "steps": [{"name": "report_health_check", "status": "HEALTH_CRITICAL", "message": "DB failed"}]},
    )

    messages = build_alert_messages(load_latest_alert_sources(tmp_path, include_maintenance=True))

    assert len(messages) == 1
    assert messages[0].severity == SEVERITY_CRITICAL
    assert "DB failed" in messages[0].body


def test_report_health_warning_builds_alert_message(tmp_path: Path) -> None:
    _write(
        tmp_path / "report_health_20260517_100000.json",
        {
            "status": "HEALTH_WARNING",
            "issues": [{"severity": "WARNING", "category": "reports", "message": "latest risk_alert older than 24h"}],
            "next_actions": ["Run python main.py ops-dry-run --network --broker kis"],
        },
    )

    messages = build_alert_messages(load_latest_alert_sources(tmp_path, include_maintenance=True))

    assert len(messages) == 1
    assert messages[0].source == "report_health"
    assert messages[0].severity == SEVERITY_WARNING
    assert "HEALTH_WARNING" in messages[0].body
    assert "latest risk_alert older than 24h" in messages[0].body
    assert messages[0].metadata["health_status"] == "HEALTH_WARNING"


def test_maintenance_ok_excluded_by_default_and_included_when_requested(tmp_path: Path) -> None:
    _write(tmp_path / "weekly_maintenance_20260517_100000.json", {"final_status": "WEEKLY_MAINTENANCE_OK", "warnings": []})
    _write(tmp_path / "report_health_20260517_100000.json", {"status": "HEALTH_OK", "issues": []})

    messages = build_alert_messages(load_latest_alert_sources(tmp_path, include_maintenance=True))
    assert messages == []

    messages_ok = build_alert_messages(load_latest_alert_sources(tmp_path, include_maintenance=True), include_ok=True)
    assert {m.source for m in messages_ok} == {"weekly_maintenance", "report_health"}
    assert all(m.severity == SEVERITY_INFO for m in messages_ok)


def test_dry_run_does_not_call_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write(tmp_path / "risk_alert_20260516_100000.json", {"status": "WARNING", "positions": []})
    post = Mock()
    monkeypatch.setattr("deepsignal.live_trading.notification_center.requests.post", post)
    messages, results, audit = notify_alerts(output_dir=tmp_path, dry_run=True, channel="telegram")
    assert len(messages) == 1
    assert results[0].status == "dry_run"
    assert audit.is_file()
    post.assert_not_called()


def test_telegram_payload_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        status_code = 200

    post = Mock(return_value=Resp())
    monkeypatch.setattr("deepsignal.live_trading.notification_center.requests.post", post)
    result = send_telegram_alert(
        build_alert_messages({"risk": {"status": "WARNING", "positions": []}}),
        bot_token="token",
        chat_id="chat",
    )
    assert result.success
    args, kwargs = post.call_args
    assert "bottoken/sendMessage" in args[0]
    assert kwargs["json"]["chat_id"] == "chat"
    assert "DeepSignal WARNING" in kwargs["json"]["text"]


def test_discord_payload_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        status_code = 204

    post = Mock(return_value=Resp())
    monkeypatch.setattr("deepsignal.live_trading.notification_center.requests.post", post)
    result = send_discord_alert(
        build_alert_messages({"sell_plan": {"status": "REVIEW", "items": []}}),
        webhook_url="https://discord.example/webhook",
    )
    assert result.success
    _args, kwargs = post.call_args
    assert "DeepSignal WARNING" in kwargs["json"]["content"]
