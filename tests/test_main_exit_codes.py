"""main() 종료 코드: run-daily는 success 기준 0/1, 그 외 0."""

from __future__ import annotations

import main as main_mod
from deepsignal.pipelines.daily_pipeline import DailyPipelineResult


def _ok_result() -> DailyPipelineResult:
    return DailyPipelineResult(
        started_at="t0",
        finished_at="t1",
        symbols=("AAPL",),
        success=True,
    )


def _fail_result() -> DailyPipelineResult:
    return DailyPipelineResult(
        started_at="t0",
        finished_at="t1",
        symbols=("AAPL",),
        success=False,
        errors=["forced failure"],
    )


def test_main_run_daily_exit_zero_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "deepsignal.pipelines.daily_pipeline.run_daily_pipeline",
        lambda *_a, **_k: _ok_result(),
    )
    monkeypatch.setattr(
        "deepsignal.notifiers.notification_service.notify_pipeline_failure",
        lambda *_a, **_k: False,
    )
    assert main_mod.main(["run-daily", "--skip-news", "--skip-market"]) == 0


def test_main_run_daily_exit_one_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "deepsignal.pipelines.daily_pipeline.run_daily_pipeline",
        lambda *_a, **_k: _fail_result(),
    )
    monkeypatch.setattr(
        "deepsignal.notifiers.notification_service.notify_pipeline_failure",
        lambda *_a, **_k: False,
    )
    assert main_mod.main(["run-daily", "--skip-news", "--skip-market"]) == 1


def test_main_default_init_exit_zero() -> None:
    assert main_mod.main([]) == 0


def test_main_show_signals_exit_zero() -> None:
    assert main_mod.main(["show-signals"]) == 0
