"""공통 코인 브로커 타입·인터페이스 (거래소 독립)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable

MIN_ORDER_KRW = 5_000


@dataclass
class CryptoTicker:
    market: str
    trade_price: float
    signed_change_rate: float
    acc_trade_price_24h: float


@dataclass
class CryptoBalance:
    currency: str
    balance: float
    locked: float
    avg_buy_price: float


@dataclass
class CryptoHolding:
    market: str
    currency: str
    balance: float
    locked: float
    available: float
    avg_buy_price: float
    current_price: float
    valuation_krw: float
    pnl_pct: float
    pnl_krw: float

    @property
    def total_quantity(self) -> float:
        return self.balance + self.locked


@dataclass
class CryptoOrderResult:
    market: str
    side: str
    order_type: str
    price: float
    volume: float
    krw_amount: float
    status: str
    uuid: str | None = None
    dry_run: bool = True
    raw: dict[str, Any] | None = None


@runtime_checkable
class CryptoBroker(Protocol):
    """1단계: 조회 API. 2단계에서 주문 메서드 확장."""

    @property
    def exchange_id(self) -> str: ...

    def get_balances(self) -> list[CryptoBalance]: ...

    def get_krw_available(self) -> float: ...

    def get_crypto_holdings(self) -> list[CryptoHolding]: ...

    def get_ticker(self, market: str) -> CryptoTicker: ...

    def get_tickers(self, markets: Sequence[str]) -> dict[str, CryptoTicker]: ...

    def get_daily_candles(self, market: str, *, count: int = 20) -> list[dict[str, Any]]: ...

    def check_connection(self) -> bool: ...

    def place_limit_buy(
        self,
        *,
        market: str,
        krw_amount: float,
        price: float | None = None,
        execute: bool = False,
    ) -> CryptoOrderResult: ...

    def place_limit_sell(
        self,
        *,
        market: str,
        volume: float,
        price: float | None = None,
        execute: bool = False,
    ) -> CryptoOrderResult: ...

    def get_order(self, uuid: str) -> dict[str, Any]: ...

    def cancel_order(self, uuid: str) -> dict[str, Any]: ...

    def get_open_orders(self, market: str | None = None) -> list[dict[str, Any]]: ...
