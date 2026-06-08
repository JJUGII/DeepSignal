"""notification_service 단위."""

from __future__ import annotations

from dataclasses import replace

from deepsignal.config.settings import Settings
from deepsignal.notifiers import notification_service as ns
from deepsignal.pipelines.daily_pipeline import DailyPipelineResult


def _fail_result() -> DailyPipelineResult:
    return DailyPipelineResult(
        started_at="a",
        finished_at="b",
        symbols=("Z",),
        success=False,
        errors=["e1"],
        summary={"failed_steps": 1},
        log_json_path="/tmp/log.json",
    )


def test_notify_skipped_when_success() -> None:
    ok = DailyPipelineResult(
        started_at="a",
        finished_at="b",
        symbols=("Z",),
        success=True,
    )
    s = replace(Settings(db_path="d.db"), notify_on_failure=True, webhook_url="http://x")
    assert ns.notify_pipeline_failure(s, ok) is False


def test_notify_skipped_when_flag_off() -> None:
    s = replace(Settings(db_path="d.db"), notify_on_failure=False, webhook_url="http://x")
    assert ns.notify_pipeline_failure(s, _fail_result()) is False


def test_notify_calls_webhook_on_failure(monkeypatch) -> None:
    calls: list[tuple] = []

    class FakeNotifier:
        def __init__(self, url, *, timeout_seconds=5.0):
            calls.append(("init", url, timeout_seconds))

        def send(self, title, message, payload=None):
            calls.append(("send", title, message, payload))
            return True

    monkeypatch.setattr(ns, "WebhookNotifier", FakeNotifier)
    s = replace(
        Settings(db_path="d.db"),
        notify_on_failure=True,
        webhook_url="https://hook.example/x",
        notify_timeout_seconds=7,
    )
    r = _fail_result()
    assert ns.notify_pipeline_failure(s, r) is True
    assert any(c[0] == "send" for c in calls)
    send_row = [c for c in calls if c[0] == "send"][0]
    assert send_row[3]["log_json_path"] == "/tmp/log.json"
    assert "errors" in send_row[3]
