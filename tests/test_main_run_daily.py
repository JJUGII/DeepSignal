"""main.py run-daily 서브커맨드 스모크 (파이프라인 본체는 monkeypatch)."""

from __future__ import annotations

import main as main_mod
from deepsignal.pipelines.daily_pipeline import DailyPipelineResult


def test_main_run_daily_calls_pipeline(monkeypatch) -> None:
    calls: list[object] = []

    def fake_run_daily(settings, **_kwargs) -> DailyPipelineResult:
        calls.append(settings)
        return DailyPipelineResult(
            started_at="t0",
            finished_at="t1",
            symbols=(),
            success=True,
        )

    monkeypatch.setattr(
        "deepsignal.pipelines.daily_pipeline.run_daily_pipeline",
        fake_run_daily,
    )
    assert main_mod.main(["run-daily"]) == 0
    assert len(calls) == 1
    assert calls[0].db_path  # Settings