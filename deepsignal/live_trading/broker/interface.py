"""
브로커 주문 인터페이스.

실전 주문은 백테스트와 모의투자 검증 후 구현한다.
`place_order(..., execute=True)` 는 가드 통과 후에만 호출되어야 한다.
[실전-5] 조회·동기화용 타입 및 선택 메서드 추가.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class BrokerOrderRequest:
    symbol: str
    side: str
    quantity: int
    order_type: str = "LIMIT"
    limit_price: float | None = None
    estimated_value: float | None = None
    client_order_id: str | None = None
    source_plan_id: str | None = None


@dataclass
class BrokerOrderResult:
    symbol: str
    side: str
    quantity: int
    order_type: str
    status: str
    broker_order_id: str | None
    submitted_price: float | None
    message: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerOrderStatus:
    """브로커 주문 1건 조회 요약 (파싱 실패 시에도 `raw` 보존)."""

    order_id: str | None
    symbol: str
    side: str | None
    quantity: int | None
    filled_quantity: int | None
    remaining_quantity: int | None
    order_price: float | None
    avg_fill_price: float | None
    status: str
    message: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerPosition:
    symbol: str
    quantity: int
    avg_price: float | None
    current_price: float | None
    market_value: float | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerCashBalance:
    cash: float | None
    withdrawable_cash: float | None
    raw: dict[str, Any] = field(default_factory=dict)


class BrokerInterface(ABC):
    """실전 브로커 어댑터가 구현할 추상 인터페이스."""

    @abstractmethod
    def connect(self) -> None:
        """세션 수립 (미구현)."""
        raise NotImplementedError

    @abstractmethod
    def submit_order(self, order: Mapping[str, Any]) -> Mapping[str, Any]:
        """주문 전송 (미구현). 실전 연동 시에만 구체화."""
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[BrokerPosition]:
        """보유 종목 조회 ([실전-5]: `BrokerPosition` 목록)."""
        raise NotImplementedError

    @abstractmethod
    def place_order(
        self,
        request: BrokerOrderRequest,
        *,
        execute: bool = False,
    ) -> BrokerOrderResult:
        """구조화 주문 제출. `execute=True` 는 `LiveExecutionGuard` 통과 후에만."""
        raise NotImplementedError

    def get_order_status(
        self,
        *,
        order_id: str | None = None,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[BrokerOrderStatus]:
        """주문·체결 조회. 기본 미구현 — `KISBroker` 등에서 오버라이드."""
        raise NotImplementedError(f"{type(self).__name__} does not implement get_order_status")

    def get_cash_balance(self) -> BrokerCashBalance:
        """현금·주문가능금 등. 기본 미구현 — `KISBroker` 등에서 오버라이드."""
        raise NotImplementedError(f"{type(self).__name__} does not implement get_cash_balance")
