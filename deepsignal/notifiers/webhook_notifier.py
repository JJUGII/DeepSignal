"""HTTP POST(JSON) 웹훅 알림."""

from __future__ import annotations

from typing import Any

import requests

from deepsignal.notifiers.base import Notifier


class WebhookNotifier(Notifier):
    """`WEBHOOK_URL`로 `requests.post` JSON 전송."""

    def __init__(self, url: str | None, *, timeout_seconds: float = 5.0) -> None:
        self._url = (url or "").strip() or None
        self._timeout = float(timeout_seconds)

    def send(self, title: str, message: str, payload: dict[str, Any] | None = None) -> bool:
        if not self._url:
            return False
        body: dict[str, Any] = {"title": title, "message": message}
        if payload:
            body["detail"] = payload
        try:
            resp = requests.post(self._url, json=body, timeout=self._timeout)
            return 200 <= resp.status_code < 300
        except (OSError, requests.RequestException):
            return False
