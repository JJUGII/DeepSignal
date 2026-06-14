"""Bithumb Open API v2 adapter — balance, ticker, candles, limit orders."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Sequence
from urllib.parse import urlencode

import requests

from deepsignal.crypto_trading.broker.bithumb.auth import authorization_header
from deepsignal.crypto_trading.broker.bithumb.config import BithumbConfig
from deepsignal.crypto_trading.broker.interface import (
    CryptoBalance,
    CryptoHolding,
    CryptoOrderResult,
    CryptoTicker,
)
from deepsignal.crypto_trading.broker.symbols import normalize_market
from deepsignal.crypto_trading.crypto_market_data import mock_daily_candles, mock_ticker, mock_tickers
from deepsignal.crypto_trading.crypto_paper_mode import orders_blocked_by_paper_or_dry_run
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

BITHUMB_API_HOST = "https://api.bithumb.com"
BITHUMB_API_V1 = f"{BITHUMB_API_HOST}/v1"
BITHUMB_API_V2 = f"{BITHUMB_API_HOST}/v2"
BITHUMB_RETRY_STATUS_CODES = frozenset({429, 503})
BITHUMB_RETRY_DELAYS_SEC = (0.5, 1.0, 2.0)
BITHUMB_MAX_RETRIES = 3
MIN_ORDER_KRW = 5_000
_POLICY_MIN_ORDER_KRW = max(
    MIN_ORDER_KRW,
    float(DEFAULT_ANALYSIS_CONDITIONS.cost.min_order_value_krw),
)

BithumbOrderResult = CryptoOrderResult

class BithumbBrokerError(ValueError):
    pass


class BithumbBroker:
    """Bithumb v2 REST. Internal markets use KRW-BTC (same as Upbit)."""

    def __init__(
        self,
        config: BithumbConfig,
        *,
        session: requests.Session | None = None,
        request_fn: Callable[..., requests.Response] | None = None,
    ) -> None:
        self._config = config
        self._session = session or requests.Session()
        self._request_fn = request_fn

    @property
    def config(self) -> BithumbConfig:
        return self._config

    @property
    def exchange_id(self) -> str:
        return "bithumb"

    @staticmethod
    def currency_to_market(currency: str) -> str:
        cur = currency.strip().upper()
        if cur == "KRW":
            return "KRW-KRW"
        return f"KRW-{cur}"

    @staticmethod
    def _parse_amount(value: Any) -> float:
        if value is None or value == "":
            return 0.0
        if isinstance(value, str):
            value = value.replace(",", "")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _raise_api_error(status_code: int, body: Any) -> None:
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            err = body["error"]
            name = err.get("name") or "error"
            message = err.get("message") or body
            raise BithumbBrokerError(f"HTTP {status_code}: {name}: {message}")
        if isinstance(body, dict) and body.get("status"):
            raise BithumbBrokerError(
                f"HTTP {status_code}: Bithumb API status {body.get('status')}: {body.get('message', body)}"
            )
        raise BithumbBrokerError(f"HTTP {status_code}: {body!r}")

    @staticmethod
    def _format_decimal(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.8f}".rstrip("0").rstrip(".")

    def _orders_blocked(self, *, execute: bool) -> bool:
        return orders_blocked_by_paper_or_dry_run(
            dry_run=self._config.dry_run,
            paper_mode=self._config.paper_mode,
            execute=execute,
        )

    def _dry_run_order_status(self) -> str:
        if self._config.paper_mode:
            return "CRYPTO_PAPER_MODE_BLOCKED"
        return "BITHUMB_DRY_RUN_BLOCKED"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        private: bool = False,
        base_url: str = BITHUMB_API_V1,
    ) -> Any:
        if path.rstrip("/") in ("/orders", "/v2/orders") and method.upper() == "POST":
            if self._config.paper_mode:
                raise BithumbBrokerError("CRYPTO_PAPER_MODE blocks POST /v2/orders")
            if self._config.dry_run:
                raise BithumbBrokerError("dry_run broker cannot POST /v2/orders without execute=True")
        if path.rstrip("/").endswith("/order") and method.upper() == "DELETE" and self._config.paper_mode:
            raise BithumbBrokerError("CRYPTO_PAPER_MODE blocks DELETE /v2/order (cancel)")

        query = dict(params or {})
        url = f"{base_url}{path}"
        jwt_query: dict[str, Any] | None = None
        if method.upper() == "GET" or method.upper() == "DELETE":
            if query:
                url = f"{url}?{urlencode(query)}"
            jwt_query = query if query else None
        elif json_body is not None:
            jwt_query = dict(json_body)

        headers: dict[str, str] = {"Accept": "application/json"}
        if private:
            headers.update(
                authorization_header(
                    self._config.api_key,
                    self._config.secret_key,
                    query=jwt_query,
                )
            )
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        last_resp: requests.Response | None = None
        for attempt in range(BITHUMB_MAX_RETRIES):
            if self._request_fn is not None:
                if json_body is not None:
                    last_resp = self._request_fn(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                    )
                else:
                    last_resp = self._request_fn(method, url, headers=headers)
            elif method.upper() == "GET":
                last_resp = self._session.get(url, headers=headers, timeout=15)
            elif method.upper() == "DELETE":
                last_resp = self._session.delete(url, headers=headers, timeout=15)
            elif json_body is not None:
                last_resp = self._session.post(url, headers=headers, json=json_body, timeout=15)
            else:
                last_resp = self._session.request(method, url, headers=headers, timeout=15)
            if last_resp.status_code not in BITHUMB_RETRY_STATUS_CODES:
                break
            if attempt < BITHUMB_MAX_RETRIES - 1:
                time.sleep(BITHUMB_RETRY_DELAYS_SEC[min(attempt, len(BITHUMB_RETRY_DELAYS_SEC) - 1)])
        assert last_resp is not None

        text = last_resp.text or ""
        try:
            body = last_resp.json() if text else None
        except json.JSONDecodeError as exc:
            raise BithumbBrokerError(f"invalid JSON: {exc}; body={text[:300]}") from exc

        if last_resp.status_code >= 400:
            self._raise_api_error(last_resp.status_code, body)
        return body

    def check_connection(self) -> bool:
        if self._config.dry_run and self._config.is_demo:
            return True
        self.get_balances()
        return True

    def get_balances(self) -> list[CryptoBalance]:
        if self._config.dry_run and self._config.is_demo:
            return [
                CryptoBalance(currency="KRW", balance=100_000.0, locked=0.0, avg_buy_price=0.0),
                CryptoBalance(currency="BTC", balance=0.0, locked=0.0, avg_buy_price=0.0),
            ]
        rows = self._request("GET", "/accounts", private=True)
        if not isinstance(rows, list):
            raise BithumbBrokerError(f"unexpected accounts response: {rows!r}")

        out: list[CryptoBalance] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cur = str(row.get("currency") or "").strip().upper()
            if not cur:
                continue
            out.append(
                CryptoBalance(
                    currency=cur,
                    balance=self._parse_amount(row.get("balance")),
                    locked=self._parse_amount(row.get("locked")),
                    avg_buy_price=self._parse_amount(row.get("avg_buy_price")),
                )
            )
        return out

    def get_krw_available(self) -> float:
        for b in self.get_balances():
            if b.currency.upper() == "KRW":
                return max(b.balance, 0.0)
        return 0.0

    def get_crypto_holdings(self) -> list[CryptoHolding]:
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

    def _parse_ticker_row(self, row: dict[str, Any]) -> CryptoTicker:
        m = normalize_market(str(row.get("market") or ""))
        return CryptoTicker(
            market=m,
            trade_price=self._parse_amount(row.get("trade_price")),
            signed_change_rate=self._parse_amount(row.get("signed_change_rate")),
            acc_trade_price_24h=self._parse_amount(row.get("acc_trade_price_24h")),
        )

    def get_ticker(self, market: str) -> CryptoTicker:
        m = normalize_market(market)
        if self._config.dry_run and self._config.is_demo:
            return mock_ticker(m)
        rows = self._request("GET", "/ticker", params={"markets": m})
        if not isinstance(rows, list) or not rows:
            raise BithumbBrokerError(f"ticker not found: {m}")
        first = rows[0]
        if not isinstance(first, dict):
            raise BithumbBrokerError(f"ticker not found: {m}")
        return self._parse_ticker_row(first)

    def get_tickers(self, markets: Sequence[str]) -> dict[str, CryptoTicker]:
        normalized = [normalize_market(m) for m in markets if m and str(m).strip()]
        if not normalized:
            return {}
        if self._config.dry_run and self._config.is_demo:
            return mock_tickers(tuple(normalized))
        rows = self._request("GET", "/ticker", params={"markets": ",".join(normalized)})
        if not isinstance(rows, list):
            return {}
        out: dict[str, CryptoTicker] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                parsed = self._parse_ticker_row(row)
                out[parsed.market] = parsed
            except Exception:
                continue
        return out

    def get_market_all(self, *, is_details: bool = False) -> list[dict[str, Any]]:
        rows = self._request("GET", "/market/all", params={"isDetails": str(is_details).lower()})
        return rows if isinstance(rows, list) else []

    def get_orderbook(self, market: str, *, levels: int = 5) -> dict[str, Any]:
        m = normalize_market(market)
        if self._config.dry_run and self._config.is_demo:
            return {"market": m, "orderbook_units": []}
        rows = self._request("GET", "/orderbook", params={"markets": m})
        if isinstance(rows, list) and rows:
            first = rows[0]
            return first if isinstance(first, dict) else {"market": m, "orderbook_units": []}
        return {"market": m, "orderbook_units": []}

    def get_minute_candles(self, market: str, unit: int, count: int) -> list[dict[str, Any]]:
        m = normalize_market(market)
        n = max(2, min(int(count), 200))
        u = max(1, min(int(unit), 240))
        if self._config.dry_run and self._config.is_demo:
            return []
        rows = self._request("GET", f"/candles/minutes/{u}", params={"market": m, "count": n})
        return rows if isinstance(rows, list) else []

    def get_daily_candles(self, market: str, *, count: int = 20) -> list[dict[str, Any]]:
        m = normalize_market(market)
        n = max(2, min(int(count), 200))
        if self._config.dry_run and self._config.is_demo:
            return mock_daily_candles(m, count=n)
        rows = self._request("GET", "/candles/days", params={"market": m, "count": n})
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            kst = str(row.get("candle_date_time_kst") or "")
            date_kst = kst[:10] if kst else ""
            out.append(
                {
                    "candle_date_time_kst": date_kst,
                    "trade_price": self._parse_amount(row.get("trade_price")),
                    "opening_price": self._parse_amount(row.get("opening_price")),
                    "high_price": self._parse_amount(row.get("high_price")),
                    "low_price": self._parse_amount(row.get("low_price")),
                    "candle_acc_trade_volume": self._parse_amount(row.get("candle_acc_trade_volume")),
                }
            )
        return out

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

    @staticmethod
    def _order_id_from_response(raw: dict[str, Any]) -> str | None:
        oid = str(raw.get("order_id") or raw.get("uuid") or "").strip()
        return oid or None

    def place_limit_buy(
        self,
        *,
        market: str,
        krw_amount: float,
        price: float | None = None,
        execute: bool = False,
    ) -> BithumbOrderResult:
        m = normalize_market(market)
        ticker = self.get_ticker(m)
        limit_price = float(price if price is not None else ticker.trade_price)
        ok, errs = self.validate_limit_buy(market=m, krw_amount=krw_amount, price=limit_price)
        if not ok:
            raise BithumbBrokerError("; ".join(errs))

        volume = round(krw_amount / limit_price, 8)
        body = {
            "market": m,
            "side": "bid",
            "order_type": "limit",
            "price": self._format_decimal(limit_price),
            "volume": self._format_decimal(volume),
        }

        if self._orders_blocked(execute=execute):
            return BithumbOrderResult(
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

        raw = self._request(
            "POST",
            "/orders",
            json_body=body,
            private=True,
            base_url=BITHUMB_API_V2,
        )
        if not isinstance(raw, dict):
            raise BithumbBrokerError(f"unexpected order response: {raw!r}")
        return BithumbOrderResult(
            market=m,
            side="bid",
            order_type="limit",
            price=limit_price,
            volume=volume,
            krw_amount=krw_amount,
            status=str(raw.get("state", "submitted")),
            uuid=self._order_id_from_response(raw),
            dry_run=False,
            raw=raw,
        )

    def place_limit_sell(
        self,
        *,
        market: str,
        volume: float,
        price: float | None = None,
        execute: bool = False,
    ) -> BithumbOrderResult:
        m = normalize_market(market)
        ticker = self.get_ticker(m)
        limit_price = float(price if price is not None else ticker.trade_price)
        vol = float(volume)
        ok, errs = self.validate_limit_sell(market=m, volume=vol, price=limit_price)
        if not ok:
            raise BithumbBrokerError("; ".join(errs))

        body = {
            "market": m,
            "side": "ask",
            "order_type": "limit",
            "price": self._format_decimal(limit_price),
            "volume": self._format_decimal(vol),
        }
        krw_amount = vol * limit_price

        if self._orders_blocked(execute=execute):
            return BithumbOrderResult(
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

        raw = self._request(
            "POST",
            "/orders",
            json_body=body,
            private=True,
            base_url=BITHUMB_API_V2,
        )
        if not isinstance(raw, dict):
            raise BithumbBrokerError(f"unexpected order response: {raw!r}")
        return BithumbOrderResult(
            market=m,
            side="ask",
            order_type="limit",
            price=limit_price,
            volume=vol,
            krw_amount=krw_amount,
            status=str(raw.get("state", "submitted")),
            uuid=self._order_id_from_response(raw),
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
    ) -> BithumbOrderResult:
        return self.place_limit_sell(market=market, volume=volume, price=price, execute=execute)

    def mock_order_status(self, order_id: str, *, state: str = "done") -> dict[str, Any]:
        return {
            "uuid": order_id,
            "order_id": order_id,
            "market": "KRW-BTC",
            "side": "bid",
            "order_type": "limit",
            "price": "80000000",
            "state": state,
            "volume": "0.001",
            "remaining_volume": "0" if state == "done" else "0.0005",
            "executed_volume": "0.001" if state in ("done", "wait") else "0",
            "paid_fee": "40",
            "trades_count": 1 if state == "done" else 0,
        }

    def get_order(self, uuid: str) -> dict[str, Any]:
        order_id = (uuid or "").strip()
        if not order_id:
            raise BithumbBrokerError("order_id is required for get_order")
        if self._config.dry_run and self._config.is_demo:
            if order_id.endswith("-wait"):
                return self.mock_order_status(order_id, state="wait")
            if order_id.endswith("-cancel"):
                return self.mock_order_status(order_id, state="cancel")
            return self.mock_order_status(order_id, state="done")
        raw = self._request(
            "GET",
            "/order",
            params={"uuid": order_id},
            private=True,
        )
        if not isinstance(raw, dict):
            raise BithumbBrokerError(f"unexpected order response: {raw!r}")
        return raw

    def cancel_order(self, uuid: str) -> dict[str, Any]:
        order_id = (uuid or "").strip()
        if not order_id:
            raise BithumbBrokerError("order_id is required for cancel_order")
        if self._config.paper_mode or (self._config.dry_run and self._config.is_demo):
            return self.mock_order_status(order_id, state="cancel")
        raw = self._request(
            "DELETE",
            "/order",
            params={"order_id": order_id},
            private=True,
            base_url=BITHUMB_API_V2,
        )
        if not isinstance(raw, dict):
            raise BithumbBrokerError(f"unexpected cancel response: {raw!r}")
        return raw

    def get_open_orders(self, market: str | None = None) -> list[dict[str, Any]]:
        if self._config.dry_run and self._config.is_demo:
            return []
        params: dict[str, Any] = {"state": "wait", "order_by": "desc", "limit": 100}
        if market:
            params["market"] = normalize_market(market)
        try:
            raw = self._request("GET", "/orders", params=params, private=True)
            return raw if isinstance(raw, list) else []
        except Exception:
            return []
