"""Upbit KRW market broker MVP — balance, ticker, limit buy (dry-run safe)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import requests

from deepsignal.crypto_trading.crypto_paper_mode import orders_blocked_by_paper_or_dry_run
from deepsignal.crypto_trading.upbit_auth import authorization_header
from deepsignal.crypto_trading.upbit_config import UpbitConfig
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

UPBIT_API_BASE = "https://api.upbit.com/v1"
MIN_ORDER_KRW = 5_000
_POLICY_MIN_ORDER_KRW = max(
    MIN_ORDER_KRW,
    float(DEFAULT_ANALYSIS_CONDITIONS.cost.min_order_value_krw),
)
UPBIT_RETRY_STATUS_CODES = frozenset({429})
UPBIT_RETRY_DELAYS_SEC = (0.5, 1.0, 2.0)
UPBIT_MAX_RETRIES = 3


class UpbitBrokerError(ValueError):
    pass


@dataclass
class UpbitBalance:
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
class UpbitTicker:
    market: str
    trade_price: float
    signed_change_rate: float
    acc_trade_price_24h: float


@dataclass
class UpbitOrderResult:
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


class UpbitBroker:
    """Upbit REST adapter. `dry_run=True` or `execute=False` never POSTs orders."""

    def __init__(
        self,
        config: UpbitConfig,
        *,
        session: requests.Session | None = None,
        request_fn: Callable[..., requests.Response] | None = None,
    ) -> None:
        self._config = config
        self._session = session or requests.Session()
        self._request_fn = request_fn

    @property
    def config(self) -> UpbitConfig:
        return self._config

    def _orders_blocked(self, *, execute: bool) -> bool:
        return orders_blocked_by_paper_or_dry_run(
            dry_run=self._config.dry_run,
            paper_mode=self._config.paper_mode,
            execute=execute,
        )

    def _dry_run_order_status(self) -> str:
        if self._config.paper_mode:
            return "CRYPTO_PAPER_MODE_BLOCKED"
        return "UPBIT_DRY_RUN_BLOCKED"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        if path == "/orders" and method.upper() == "POST":
            if self._config.paper_mode:
                raise UpbitBrokerError("CRYPTO_PAPER_MODE blocks POST /orders")
            if self._config.dry_run:
                raise UpbitBrokerError("dry_run broker cannot POST /orders without execute=True")
        if path == "/order" and method.upper() == "DELETE" and self._config.paper_mode:
            raise UpbitBrokerError("CRYPTO_PAPER_MODE blocks DELETE /order (cancel)")

        url = f"{UPBIT_API_BASE}{path}"
        query = params if method.upper() in ("GET", "DELETE") else json_body
        headers = {"Accept": "application/json"}
        if not self._config.dry_run or not self._config.is_demo:
            headers.update(
                authorization_header(self._config.access_key, self._config.secret_key, query=query)
            )

        fn = self._request_fn or self._session.request
        last_resp: requests.Response | None = None
        for attempt in range(UPBIT_MAX_RETRIES + 1):
            if method.upper() in ("GET", "DELETE"):
                resp = fn(method.upper(), url, params=params, headers=headers, timeout=15)
            else:
                resp = fn(method.upper(), url, json=json_body, headers=headers, timeout=15)
            last_resp = resp
            if resp.status_code in UPBIT_RETRY_STATUS_CODES and attempt < UPBIT_MAX_RETRIES:
                time.sleep(UPBIT_RETRY_DELAYS_SEC[attempt])
                continue
            break
        assert last_resp is not None
        if last_resp.status_code >= 400:
            text = (last_resp.text or "")[:500]
            raise UpbitBrokerError(f"Upbit HTTP {last_resp.status_code}: {text}")
        if not last_resp.text:
            return {}
        return last_resp.json()

    def check_connection(self) -> bool:
        if self._config.dry_run:
            return True
        self.get_balances()
        return True

    @staticmethod
    def currency_to_market(currency: str) -> str:
        return f"KRW-{currency.strip().upper()}"

    @staticmethod
    def _parse_amount(value: Any) -> float:
        if value is None or value == "":
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def get_balances(self) -> list[UpbitBalance]:
        if self._config.dry_run and self._config.is_demo:
            return [
                UpbitBalance(currency="KRW", balance=100_000.0, locked=0.0, avg_buy_price=0.0),
                UpbitBalance(currency="BTC", balance=0.0, locked=0.0, avg_buy_price=0.0),
                UpbitBalance(currency="XRP", balance=4.99251123, locked=0.0, avg_buy_price=2_003.0),
            ]
        raw = self._request("GET", "/accounts")
        if not isinstance(raw, list):
            raise UpbitBrokerError(f"unexpected accounts response: {raw!r}")
        out: list[UpbitBalance] = []
        for row in raw:
            out.append(
                UpbitBalance(
                    currency=str(row.get("currency", "")),
                    balance=self._parse_amount(row.get("balance")),
                    locked=self._parse_amount(row.get("locked")),
                    avg_buy_price=self._parse_amount(row.get("avg_buy_price")),
                )
            )
        return out

    def get_crypto_holdings(self) -> list[CryptoHolding]:
        """Evaluate non-KRW balances with current price and PnL.

        Upbit /accounts: ``balance`` = orderable, ``locked`` = in open orders.
        Total holding = balance + locked.
        """
        holdings: list[CryptoHolding] = []
        for bal in self.get_balances():
            cur = bal.currency.upper()
            if not cur or cur == "KRW":
                continue
            orderable = max(bal.balance, 0.0)
            locked = max(bal.locked, 0.0)
            total_qty = orderable + locked
            if total_qty <= 0:
                continue
            market = self.currency_to_market(cur)
            current = 0.0
            try:
                ticker = self.get_ticker(market)
                current = ticker.trade_price
            except Exception:
                if bal.avg_buy_price > 0:
                    current = bal.avg_buy_price
                else:
                    continue
            avg = bal.avg_buy_price if bal.avg_buy_price > 0 else current
            valuation = total_qty * current
            cost = total_qty * avg
            pnl_krw = valuation - cost
            pnl_pct = ((current - avg) / avg * 100.0) if avg > 0 else 0.0
            holdings.append(
                CryptoHolding(
                    market=market,
                    currency=cur,
                    balance=orderable,
                    locked=locked,
                    available=orderable,
                    avg_buy_price=avg,
                    current_price=current,
                    valuation_krw=valuation,
                    pnl_pct=pnl_pct,
                    pnl_krw=pnl_krw,
                )
            )
        return holdings

    def get_krw_available(self) -> float:
        for b in self.get_balances():
            if b.currency.upper() == "KRW":
                return max(b.balance, 0.0)
        return 0.0

    def get_daily_candles(self, market: str, *, count: int = 20) -> list[dict[str, Any]]:
        """Upbit 일봉 (public). dry-run은 mock candles."""
        m = market.strip().upper()
        n = max(2, min(int(count), 200))
        if self._config.dry_run and self._config.is_demo:
            from deepsignal.crypto_trading.crypto_market_data import mock_daily_candles

            return mock_daily_candles(m, count=n)
        try:
            rows = self._request("GET", "/candles/days", params={"market": m, "count": n})
        except UpbitBrokerError as exc:
            if "404" in str(exc):
                return []
            raise
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "candle_date_time_kst": str(row.get("candle_date_time_kst") or row.get("candle_date_time_utc") or "")[:10],
                    "trade_price": float(row.get("trade_price", 0) or 0),
                    "opening_price": float(row.get("opening_price", 0) or 0),
                    "high_price": float(row.get("high_price", 0) or 0),
                    "low_price": float(row.get("low_price", 0) or 0),
                    "candle_acc_trade_volume": float(row.get("candle_acc_trade_volume", 0) or 0),
                }
            )
        return out

    @staticmethod
    def _parse_ticker_row(row: dict[str, Any], *, default_market: str = "") -> UpbitTicker:
        m = str(row.get("market", default_market)).upper()
        return UpbitTicker(
            market=m,
            trade_price=float(row.get("trade_price", 0) or 0),
            signed_change_rate=float(row.get("signed_change_rate", 0) or 0),
            acc_trade_price_24h=float(row.get("acc_trade_price_24h", 0) or 0),
        )

    def get_tickers(self, markets: Sequence[str]) -> dict[str, UpbitTicker]:
        """Batch GET /ticker?markets=KRW-BTC,KRW-ETH,..."""
        normalized = [m.strip().upper() for m in markets if m and m.strip()]
        if not normalized:
            return {}
        if self._config.dry_run and self._config.is_demo:
            from deepsignal.crypto_trading.crypto_market_data import mock_tickers

            return mock_tickers(tuple(normalized))

        try:
            return self._get_tickers_batch(normalized)
        except UpbitBrokerError as exc:
            if "404" not in str(exc):
                raise
            out: dict[str, UpbitTicker] = {}
            for m in normalized:
                try:
                    out.update(self._get_tickers_batch([m]))
                except UpbitBrokerError:
                    continue
            return out

    def _get_tickers_batch(self, normalized: list[str]) -> dict[str, UpbitTicker]:
        markets_param = ",".join(normalized)
        rows = self._request("GET", "/ticker", params={"markets": markets_param})
        if not isinstance(rows, list):
            raise UpbitBrokerError(f"unexpected ticker response: {rows!r}")
        out: dict[str, UpbitTicker] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = self._parse_ticker_row(row)
            out[ticker.market] = ticker
        return out

    def get_ticker(self, market: str) -> UpbitTicker:
        m = market.strip().upper()
        batch = self.get_tickers([m])
        if m not in batch:
            raise UpbitBrokerError(f"ticker not found: {m}")
        return batch[m]

    def get_orderbook(self, market: str, *, levels: int = 5) -> dict[str, Any]:
        """GET /orderbook — best bid/ask and depth units (public)."""
        m = market.strip().upper()
        n = max(1, min(int(levels), 30))
        if self._config.dry_run and self._config.is_demo:
            ticker = self.get_ticker(m)
            px = float(ticker.trade_price)
            spread = px * 0.0008
            bid = px - spread / 2
            ask = px + spread / 2
            unit = {
                "bid_price": bid,
                "ask_price": ask,
                "bid_size": 12.5,
                "ask_size": 8.0,
            }
            return {"market": m, "orderbook_units": [unit] * n}
        rows = self._request("GET", "/orderbook", params={"markets": m})
        if not isinstance(rows, list) or not rows:
            raise UpbitBrokerError(f"orderbook not found: {m}")
        row = rows[0]
        if not isinstance(row, dict):
            raise UpbitBrokerError(f"unexpected orderbook: {row!r}")
        units = row.get("orderbook_units") or []
        row["orderbook_units"] = list(units)[:n]
        return row

    def validate_limit_buy(
        self,
        *,
        market: str,
        krw_amount: float,
        price: float,
    ) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if krw_amount < _POLICY_MIN_ORDER_KRW:
            errors.append(f"주문금액은 최소 {_POLICY_MIN_ORDER_KRW:,.0f}원 이상이어야 합니다.")
        if price <= 0:
            errors.append("지정가는 0보다 커야 합니다.")
        if not market.upper().startswith("KRW-"):
            errors.append(f"KRW 마켓만 지원합니다: {market}")
        volume = krw_amount / price if price > 0 else 0
        if volume <= 0:
            errors.append("주문 수량을 계산할 수 없습니다.")
        krw_avail = self.get_krw_available()
        if krw_amount > krw_avail:
            errors.append(f"KRW 잔고 부족: 필요 {krw_amount:,.0f} / 가용 {krw_avail:,.0f}")
        return (len(errors) == 0, errors)

    def validate_limit_sell(
        self,
        *,
        market: str,
        volume: float,
        price: float,
    ) -> tuple[bool, list[str]]:
        errors: list[str] = []
        m = market.strip().upper()
        if not m.startswith("KRW-"):
            errors.append(f"KRW 마켓만 지원합니다: {market}")
        if price <= 0:
            errors.append("지정가는 0보다 커야 합니다.")
        if volume <= 0:
            errors.append("매도 수량은 0보다 커야 합니다.")
        krw_value = volume * price
        if krw_value < _POLICY_MIN_ORDER_KRW:
            errors.append(f"주문금액은 최소 {_POLICY_MIN_ORDER_KRW:,.0f}원 이상이어야 합니다.")
        currency = m.split("-", 1)[-1]
        for bal in self.get_balances():
            if bal.currency.upper() == currency:
                available = max(bal.balance, 0.0)
                if volume > available + 1e-12:
                    errors.append(f"보유수량 초과: 매도 {volume} > 가용 {available}")
                break
        else:
            errors.append(f"보유 코인 없음: {currency}")
        return (len(errors) == 0, errors)

    def place_limit_sell(
        self,
        *,
        market: str,
        volume: float,
        price: float | None = None,
        execute: bool = False,
    ) -> UpbitOrderResult:
        m = market.strip().upper()
        ticker = self.get_ticker(m)
        limit_price = float(price if price is not None else ticker.trade_price)
        vol = float(volume)
        ok, errs = self.validate_limit_sell(market=m, volume=vol, price=limit_price)
        if not ok:
            raise UpbitBrokerError("; ".join(errs))

        body = {
            "market": m,
            "side": "ask",
            "volume": f"{vol:.8f}".rstrip("0").rstrip("."),
            "price": (str(int(limit_price)) if float(limit_price).is_integer() else f"{limit_price:.8f}".rstrip("0").rstrip(".")),
            "ord_type": "limit",
        }
        krw_amount = vol * limit_price

        if self._orders_blocked(execute=execute):
            return UpbitOrderResult(
                market=m,
                side="ask",
                order_type="limit",
                price=limit_price,
                volume=vol,
                krw_amount=krw_amount,
                status=self._dry_run_order_status(),
                uuid=None,
                dry_run=True,
                raw={"body": body, "execute": execute, "paper_mode": self._config.paper_mode},
            )

        raw = self._request("POST", "/orders", json_body=body)
        if not isinstance(raw, dict):
            raise UpbitBrokerError(f"unexpected order response: {raw!r}")
        return UpbitOrderResult(
            market=m,
            side="ask",
            order_type="limit",
            price=limit_price,
            volume=vol,
            krw_amount=krw_amount,
            status=str(raw.get("state", "submitted")),
            uuid=str(raw.get("uuid", "") or "") or None,
            dry_run=False,
            raw=raw,
        )

    def sell_limit(
        self,
        market: str,
        volume: float,
        price: float,
        *,
        execute: bool = False,
    ) -> UpbitOrderResult:
        return self.place_limit_sell(market=market, volume=volume, price=price, execute=execute)

    def place_limit_buy(
        self,
        *,
        market: str,
        krw_amount: float,
        price: float | None = None,
        execute: bool = False,
    ) -> UpbitOrderResult:
        m = market.strip().upper()
        ticker = self.get_ticker(m)
        limit_price = float(price if price is not None else ticker.trade_price)
        ok, errs = self.validate_limit_buy(market=m, krw_amount=krw_amount, price=limit_price)
        if not ok:
            raise UpbitBrokerError("; ".join(errs))

        volume = round(krw_amount / limit_price, 8)
        body = {
            "market": m,
            "side": "bid",
            "volume": f"{volume:.8f}".rstrip("0").rstrip("."),
            "price": (str(int(limit_price)) if float(limit_price).is_integer() else f"{limit_price:.8f}".rstrip("0").rstrip(".")),
            "ord_type": "limit",
        }

        if self._orders_blocked(execute=execute):
            return UpbitOrderResult(
                market=m,
                side="bid",
                order_type="limit",
                price=limit_price,
                volume=volume,
                krw_amount=krw_amount,
                status=self._dry_run_order_status(),
                uuid=None,
                dry_run=True,
                raw={"body": body, "execute": execute, "paper_mode": self._config.paper_mode},
            )

        raw = self._request("POST", "/orders", json_body=body)
        if not isinstance(raw, dict):
            raise UpbitBrokerError(f"unexpected order response: {raw!r}")
        return UpbitOrderResult(
            market=m,
            side="bid",
            order_type="limit",
            price=limit_price,
            volume=volume,
            krw_amount=krw_amount,
            status=str(raw.get("state", "submitted")),
            uuid=str(raw.get("uuid", "") or "") or None,
            dry_run=False,
            raw=raw,
        )

    def mock_order_status(self, uuid: str, *, state: str = "done") -> dict[str, Any]:
        """Dry-run order lookup."""
        return {
            "uuid": uuid,
            "market": "KRW-XRP",
            "side": "bid",
            "ord_type": "limit",
            "price": "2003",
            "state": state,
            "volume": "4.99251123",
            "remaining_volume": "0" if state == "done" else "2.0",
            "executed_volume": "4.99251123" if state in ("done", "wait") else "0",
            "paid_fee": "4.99",
            "remaining_fee": "0",
            "trades_count": 1 if state == "done" else 0,
        }

    def get_order(self, uuid: str) -> dict[str, Any]:
        """GET /v1/order?uuid=... — JWT query_hash on uuid param."""
        uid = (uuid or "").strip()
        if not uid:
            raise UpbitBrokerError("uuid is required for get_order")
        if self._config.dry_run and self._config.is_demo:
            # simulate: uuid ending with 'w' -> wait, 'c' -> cancel, else done
            if uid.endswith("-wait"):
                return self.mock_order_status(uid, state="wait")
            if uid.endswith("-cancel"):
                return self.mock_order_status(uid, state="cancel")
            if uid.endswith("-partial"):
                row = self.mock_order_status(uid, state="wait")
                row["executed_volume"] = "2.5"
                row["remaining_volume"] = "2.49251123"
                return row
            return self.mock_order_status(uid, state="done")
        raw = self._request("GET", "/order", params={"uuid": uid})
        if not isinstance(raw, dict):
            raise UpbitBrokerError(f"unexpected order response: {raw!r}")
        return raw

    def cancel_order(self, uuid: str) -> dict[str, Any]:
        """DELETE /v1/order — cancel open limit order by uuid."""
        uid = (uuid or "").strip()
        if not uid:
            raise UpbitBrokerError("uuid is required for cancel_order")
        if self._config.paper_mode or (
            self._config.dry_run and self._config.is_demo
        ):
            return self.mock_order_status(uid, state="cancel")
        raw = self._request("DELETE", "/order", params={"uuid": uid})
        if not isinstance(raw, dict):
            raise UpbitBrokerError(f"unexpected cancel response: {raw!r}")
        return raw

    def get_open_orders(self, market: str | None = None) -> list[dict[str, Any]]:
        """GET /v1/orders?state=wait — all unfilled limit orders."""
        if self._config.dry_run and self._config.is_demo:
            return []
        params: dict[str, Any] = {"state": "wait", "order_by": "desc", "limit": 100}
        if market:
            params["market"] = market.strip().upper()
        try:
            raw = self._request("GET", "/orders", params=params)
            return raw if isinstance(raw, list) else []
        except Exception:
            return []
