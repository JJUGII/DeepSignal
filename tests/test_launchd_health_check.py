"""launchd_health_check — post-login service verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deepsignal.live_trading.launchd_health_check import (
    REQUIRED_SERVICES,
    format_health_telegram,
    run_launchd_health_check,
)


def test_format_health_telegram_all_ok() -> None:
    from deepsignal.live_trading.launchd_health_check import LaunchdHealthCheckResult, ServiceStatus

    result = LaunchdHealthCheckResult(
        checked_at="2026-01-01T00:00:00+09:00",
        launch_root="/tmp/root",
        project_root="/tmp/root",
        delay_seconds=90.0,
        infrastructure_ok=True,
        services=[
            ServiceStatus(label="a", display_name="코인", running=True, state="running"),
            ServiceStatus(label="b", display_name="KIS", running=True, state="running"),
        ],
        all_running=True,
    )
    text = format_health_telegram(result)
    assert "재기동 점검" in text
    assert "✅" in text
    assert "재부팅 점검 완료" in text


def test_run_health_check_detects_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_health_check.launch_root_symlink_path",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_health_check.project_root",
        lambda _=None: tmp_path,
    )
    (tmp_path / "main.py").write_text("# test\n", encoding="utf-8")
    venv_py = tmp_path / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")

    def fake_status(label: str, *, display_name: str):
        from deepsignal.live_trading.launchd_health_check import ServiceStatus

        running = label == REQUIRED_SERVICES[0][0]
        return ServiceStatus(
            label=label,
            display_name=display_name,
            loaded=True,
            running=running,
            state="running" if running else "spawn",
        )

    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_health_check.launchctl_service_status",
        fake_status,
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_health_check.kickstart_service",
        lambda _label: (True, "ok"),
    )
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_health_check.health_send_telegram_enabled",
        lambda **_: False,
    )

    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_health_check.health_check_telegram_bot_enabled",
        lambda **_: False,
    )
    result = run_launchd_health_check(wait_seconds=0.0, kickstart_missing=True)
    assert not result.all_running
    assert len(result.services) == 3
