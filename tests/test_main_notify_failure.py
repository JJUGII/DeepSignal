"""run-daily 실패 시 알림 호출 (종료 코드는 변하지 않음)."""

from __future__ import annotations

import main as main_mod
from deepsignal.config.settings import Settings
from deepsignal.pipelines.daily_pipeline import DailyPipelineResult


def _fail() -> DailyPipelineResult:
    return DailyPipelineResult(
        started_at="a",
        finished_at="b",
        symbols=("X",),
        success=False,
        errors=["boom"],
    )


def test_main_failure_invokes_notify_when_enabled(monkeypatch) -> None:
    calls: list[object] = []

    def fake_run(*_a, **_k):
        return _fail()

    def fake_load():
        return Settings(
            db_path="data/x.db",
            notify_on_failure=True,
            webhook_url="https://example.com/h",
        )

    def fake_notify(settings, result):
        calls.append((settings, result))
        return True

    monkeypatch.setattr(
        "deepsignal.pipelines.daily_pipeline.run_daily_pipeline",
        fake_run,
    )
    monkeypatch.setattr("deepsignal.config.settings.load_settings", fake_load)
    monkeypatch.setattr(
        "deepsignal.notifiers.notification_service.notify_pipeline_failure",
        fake_notify,
    )
    assert main_mod.main(["run-daily", "--skip-news", "--skip-market"]) == 1
    assert len(calls) == 1
    assert calls[0][1].success is False


def test_main_failure_notify_failure_still_exit_one(monkeypatch) -> None:
    monkeypatch.setattr(
        "deepsignal.pipelines.daily_pipeline.run_daily_pipeline",
        lambda *_a, **_k: _fail(),
    )
    monkeypatch.setattr(
        "deepsignal.config.settings.load_settings",
        lambda: Settings(
            db_path="data/x.db",
            notify_on_failure=True,
            webhook_url="https://example.com/h",
        ),
    )
    monkeypatch.setattr(
        "deepsignal.notifiers.notification_service.notify_pipeline_failure",
        lambda *_a, **_k: False,
    )
    assert main_mod.main(["run-daily", "--skip-news", "--skip-market"]) == 1


def test_main_failure_no_notify_when_disabled(monkeypatch) -> None:
    calls: list[object] = []

    monkeypatch.setattr(
        "deepsignal.pipelines.daily_pipeline.run_daily_pipeline",
        lambda *_a, **_k: _fail(),
    )
    monkeypatch.setattr(
        "deepsignal.config.settings.load_settings",
        lambda: Settings(db_path="data/x.db", notify_on_failure=False),
    )
    monkeypatch.setattr(
        "deepsignal.notifiers.notification_service.notify_pipeline_failure",
        lambda *a, **k: calls.append(1) or True,
    )
    assert main_mod.main(["run-daily", "--skip-news", "--skip-market"]) == 1
    assert calls == []
