"""알림 전송 추상 인터페이스."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Notifier(ABC):
    """제목·본문·부가 JSON으로 알림을 보낸다. 성공 여부만 bool로 반환."""

    @abstractmethod
    def send(self, title: str, message: str, payload: dict[str, Any] | None = None) -> bool:
        """전송 성공이면 True, 건너뜀·실패면 False."""
