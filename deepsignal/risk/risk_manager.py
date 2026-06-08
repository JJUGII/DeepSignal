"""리스크 한도·사이징. 스캐폴딩만 제공."""

from __future__ import annotations

from typing import Any, Mapping


class RiskManager:
    """포지션 한도·드로다운 등 제어."""

    def evaluate(self, portfolio: Mapping[str, Any]) -> Mapping[str, Any]:
        """추후 VaR·Kelly 등."""
        raise NotImplementedError
