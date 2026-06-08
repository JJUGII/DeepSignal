"""전략 기반 클래스."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping, Sequence


class BaseStrategy(ABC):
    """백테스트·모의·실전에서 공통으로 쓰일 전략 인터페이스 스케치."""

    @abstractmethod
    def on_bar(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """바 단위 의사결정."""
        raise NotImplementedError

    def warmup_bars(self) -> int:
        """지표 워밍업에 필요한 최소 바 수."""
        return 0

    def describe(self) -> str:
        """전략 설명."""
        return self.__class__.__name__
