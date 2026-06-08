"""예시 전략 (스캐폴딩)."""

from __future__ import annotations

from typing import Any, Mapping

from deepsignal.strategy.base_strategy import BaseStrategy


class SampleStrategy(BaseStrategy):
    """추후 규칙/ML 전략으로 대체."""

    def on_bar(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError
