"""학습·추론 파이프라인. 스캐폴딩만 제공."""

from __future__ import annotations

from typing import Any, Mapping


class ModelPipeline:
    """피처 생성·학습·평가."""

    def train(self, dataset: Mapping[str, Any]) -> None:
        """추후 XGBoost/LSTM 등."""
        raise NotImplementedError

    def predict(self, features: Mapping[str, Any]) -> Mapping[str, Any]:
        """추후 추론."""
        raise NotImplementedError
