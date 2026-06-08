"""run-daily 실패 시 선택적 알림."""

from __future__ import annotations

from typing import Any

from deepsignal.config.settings import Settings
from deepsignal.notifiers.webhook_notifier import WebhookNotifier
from deepsignal.pipelines.daily_pipeline import DailyPipelineResult


def notify_pipeline_failure(settings: Settings, result: DailyPipelineResult) -> bool:
    """파이프라인 실패 시에만(설정 활성 시) 웹훅 전송. 성공이면 항상 False."""
    if result.success:
        return False
    if not settings.notify_on_failure:
        return False

    notifier = WebhookNotifier(
        settings.webhook_url,
        timeout_seconds=float(settings.notify_timeout_seconds),
    )
    errors = result.errors or []
    payload: dict[str, Any] = {
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "symbols": list(result.symbols),
        "summary": result.summary,
        "options": result.options,
        "errors": errors[:50],
        "log_json_path": result.log_json_path,
    }
    title = "DeepSignal run-daily failed"
    msg = (
        f"success={result.success}, symbols={list(result.symbols)}, "
        f"failed_steps={result.summary.get('failed_steps', 'n/a')}"
    )
    return notifier.send(title, msg, payload)
