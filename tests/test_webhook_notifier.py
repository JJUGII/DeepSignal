"""WebhookNotifier 단위 (네트워크 없음)."""

from __future__ import annotations

from unittest.mock import MagicMock

from deepsignal.notifiers.webhook_notifier import WebhookNotifier


def test_webhook_notifier_no_url_returns_false() -> None:
    n = WebhookNotifier(None)
    assert n.send("t", "m") is False
    n2 = WebhookNotifier("   ")
    assert n2.send("t", "m") is False


def test_webhook_notifier_success_on_200(monkeypatch) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    def fake_post(url, json=None, timeout=None):
        assert "hook" in url
        assert json["title"] == "t"
        return mock_resp

    monkeypatch.setattr(
        "deepsignal.notifiers.webhook_notifier.requests.post",
        fake_post,
    )
    n = WebhookNotifier("https://example.com/hook", timeout_seconds=5.0)
    assert n.send("t", "m", {"k": 1}) is True


def test_webhook_notifier_false_on_exception(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("network")

    monkeypatch.setattr(
        "deepsignal.notifiers.webhook_notifier.requests.post",
        boom,
    )
    n = WebhookNotifier("https://example.com/hook")
    assert n.send("t", "m") is False


def test_webhook_notifier_false_on_non_2xx(monkeypatch) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 500

    monkeypatch.setattr(
        "deepsignal.notifiers.webhook_notifier.requests.post",
        lambda *_a, **_k: mock_resp,
    )
    n = WebhookNotifier("https://example.com/hook")
    assert n.send("t", "m") is False
