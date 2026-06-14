"""한국투자증권 KIS Open API 브로커 어댑터 (OAuth·국내 현금 LIMIT BUY·[실전-4] 선택 실주문)."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import requests

from deepsignal.live_trading.broker_interface import (
    BrokerCashBalance,
    BrokerInterface,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerOrderStatus,
    BrokerPosition,
)
from deepsignal.live_trading.kis_config import KISConfig
from deepsignal.live_trading.kis_token_cache import (
    get_default_token_cache_path,
    load_cached_token,
    save_cached_token,
)


class BrokerError(ValueError):
    """주문·페이로드 관련 오류."""


def post_kis_order_cash_request(
    session: requests.Session,
    base_url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
) -> requests.Response:
    """
    국내주식 **현금주문** API HTTP POST.

    경로·TR 분기: KIS Developers / `koreainvestment/open-trading-api` 국내주식 현금주문
    (`/uapi/domestic-stock/v1/trading/order-cash`). 실전·모의는 `KISConfig.env`·`tr_id`로 구분한다.
    """
    url = f"{base_url.rstrip('/')}/uapi/domestic-stock/v1/trading/order-cash"
    merged = {"content-type": "application/json; charset=utf-8"}
    merged.update(headers)
    return session.post(url, headers=merged, data=json.dumps(body), timeout=60)


def kis_get_request(
    session: requests.Session,
    base_url: str,
    path: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any],
) -> requests.Response:
    """KIS Open API GET (조회 전용). 경로는 공식 domestic-stock v1."""
    url = f"{base_url.rstrip('/')}{path}"
    merged = {"content-type": "application/json; charset=utf-8"}
    merged.update(headers)
    return session.get(url, headers=merged, params=params, timeout=60)


class KISBroker(BrokerInterface):
    """KIS OAuth·주문. `safe_mode=True` 또는 `execute=False` 이면 주문 POST 금지(가드 외부에서 `execute` 제어)."""

    def __init__(
        self,
        config: KISConfig,
        *,
        safe_mode: bool = True,
        session: requests.Session | None = None,
        token_cache_path: str | Path | None = None,
    ) -> None:
        self._config = config
        self._safe_mode = bool(safe_mode)
        self._session = session if session is not None else requests.Session()
        self._token_cache_path = Path(token_cache_path) if token_cache_path is not None else get_default_token_cache_path()
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0
        self._last_balance_response_body: dict[str, Any] | None = None

    @property
    def config(self) -> KISConfig:
        return self._config

    @property
    def safe_mode(self) -> bool:
        return self._safe_mode

    @property
    def last_balance_response_body(self) -> dict[str, Any] | None:
        return self._last_balance_response_body

    def connect(self) -> None:
        self.get_access_token()

    def submit_order(self, order: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "status": "KIS_LEGACY_SUBMIT_NOT_USED",
            "message": "Use place_order(BrokerOrderRequest).",
            "order": dict(order),
        }

    def get_positions(self) -> list[BrokerPosition]:
        """국내주식 잔고조회 (`/uapi/domestic-stock/v1/trading/inquire-balance`). TR: 실전 TTTC8434R / 모의 VTTC8434R."""
        tr = "VTTC8434R" if not self._config.is_live else "TTTC8434R"
        cano = self._config.account_no.strip()
        acnt = self._config.account_product_code.strip()
        params: dict[str, Any] = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        body, _hdr = self._kis_get_json(tr, path, params)
        raw: dict[str, Any] = {"tr_id": tr, "endpoint": path}
        self._last_balance_response_body = body if isinstance(body, dict) else {"_non_json_body": str(body)}
        raw["response_body"] = body
        if not isinstance(body, dict):
            return []
        out1 = body.get("output1") or body.get("Output1")
        rows: list[Any]
        if isinstance(out1, list):
            rows = out1
        elif isinstance(out1, dict):
            rows = [out1]
        else:
            rows = []
        positions: list[BrokerPosition] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = self._pick_str(row, ("pdno", "PDNO"))
            if not sym or not re.fullmatch(r"\d{6}", str(sym).strip()):
                continue
            qty = self._pick_int(row, ("hldg_qty", "HLDG_QTY", "hltg_qty"))
            if qty is None:
                qty = self._pick_int(row, ("ord_psbl_qty", "ORD_PSBL_QTY"))
            if qty is None or int(qty) <= 0:
                continue
            if not sym:
                continue
            positions.append(
                BrokerPosition(
                    symbol=str(sym).zfill(6) if str(sym).isdigit() else str(sym),
                    quantity=int(qty or 0),
                    avg_price=self._pick_float(row, ("pchs_avg_pric", "PCHS_AVG_PRIC", "avg_prc")),
                    current_price=self._pick_float(row, ("prpr", "PRPR", "stck_prpr")),
                    market_value=self._pick_float(row, ("evlu_amt", "EVLU_AMT", "evlu_pfls_amt")),
                    raw=dict(row),
                )
            )
        return positions

    def get_cash_balance(self) -> BrokerCashBalance:
        """잔고조회 응답 `output2`에서 현금 요약 (필드명은 버전별 상이할 수 있음)."""
        tr = "VTTC8434R" if not self._config.is_live else "TTTC8434R"
        cano = self._config.account_no.strip()
        acnt = self._config.account_product_code.strip()
        params: dict[str, Any] = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        body, _hdr = self._kis_get_json(tr, path, params)
        raw: dict[str, Any] = {"tr_id": tr}
        self._last_balance_response_body = body if isinstance(body, dict) else {"_non_json_body": str(body)}
        raw["response_body"] = body
        if isinstance(body, dict) and str(body.get("rt_cd", "")).strip() != "0":
            raw["kis_warning"] = str(body.get("msg1") or body.get("msg_cd") or "rt_cd not 0")
        out2 = body.get("output2") if isinstance(body, dict) else None
        row: dict[str, Any] = out2[0] if isinstance(out2, list) and out2 and isinstance(out2[0], dict) else (
            out2 if isinstance(out2, dict) else {}
        )
        cash = self._pick_float(row, ("dnca_tot_amt", "DNCa_TOT_AMT", "tot_crdl_loan_amt", "nass_amt"))
        wdr = self._pick_float(row, ("ord_psbl_cash", "ORD_PSBL_CASH", "nxdy_excc_amt"))
        return BrokerCashBalance(
            cash=cash,
            withdrawable_cash=wdr if wdr is not None else cash,
            raw=raw,
        )

    def get_current_price(self, symbol: str) -> float | None:
        """국내주식 실시간 현재가 조회.

        `/uapi/domestic-stock/v1/quotations/inquire-price`, TR: FHKST01010100.
        6자리 숫자 종목코드만 지원(미국 티커 등은 None). 실패·파싱불가 시 None.
        실주문 LIMIT 가격을 일봉 종가 대신 실시간 시세와 대조하는 데 쓴다(#5).
        """
        code = str(symbol).strip()
        if not re.fullmatch(r"\d{6}", code):
            return None
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        hdr = self._inquire_headers("FHKST01010100")
        path = "/uapi/domestic-stock/v1/quotations/inquire-price"
        try:
            resp = kis_get_request(
                self._session, self._config.base_url, path, headers=hdr, params=params
            )
            body = resp.json()
        except (requests.RequestException, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(body, dict):
            return None
        out = body.get("output") or body.get("Output")
        row = out if isinstance(out, dict) else {}
        px = self._pick_float(row, ("stck_prpr", "STCK_PRPR"))
        if px is None or px <= 0:
            return None
        return float(px)

    def get_order_status(
        self,
        *,
        order_id: str | None = None,
        symbol: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[BrokerOrderStatus]:
        """
        국내주식 일별주문체결조회 (`/uapi/domestic-stock/v1/trading/inquire-daily-ccld`).
        TR: 실전 TTTC0081R / 모의 VTTC0081R. `order_id` 있으면 응답 행에서 해당 ODNO 우선 매칭.
        """
        tr = "VTTC0081R" if not self._config.is_live else "TTTC0081R"
        cano = self._config.account_no.strip()
        acnt = self._config.account_product_code.strip()
        if not start_date or not end_date:
            from datetime import date

            d = date.today().strftime("%Y%m%d")
            start_date = start_date or d
            end_date = end_date or d
        pdno = (symbol or "").strip()
        params: dict[str, Any] = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt,
            "INQR_STRT_DT": start_date,
            "INQR_END_DT": end_date,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": pdno,
            "ORD_DVSN": "00",
            "CCLD_DVSN": "00",
            "INQR_DVSN_1": "",
            "INQR_DVSN_3": "",
            "EXC_CBL_CLS": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        if order_id:
            params["ODNO"] = str(order_id).strip()
        hdr = self._inquire_headers(tr)
        path = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        resp = kis_get_request(self._session, self._config.base_url, path, headers=hdr, params=params)
        raw_base: dict[str, Any] = {"http_status": resp.status_code, "tr_id": tr, "endpoint": path}
        try:
            body = resp.json()
        except json.JSONDecodeError:
            body = {"_non_json_body": resp.text}
        raw_base["response_body"] = body
        if not isinstance(body, dict):
            return [
                BrokerOrderStatus(
                    order_id=order_id,
                    symbol=pdno or "",
                    side=None,
                    quantity=None,
                    filled_quantity=None,
                    remaining_quantity=None,
                    order_price=None,
                    avg_fill_price=None,
                    status="KIS_PARSE_ERROR",
                    message="response is not a JSON object",
                    raw=raw_base,
                )
            ]
        if str(body.get("rt_cd", "")).strip() != "0":
            return [
                BrokerOrderStatus(
                    order_id=order_id,
                    symbol=pdno or "",
                    side=None,
                    quantity=None,
                    filled_quantity=None,
                    remaining_quantity=None,
                    order_price=None,
                    avg_fill_price=None,
                    status="KIS_QUERY_ERROR",
                    message=str(body.get("msg1") or body.get("msg_cd") or "rt_cd not 0"),
                    raw=raw_base,
                )
            ]
        out1 = body.get("output1") or body.get("Output1")
        rows: list[Any] = out1 if isinstance(out1, list) else ([] if out1 is None else [out1])
        statuses: list[BrokerOrderStatus] = []
        oid_needle = str(order_id).strip() if order_id else None
        for row in rows:
            if not isinstance(row, dict):
                continue
            roid = self._pick_str(row, ("odno", "ODNO", "ord_no"))
            if oid_needle and roid and str(roid).strip() != oid_needle:
                continue
            sym = self._pick_str(row, ("pdno", "PDNO")) or (pdno or "")
            oqty = self._pick_int(row, ("ord_qty", "ORD_QTY", "ord_qty"))
            fq = self._pick_int(row, ("tot_ccld_qty", "TOT_CCLD_QTY", "ccld_qty"))
            rq = self._pick_int(row, ("rmn_qty", "RMN_QTY", "rmnd_qty", "RMND_QTY", "nccs_qty"))
            if rq is None and oqty is not None and fq is not None:
                rq = max(0, oqty - fq)
            op = self._pick_float(row, ("ord_unpr", "ORD_UNPR", "ord_unpr"))
            ap = self._pick_float(row, ("avg_prvs", "AVG_PRVS", "avg_ccld_unpr"))
            side_code = self._pick_str(row, ("sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD"))
            side = "BUY" if side_code in ("02", "2") else ("SELL" if side_code in ("01", "1") else side_code)
            st, msg = self._infer_order_row_status(row, oqty, fq)
            one_raw = {**raw_base, "matched_row": dict(row)}
            statuses.append(
                BrokerOrderStatus(
                    order_id=str(roid) if roid else order_id,
                    symbol=str(sym).zfill(6) if sym and str(sym).isdigit() else (sym or ""),
                    side=side,
                    quantity=oqty,
                    filled_quantity=fq,
                    remaining_quantity=rq,
                    order_price=op,
                    avg_fill_price=ap,
                    status=st,
                    message=msg,
                    raw=one_raw,
                )
            )
        if not statuses and order_id:
            statuses.append(
                BrokerOrderStatus(
                    order_id=order_id,
                    symbol=pdno or "",
                    side=None,
                    quantity=None,
                    filled_quantity=None,
                    remaining_quantity=None,
                    order_price=None,
                    avg_fill_price=None,
                    status="NOT_FOUND",
                    message="no matching rows in inquire-daily-ccld output1",
                    raw=raw_base,
                )
            )
        return statuses

    def _inquire_headers(self, tr_id: str) -> dict[str, str]:
        tok = self.get_access_token()
        return {
            "authorization": f"Bearer {tok}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    @staticmethod
    def _is_rate_limited_body(body: Any) -> bool:
        """KIS 초당 거래건수 초과(EGW00201)인지 판별."""
        if not isinstance(body, dict):
            return False
        if str(body.get("msg_cd", "")).strip() == "EGW00201":
            return True
        text = f"{body.get('msg1', '')}{body.get('kis_warning', '')}"
        return "초당 거래건수" in text or "초과하였습니다" in text

    def _kis_get_json(self, tr_id: str, path: str, params: dict[str, Any]) -> tuple[Any, dict[str, str]]:
        """GET 조회 + 토큰만료/초당한도(EGW00123·EGW00201) 자동 재시도. (body, headers) 반환."""
        import logging as _logging
        import time as _t
        _log = _logging.getLogger(__name__)

        def _do() -> tuple[Any, dict[str, str]]:
            h = self._inquire_headers(tr_id)
            r = kis_get_request(self._session, self._config.base_url, path, headers=h, params=params)
            try:
                return r.json(), h
            except json.JSONDecodeError:
                return {"_non_json_body": r.text}, h

        body, hdr = _do()
        # 1) 토큰 만료/무효 → 새 토큰 발급 후 재시도
        if self._is_expired_token_body(body):
            _log.warning("KIS 토큰 만료 감지 — 재발급 후 재시도 (%s)", tr_id)
            self.get_access_token(force=True)
            body, hdr = _do()
        # 2) 초당 거래건수 초과 → 짧게 대기하며 최대 3회 재시도
        for attempt in range(3):
            if not self._is_rate_limited_body(body):
                break
            _log.warning("KIS 초당 한도 초과(EGW00201) — %.1f초 후 재시도 (%s)", 0.7 * (attempt + 1), tr_id)
            _t.sleep(0.7 * (attempt + 1))
            body, hdr = _do()
        return body, hdr

    @staticmethod
    def _pick_str(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        for k in keys:
            v = row.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return None

    @staticmethod
    def _pick_int(row: dict[str, Any], keys: tuple[str, ...]) -> int | None:
        for k in keys:
            v = row.get(k)
            if v is None or str(v).strip() == "":
                continue
            try:
                return int(float(str(v).replace(",", "")))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _pick_float(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for k in keys:
            v = row.get(k)
            if v is None or str(v).strip() == "":
                continue
            try:
                return float(str(v).replace(",", ""))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _infer_order_row_status(
        row: dict[str, Any],
        ord_qty: int | None,
        filled: int | None,
    ) -> tuple[str, str]:
        """체결/미체결 코드가 있으면 우선, 없으면 수량으로 추정."""
        for k in ("ord_stts", "ORD_STTS", "prcs_stat_name", "ccld_dvsn_name"):
            v = row.get(k)
            if v is not None and str(v).strip():
                return "REPORTED", str(v).strip()
        if ord_qty is not None and filled is not None:
            if filled >= ord_qty > 0:
                return "FILLED", "filled_qty >= ord_qty"
            if filled > 0:
                return "PARTIALLY_FILLED", "partial fill"
            return "OPEN_OR_UNKNOWN", "no fills in row"
        return "UNKNOWN", "could not infer status from row"

    def _tr_id_cash_buy(self) -> str:
        """모의 `VTTC0802U` / 실전 `TTTC0802U` (KIS 국내 현금 매수 TR)."""
        return "VTTC0802U" if not self._config.is_live else "TTTC0802U"

    def _tr_id_cash_sell(self) -> str:
        """모의 `VTTC0801U` / 실전 `TTTC0801U` (KIS 국내 현금 매도 TR)."""
        return "VTTC0801U" if not self._config.is_live else "TTTC0801U"

    @staticmethod
    def _is_expired_token_body(body: Any) -> bool:
        """KIS 응답이 토큰 만료(EGW00123)·무효(EGW00121) 등 토큰 문제인지 판별."""
        if not isinstance(body, dict):
            return False
        if str(body.get("msg_cd", "")).strip() in ("EGW00123", "EGW00121"):
            return True
        text = f"{body.get('msg1', '')}{body.get('kis_warning', '')}{body.get('error_description', '')}"
        low = text.lower()
        return ("만료된 token" in text or "유효하지 않은 token" in text
                or "expired" in low or "invalid" in low and "token" in low)

    def get_access_token(self, *, force: bool = False) -> str:
        now = time.monotonic()
        if not force and self._access_token and now < self._access_token_expires_at:
            return self._access_token

        # force=True 면 메모리·파일 캐시 무시하고 즉시 새 토큰 발급
        cached = None if force else load_cached_token(
            self._token_cache_path,
            env=self._config.env,
            app_key=self._config.app_key,
        )
        if cached is not None:
            self._access_token = cached.access_token
            try:
                expires_at = datetime.fromisoformat(cached.expires_at).astimezone(UTC)
                remaining = max(60.0, (expires_at - datetime.now(UTC)).total_seconds() - 30.0)
            except ValueError:
                remaining = 60.0
            self._access_token_expires_at = now + remaining
            return cached.access_token

        url = f"{self._config.base_url.rstrip('/')}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
        }
        headers = {"content-type": "application/json; charset=utf-8"}
        resp = self._session.post(url, headers=headers, data=json.dumps(body), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        if not token or not isinstance(token, str):
            raise BrokerError(f"KIS token response missing access_token: {data!r}")
        try:
            expires_in = int(data.get("expires_in", 86400))
        except (TypeError, ValueError):
            expires_in = 86400
        self._access_token = token
        self._access_token_expires_at = now + max(60, expires_in - 120)
        save_cached_token(
            self._token_cache_path,
            token,
            max(60, expires_in - 120),
            env=self._config.env,
            app_key=self._config.app_key,
            token_type=data.get("token_type") if isinstance(data.get("token_type"), str) else None,
        )
        return token

    def check_connection(self) -> bool:
        self.get_access_token()
        return True

    def build_order_payload(self, request: BrokerOrderRequest) -> dict[str, Any]:
        side = (request.side or "").strip().upper()
        if side not in ("BUY", "SELL"):
            raise BrokerError(f"KIS domestic cash path supports BUY/SELL only, got {request.side!r}")
        ot = (request.order_type or "").strip().upper()
        if ot != "LIMIT":
            raise BrokerError(f"only LIMIT orders supported, got {request.order_type!r}")
        if request.quantity is None or int(request.quantity) <= 0:
            raise BrokerError("quantity must be a positive integer")
        if request.limit_price is None:
            raise BrokerError("limit_price is required for LIMIT orders")
        lim = float(request.limit_price)
        if lim <= 0:
            raise BrokerError("limit_price must be > 0")

        sym = (request.symbol or "").strip()
        pdno = sym.zfill(6) if re.fullmatch(r"\d{1,6}", sym) else sym
        if not re.fullmatch(r"\d{6}", pdno):
            raise BrokerError(
                f"KIS domestic PDNO must be 6 digits, got symbol={request.symbol!r} → {pdno!r}"
            )

        cano = self._config.account_no.strip()
        acnt = self._config.account_product_code.strip()
        if len(cano) != 8 or not cano.isdigit():
            raise BrokerError("KIS_ACCOUNT_NO must be 8-digit CANO for order payload")
        if len(acnt) != 2 or not acnt.isdigit():
            raise BrokerError("KIS_ACCOUNT_PRODUCT_CODE must be 2-digit ACNT_PRDT_CD")

        return {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt,
            "PDNO": pdno,
            "ORD_DVSN": "00",
            "ORD_QTY": str(int(request.quantity)),
            "ORD_UNPR": str(int(round(lim))),
        }

    def _order_headers(self, side: str = "BUY") -> dict[str, str]:
        tok = self.get_access_token()
        tr_id = self._tr_id_cash_sell() if side.upper() == "SELL" else self._tr_id_cash_buy()
        return {
            "authorization": f"Bearer {tok}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def place_order(
        self,
        request: BrokerOrderRequest,
        *,
        execute: bool = False,
    ) -> BrokerOrderResult:
        try:
            payload = self.build_order_payload(request)
        except BrokerError as e:
            return BrokerOrderResult(
                symbol=request.symbol,
                side=request.side,
                quantity=int(request.quantity),
                order_type=request.order_type,
                status="KIS_ORDER_BUILD_FAILED",
                broker_order_id=None,
                submitted_price=request.limit_price,
                message=str(e),
                raw={
                    "error": str(e),
                    "실제_주문_없음": True,
                },
            )

        _side_upper = (request.side or "BUY").strip().upper()
        preview: dict[str, Any] = {
            "kis_payload": payload,
            "tr_id": self._tr_id_cash_sell() if _side_upper == "SELL" else self._tr_id_cash_buy(),
            "endpoint": "/uapi/domestic-stock/v1/trading/order-cash",
            "kis_env": self._config.env,
        }

        if self._safe_mode:
            preview["실제_주문_없음"] = True
            return BrokerOrderResult(
                symbol=request.symbol,
                side=request.side,
                quantity=int(request.quantity),
                order_type=request.order_type,
                status="KIS_SAFE_MODE_BLOCKED",
                broker_order_id=None,
                submitted_price=request.limit_price,
                message="safe_mode=True: no KIS order HTTP.",
                raw=preview,
            )

        if not execute:
            preview["note"] = "execute=False: order POST not sent (preview only)."
            preview["실제_주문_없음"] = True
            return BrokerOrderResult(
                symbol=request.symbol,
                side=request.side,
                quantity=int(request.quantity),
                order_type=request.order_type,
                status="KIS_ORDER_SEND_BLOCKED_PHASE3",
                broker_order_id=None,
                submitted_price=request.limit_price,
                message="execute=False: no HTTP order (guard or dry path).",
                raw=preview,
            )

        headers = self._order_headers(side=_side_upper)
        resp = post_kis_order_cash_request(
            self._session,
            self._config.base_url,
            headers=headers,
            body=payload,
        )
        raw_text = resp.text
        try:
            body_json: Any = resp.json()
        except json.JSONDecodeError:
            body_json = {"_non_json_body": raw_text}
        # 토큰 만료(EGW00123) 시 새 토큰으로 1회 재시도
        if self._is_expired_token_body(body_json):
            import logging
            logging.getLogger(__name__).warning("KIS 토큰 만료 감지 — 주문 재발급 후 재시도")
            self.get_access_token(force=True)
            headers = self._order_headers(side=_side_upper)
            resp = post_kis_order_cash_request(self._session, self._config.base_url, headers=headers, body=payload)
            raw_text = resp.text
            try:
                body_json = resp.json()
            except json.JSONDecodeError:
                body_json = {"_non_json_body": raw_text}

        oid: str | None = None
        if isinstance(body_json, dict):
            out = body_json.get("output") or body_json.get("Output")
            if isinstance(out, dict):
                oid = out.get("ODNO") or out.get("ORD_NO") or out.get("KRX_FWDG_ORD_ORGNO")
                if oid is not None:
                    oid = str(oid)

        ok_http = resp.status_code == 200
        rt_ok = isinstance(body_json, dict) and str(body_json.get("rt_cd", "")).strip() == "0"
        success = ok_http and rt_ok

        raw_out: dict[str, Any] = {
            "http_status": resp.status_code,
            "response_body": body_json if isinstance(body_json, dict) else str(body_json),
            "kis_payload": payload,
            "tr_id": headers.get("tr_id"),
            "실제_주문_없음": not success,
        }

        if success:
            return BrokerOrderResult(
                symbol=request.symbol,
                side=request.side,
                quantity=int(request.quantity),
                order_type=request.order_type,
                status="KIS_ORDER_SUBMITTED",
                broker_order_id=oid,
                submitted_price=request.limit_price,
                message="KIS order-cash POST completed (rt_cd=0).",
                raw=raw_out,
            )

        msg = raw_text[:500] if raw_text else str(resp.status_code)
        if isinstance(body_json, dict) and body_json.get("msg1"):
            msg = str(body_json.get("msg1"))
        return BrokerOrderResult(
            symbol=request.symbol,
            side=request.side,
            quantity=int(request.quantity),
            order_type=request.order_type,
            status="KIS_ORDER_REJECTED",
            broker_order_id=oid,
            submitted_price=request.limit_price,
            message=msg,
            raw=raw_out,
        )

    def _tr_id_cancel(self) -> str:
        """모의 `VTTC0803U` / 실전 `TTTC0803U` (KIS 국내 정정취소 TR)."""
        return "VTTC0803U" if not self._config.is_live else "TTTC0803U"

    def cancel_order(
        self,
        order_id: str,
        *,
        quantity: int = 0,
        org_no: str = "",
        all_qty: bool = True,
        execute: bool = False,
    ) -> BrokerOrderResult:
        """국내주식 미체결 주문 취소 (`order-rvsecncl`, RVSE_CNCL_DVSN_CD='02').

        손절 LIMIT이 미체결로 남을 때 취소→재호가에 쓴다(#6).
        safe_mode/execute 게이트는 place_order와 동일(둘 중 하나라도 막히면 POST 안 함).
        """
        oid = str(order_id or "").strip()

        def _res(status: str, msg: str, raw: dict[str, Any]) -> BrokerOrderResult:
            return BrokerOrderResult(
                symbol=oid,
                side="CANCEL",
                quantity=int(quantity or 0),
                order_type="CANCEL",
                status=status,
                broker_order_id=oid or None,
                submitted_price=None,
                message=msg,
                raw=raw,
            )

        if not oid:
            return _res(
                "KIS_CANCEL_BUILD_FAILED",
                "order_id(ORGN_ODNO) required",
                {"실제_주문_없음": True},
            )
        body = {
            "CANO": self._config.account_no.strip(),
            "ACNT_PRDT_CD": self._config.account_product_code.strip(),
            "KRX_FWDG_ORD_ORGNO": str(org_no or ""),
            "ORGN_ODNO": oid,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(int(quantity or 0)),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y" if all_qty else "N",
        }
        preview = {
            "kis_payload": body,
            "tr_id": self._tr_id_cancel(),
            "endpoint": "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            "kis_env": self._config.env,
        }
        if self._safe_mode:
            return _res("KIS_SAFE_MODE_BLOCKED", "safe_mode=True: no cancel HTTP.", {**preview, "실제_주문_없음": True})
        if not execute:
            return _res("KIS_ORDER_SEND_BLOCKED_PHASE3", "execute=False: no cancel HTTP.", {**preview, "실제_주문_없음": True})

        tok = self.get_access_token()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {tok}",
            "appkey": self._config.app_key,
            "appsecret": self._config.app_secret,
            "tr_id": self._tr_id_cancel(),
            "custtype": "P",
        }
        url = f"{self._config.base_url.rstrip('/')}/uapi/domestic-stock/v1/trading/order-rvsecncl"
        try:
            resp = self._session.post(url, headers=headers, data=json.dumps(body), timeout=60)
        except requests.RequestException as e:
            return _res("KIS_CANCEL_FAILED", f"cancel POST exception: {e!r}", {**preview, "실제_주문_없음": True})
        try:
            bj: Any = resp.json()
        except json.JSONDecodeError:
            bj = {"_non_json_body": resp.text}
        rt_ok = isinstance(bj, dict) and str(bj.get("rt_cd", "")).strip() == "0"
        success = resp.status_code == 200 and rt_ok
        raw = {
            "http_status": resp.status_code,
            "response_body": bj if isinstance(bj, dict) else str(bj),
            "kis_payload": body,
            "tr_id": headers["tr_id"],
            "실제_주문_없음": not success,
        }
        if success:
            return _res("KIS_CANCEL_SUBMITTED", "KIS order-rvsecncl completed (rt_cd=0).", raw)
        msg = (
            str(bj.get("msg1"))
            if isinstance(bj, dict) and bj.get("msg1")
            else (resp.text[:500] if resp.text else str(resp.status_code))
        )
        return _res("KIS_CANCEL_REJECTED", msg, raw)

    # ══════════════════════════════════════════════
    # 해외주식 (미국장) — KIS Overseas Stock API
    #   매수 TTTT1002U / 매도 TTTT1006U / 잔고 TTTS3012R (모의: V* 프리픽스)
    #   원화 통합증거금 계좌 → 증권사 자동 환전. USD 단가 기준.
    # ══════════════════════════════════════════════

    # 미국 거래소 코드 (주문/잔고 조회용)
    _US_EXCHANGES = ("NASD", "NYSE", "AMEX")

    def _tr_id_overseas_buy(self) -> str:
        return "VTTT1002U" if not self._config.is_live else "TTTT1002U"

    def _tr_id_overseas_sell(self) -> str:
        return "VTTT1001U" if not self._config.is_live else "TTTT1006U"

    def _tr_id_overseas_balance(self) -> str:
        return "VTTS3012R" if not self._config.is_live else "TTTS3012R"

    @staticmethod
    def _parse_overseas_symbol(symbol: str) -> tuple[str, str]:
        """'NASD:NVDA' → ('NASD', 'NVDA'), 'NVDA' → ('', 'NVDA')."""
        s = (symbol or "").strip().upper()
        if ":" in s:
            exch, _, tick = s.partition(":")
            return exch.strip(), tick.strip()
        return "", s

    def get_positions_overseas(self) -> list[BrokerPosition]:
        """해외주식 잔고조회 (`/uapi/overseas-stock/v1/trading/inquire-balance`).

        미국 3개 거래소(NASD/NYSE/AMEX)를 순회해 보유 종목을 취합한다.
        symbol은 'NASD:NVDA' 형식으로 거래소를 보존한다.
        """
        tr = self._tr_id_overseas_balance()
        cano = self._config.account_no.strip()
        acnt = self._config.account_product_code.strip()
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        positions: list[BrokerPosition] = []
        seen: set[str] = set()
        for exch in self._US_EXCHANGES:
            params: dict[str, Any] = {
                "CANO": cano,
                "ACNT_PRDT_CD": acnt,
                "OVRS_EXCG_CD": exch,
                "TR_CRCY_CD": "USD",
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            }
            hdr = self._inquire_headers(tr)
            try:
                resp = kis_get_request(self._session, self._config.base_url, path, headers=hdr, params=params)
                body = resp.json()
            except Exception:
                continue
            if not isinstance(body, dict):
                continue
            out1 = body.get("output1") or body.get("Output1")
            rows = out1 if isinstance(out1, list) else ([out1] if isinstance(out1, dict) else [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                tick = self._pick_str(row, ("ovrs_pdno", "OVRS_PDNO", "pdno"))
                if not tick:
                    continue
                qty = self._pick_float(row, ("ovrs_cblc_qty", "OVRS_CBLC_QTY", "cblc_qty13"))
                if qty is None or float(qty) <= 0:
                    continue
                key = f"{exch}:{tick}"
                if key in seen:
                    continue
                seen.add(key)
                positions.append(
                    BrokerPosition(
                        symbol=key,
                        quantity=int(float(qty)),  # 정수 주수 (소수점 보유는 raw에 보존)
                        avg_price=self._pick_float(row, ("pchs_avg_pric", "PCHS_AVG_PRIC")),
                        current_price=self._pick_float(row, ("now_pric2", "NOW_PRIC2", "ovrs_now_pric1")),
                        market_value=self._pick_float(row, ("ovrs_stck_evlu_amt", "OVRS_STCK_EVLU_AMT", "evlu_amt")),
                        raw={**dict(row), "exchange": exch, "ticker": tick, "qty_exact": float(qty)},
                    )
                )
        return positions

    def get_overseas_cash_balance(self) -> BrokerCashBalance:
        """해외주식 주문가능 USD 조회.

        원화 통합증거금 계좌이므로 가능한 경우 외화 매수가능금액(USD)을 반환.
        실패 시 None. (정확 조회는 별도 psamount API가 필요할 수 있어 잔고 output2 폴백.)
        """
        tr = self._tr_id_overseas_balance()
        cano = self._config.account_no.strip()
        acnt = self._config.account_product_code.strip()
        path = "/uapi/overseas-stock/v1/trading/inquire-balance"
        params: dict[str, Any] = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt,
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        hdr = self._inquire_headers(tr)
        raw: dict[str, Any] = {"tr_id": tr}
        try:
            resp = kis_get_request(self._session, self._config.base_url, path, headers=hdr, params=params)
            body = resp.json()
            raw["http_status"] = resp.status_code
            raw["response_body"] = body
        except Exception as exc:
            raw["error"] = str(exc)
            return BrokerCashBalance(cash=None, withdrawable_cash=None, raw=raw)
        out2 = body.get("output2") if isinstance(body, dict) else None
        row = out2[0] if isinstance(out2, list) and out2 and isinstance(out2[0], dict) else (
            out2 if isinstance(out2, dict) else {}
        )
        # 외화 매수가능 / 예수금 (USD)
        usd = self._pick_float(row, ("frcr_pchs_amt1", "FRCR_PCHS_AMT1", "frcr_dncl_amt_2", "frcr_evlu_tota"))
        # 원화 통합증거금: 외화예수금이 0이어도 원화기반 매수가능액이 있을 수 있으므로
        # 매수가능금액(psamount) API로 보강한다.
        if usd is None or float(usd) <= 0:
            try:
                buyable = self.get_overseas_buyable_usd()
                if buyable is not None and float(buyable) > 0:
                    raw["buyable_via_psamount"] = float(buyable)
                    return BrokerCashBalance(cash=float(buyable), withdrawable_cash=float(buyable), raw=raw)
            except Exception as exc:  # noqa: BLE001
                raw["psamount_error"] = str(exc)
        return BrokerCashBalance(cash=usd, withdrawable_cash=usd, raw=raw)

    def _tr_id_overseas_psamount(self) -> str:
        return "VTTS3007R" if not self._config.is_live else "TTTS3007R"

    def get_overseas_buyable_usd(
        self,
        ticker: str = "AAPL",
        price_usd: float = 1.0,
        exch: str = "NASD",
    ) -> float | None:
        """해외주식 매수가능금액 조회(`inquire-psamount`).

        원화 통합증거금 계좌에서 원화 기반 매수가능 외화금액(USD)을 반환한다.
        외화예수금이 0이어도 원화로 환산 가능한 매수여력을 잡아낸다.
        대표종목/단가로 조회하며, 반환되는 주문가능외화금액은 종목과 무관한
        계좌 단위 매수여력에 가깝다. 실패 시 None.
        """
        tr = self._tr_id_overseas_psamount()
        cano = self._config.account_no.strip()
        acnt = self._config.account_product_code.strip()
        path = "/uapi/overseas-stock/v1/trading/inquire-psamount"
        params: dict[str, Any] = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt,
            "OVRS_EXCG_CD": exch,
            "OVRS_ORD_UNPR": f"{float(price_usd):.4f}",
            "ITEM_CD": ticker.strip().upper(),
        }
        hdr = self._inquire_headers(tr)
        try:
            resp = kis_get_request(self._session, self._config.base_url, path, headers=hdr, params=params)
            body = resp.json()
        except Exception:
            return None
        if not isinstance(body, dict):
            return None
        out = body.get("output") or body.get("Output") or {}
        if isinstance(out, list):
            out = out[0] if out and isinstance(out[0], dict) else {}
        if not isinstance(out, dict):
            return None
        # 주문가능 외화금액(통합증거금 환산 포함). 필드마다 0/잔여가 섞여 나오므로
        # 후보 중 최댓값을 매수여력으로 본다. frcr_ord_psbl_amt1 이 통합증거금 인식 필드.
        candidates = []
        for k in (
            "ord_psbl_frcr_amt",
            "ORD_PSBL_FRCR_AMT",
            "frcr_ord_psbl_amt1",
            "ovrs_ord_psbl_amt",
            "OVRS_ORD_PSBL_AMT",
        ):
            v = self._pick_float(out, (k,))
            if v is not None:
                candidates.append(float(v))
        return max(candidates) if candidates else None

    def place_order_overseas(
        self,
        symbol: str,
        side: str,
        quantity: int,
        limit_price_usd: float,
        *,
        exchange: str | None = None,
        execute: bool = False,
    ) -> BrokerOrderResult:
        """미국 주식 지정가 주문.

        Args:
            symbol: 'NASD:NVDA' 또는 'NVDA' (exchange 인자로 보완)
            side: 'BUY' | 'SELL'
            quantity: 정수 주수
            limit_price_usd: 지정가 (USD)
            exchange: 거래소 코드 (NASD/NYSE/AMEX). symbol에 없으면 사용. 기본 NASD.
            execute: True + safe_mode=False 일 때만 실제 POST.
        """
        _exch_from_sym, ticker = self._parse_overseas_symbol(symbol)
        exch = (exchange or _exch_from_sym or "NASD").strip().upper()
        _side = (side or "BUY").strip().upper()
        tr_id = self._tr_id_overseas_sell() if _side == "SELL" else self._tr_id_overseas_buy()
        endpoint = "/uapi/overseas-stock/v1/trading/order"

        cano = self._config.account_no.strip()
        acnt = self._config.account_product_code.strip()
        payload = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt,
            "OVRS_EXCG_CD": exch,
            "PDNO": ticker,
            "ORD_QTY": str(int(quantity)),
            "OVRS_ORD_UNPR": f"{float(limit_price_usd):.2f}",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",   # 지정가
        }
        preview: dict[str, Any] = {
            "kis_payload": payload, "tr_id": tr_id,
            "endpoint": endpoint, "kis_env": self._config.env,
        }

        def _result(status: str, oid: str | None, message: str, raw: dict[str, Any]) -> BrokerOrderResult:
            return BrokerOrderResult(
                symbol=f"{exch}:{ticker}", side=_side, quantity=int(quantity),
                order_type="LIMIT", status=status, broker_order_id=oid,
                submitted_price=float(limit_price_usd), message=message, raw=raw,
            )

        # 입력 검증
        if not ticker or quantity <= 0 or limit_price_usd <= 0:
            preview["실제_주문_없음"] = True
            return _result("KIS_ORDER_BUILD_FAILED", None,
                           f"invalid overseas order: ticker={ticker} qty={quantity} px={limit_price_usd}", preview)

        # safe_mode / execute 게이트 (국내와 동일 안전 패턴)
        if self._safe_mode:
            preview["실제_주문_없음"] = True
            return _result("KIS_SAFE_MODE_BLOCKED", None, "safe_mode=True: no KIS overseas order HTTP.", preview)
        if not execute:
            preview["실제_주문_없음"] = True
            preview["note"] = "execute=False: overseas order POST not sent (preview only)."
            return _result("KIS_ORDER_SEND_BLOCKED_PHASE3", None, "execute=False: no HTTP order (guard/dry path).", preview)

        # 실제 주문 POST
        def _ovs_headers() -> dict[str, str]:
            return {
                "authorization": f"Bearer {self.get_access_token()}",
                "appkey": self._config.app_key,
                "appsecret": self._config.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
                "content-type": "application/json; charset=utf-8",
            }
        url = f"{self._config.base_url.rstrip('/')}{endpoint}"
        try:
            resp = self._session.post(url, headers=_ovs_headers(), data=json.dumps(payload), timeout=60)
            body_json = resp.json()
            # 토큰 만료(EGW00123) 시 새 토큰으로 1회 재시도
            if self._is_expired_token_body(body_json):
                import logging
                logging.getLogger(__name__).warning("KIS 토큰 만료 감지 — 해외주문 재발급 후 재시도")
                self.get_access_token(force=True)
                resp = self._session.post(url, headers=_ovs_headers(), data=json.dumps(payload), timeout=60)
                body_json = resp.json()
        except Exception as exc:
            return _result("KIS_ORDER_HTTP_FAILED", None, f"overseas order POST failed: {exc}",
                           {**preview, "error": str(exc), "실제_주문_없음": True})

        oid = None
        if isinstance(body_json, dict):
            out = body_json.get("output") or body_json.get("Output")
            if isinstance(out, dict):
                oid = out.get("ODNO") or out.get("ORD_NO")
                if oid is not None:
                    oid = str(oid)
        success = resp.status_code == 200 and isinstance(body_json, dict) and str(body_json.get("rt_cd", "")).strip() == "0"
        raw_out = {
            "http_status": resp.status_code,
            "response_body": body_json if isinstance(body_json, dict) else str(body_json),
            "kis_payload": payload, "tr_id": tr_id,
            "실제_주문_없음": not success,
        }
        if success:
            return _result("KIS_ORDER_SUBMITTED", oid, "KIS overseas order POST completed (rt_cd=0).", raw_out)
        msg = str(body_json.get("msg1")) if isinstance(body_json, dict) and body_json.get("msg1") else f"HTTP {resp.status_code}"
        return _result("KIS_ORDER_REJECTED", oid, msg, raw_out)

    def _tr_id_overseas_nccs(self) -> str:
        """해외 미체결내역조회."""
        return "VTTS3018R" if not self._config.is_live else "TTTS3018R"

    def _tr_id_overseas_cancel(self) -> str:
        """해외(미국) 정정취소."""
        return "VTTT1004U" if not self._config.is_live else "TTTT1004U"

    def get_open_orders_overseas(self) -> list[dict[str, Any]]:
        """해외주식 미체결주문 조회(`inquire-nccs`).

        미국 3개 거래소를 순회하며 미체결(잔량>0) 주문을 취합한다.
        반환: [{order_id, exchange, ticker, symbol, side, qty, remaining,
                price_usd, order_dt}] — 취소는 cancel_order_overseas로.
        """
        tr = self._tr_id_overseas_nccs()
        cano = self._config.account_no.strip()
        acnt = self._config.account_product_code.strip()
        path = "/uapi/overseas-stock/v1/trading/inquire-nccs"
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for exch in self._US_EXCHANGES:
            params = {
                "CANO": cano, "ACNT_PRDT_CD": acnt, "OVRS_EXCG_CD": exch,
                "SORT_SQN": "DS", "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
            }
            try:
                body, _ = self._kis_get_json(tr, path, params)
            except Exception:
                continue
            if not isinstance(body, dict):
                continue
            rows = body.get("output") or body.get("Output") or []
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                oid = self._pick_str(row, ("odno", "ODNO", "ord_no"))
                if not oid:
                    continue
                remaining = self._pick_int(row, ("nccs_qty", "NCCS_QTY", "ord_psbl_qty"))
                if remaining is None:
                    ord_q = self._pick_int(row, ("ft_ord_qty", "FT_ORD_QTY", "ord_qty")) or 0
                    ccld_q = self._pick_int(row, ("ft_ccld_qty", "FT_CCLD_QTY", "ccld_qty")) or 0
                    remaining = max(0, ord_q - ccld_q)
                if not remaining or remaining <= 0:
                    continue
                tick = self._pick_str(row, ("pdno", "PDNO", "ovrs_pdno")) or ""
                key = f"{exch}:{tick}:{oid}"
                if key in seen:
                    continue
                seen.add(key)
                sbd = self._pick_str(row, ("sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD")) or ""
                side = "SELL" if sbd == "01" else "BUY"
                out.append({
                    "order_id": oid,
                    "exchange": exch,
                    "ticker": tick,
                    "symbol": f"{exch}:{tick}",
                    "side": side,
                    "qty": self._pick_int(row, ("ft_ord_qty", "FT_ORD_QTY", "ord_qty")) or remaining,
                    "remaining": remaining,
                    "price_usd": self._pick_float(row, ("ft_ord_unpr3", "FT_ORD_UNPR3", "ovrs_ord_unpr", "ord_unpr")),
                    "order_dt": self._pick_str(row, ("ord_dt", "ORD_DT", "dmst_ord_dt")) or "",
                })
        return out

    def cancel_order_overseas(
        self,
        order_id: str,
        ticker: str,
        quantity: int,
        *,
        exchange: str = "NASD",
        execute: bool = False,
    ) -> BrokerOrderResult:
        """해외주식 미체결 주문 취소 (`order-rvsecncl`, RVSE_CNCL_DVSN_CD='02').

        safe_mode/execute 게이트는 주문과 동일(둘 중 하나라도 막히면 POST 안 함).
        """
        oid = str(order_id or "").strip()
        exch = (exchange or "NASD").strip().upper()
        tick = (ticker or "").strip().upper()
        tr_id = self._tr_id_overseas_cancel()
        endpoint = "/uapi/overseas-stock/v1/trading/order-rvsecncl"

        def _res(status: str, msg: str, raw: dict[str, Any]) -> BrokerOrderResult:
            return BrokerOrderResult(
                symbol=f"{exch}:{tick}", side="CANCEL", quantity=int(quantity or 0),
                order_type="CANCEL", status=status, broker_order_id=oid or None,
                submitted_price=None, message=msg, raw=raw,
            )

        if not oid or not tick:
            return _res("KIS_CANCEL_BUILD_FAILED", "order_id(ORGN_ODNO)·ticker required",
                        {"실제_주문_없음": True})

        payload = {
            "CANO": self._config.account_no.strip(),
            "ACNT_PRDT_CD": self._config.account_product_code.strip(),
            "OVRS_EXCG_CD": exch,
            "PDNO": tick,
            "ORGN_ODNO": oid,
            "RVSE_CNCL_DVSN_CD": "02",   # 취소
            "ORD_QTY": str(int(quantity or 0)),
            "OVRS_ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0",
        }
        preview = {"kis_payload": payload, "tr_id": tr_id, "endpoint": endpoint,
                   "kis_env": self._config.env}
        if self._safe_mode:
            return _res("KIS_SAFE_MODE_BLOCKED", "safe_mode=True: no cancel HTTP.",
                        {**preview, "실제_주문_없음": True})
        if not execute:
            return _res("KIS_ORDER_SEND_BLOCKED_PHASE3", "execute=False: no cancel HTTP.",
                        {**preview, "실제_주문_없음": True})

        def _hdrs() -> dict[str, str]:
            return {
                "authorization": f"Bearer {self.get_access_token()}",
                "appkey": self._config.app_key,
                "appsecret": self._config.app_secret,
                "tr_id": tr_id, "custtype": "P",
                "content-type": "application/json; charset=utf-8",
            }
        url = f"{self._config.base_url.rstrip('/')}{endpoint}"
        try:
            resp = self._session.post(url, headers=_hdrs(), data=json.dumps(payload), timeout=60)
            bj: Any = resp.json()
            if self._is_expired_token_body(bj):
                self.get_access_token(force=True)
                resp = self._session.post(url, headers=_hdrs(), data=json.dumps(payload), timeout=60)
                bj = resp.json()
        except Exception as e:
            return _res("KIS_CANCEL_FAILED", f"overseas cancel POST exception: {e!r}",
                        {**preview, "실제_주문_없음": True})
        rt_ok = isinstance(bj, dict) and str(bj.get("rt_cd", "")).strip() == "0"
        success = resp.status_code == 200 and rt_ok
        raw = {
            "http_status": resp.status_code,
            "response_body": bj if isinstance(bj, dict) else str(bj),
            "kis_payload": payload, "tr_id": tr_id, "실제_주문_없음": not success,
        }
        if success:
            return _res("KIS_CANCEL_SUBMITTED", "KIS overseas order-rvsecncl completed (rt_cd=0).", raw)
        msg = str(bj.get("msg1")) if isinstance(bj, dict) and bj.get("msg1") else f"HTTP {resp.status_code}"
        return _res("KIS_CANCEL_REJECTED", msg, raw)
