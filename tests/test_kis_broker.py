"""KISBroker: 페이로드·safe_mode·토큰(mock)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from deepsignal.live_trading.broker_interface import BrokerOrderRequest
from deepsignal.live_trading.kis_broker import BrokerError, KISBroker, post_kis_order_cash_request
from deepsignal.live_trading.kis_config import KISConfig


def _cfg() -> KISConfig:
    return KISConfig(
        app_key="app",
        app_secret="sec",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="paper",
    )


def test_build_order_payload_buy_limit() -> None:
    b = KISBroker(_cfg(), safe_mode=True)
    p = b.build_order_payload(
        BrokerOrderRequest(
            symbol="005930",
            side="BUY",
            quantity=10,
            order_type="LIMIT",
            limit_price=70_000.0,
            estimated_value=700_000.0,
        )
    )
    assert p["CANO"] == "12345678"
    assert p["ACNT_PRDT_CD"] == "01"
    assert p["PDNO"] == "005930"
    assert p["ORD_DVSN"] == "00"
    assert p["ORD_QTY"] == "10"
    assert p["ORD_UNPR"] == "70000"


def test_build_order_payload_rejects_sell() -> None:
    b = KISBroker(_cfg(), safe_mode=True)
    with pytest.raises(BrokerError, match="BUY"):
        b.build_order_payload(
            BrokerOrderRequest(
                symbol="005930",
                side="SELL",
                quantity=1,
                order_type="LIMIT",
                limit_price=1.0,
            )
        )


def test_build_order_payload_rejects_market() -> None:
    b = KISBroker(_cfg(), safe_mode=True)
    with pytest.raises(BrokerError, match="LIMIT"):
        b.build_order_payload(
            BrokerOrderRequest(
                symbol="005930",
                side="BUY",
                quantity=1,
                order_type="MARKET",
                limit_price=None,
            )
        )


def test_build_order_payload_rejects_bad_qty() -> None:
    b = KISBroker(_cfg(), safe_mode=True)
    with pytest.raises(BrokerError, match="quantity"):
        b.build_order_payload(
            BrokerOrderRequest(
                symbol="005930",
                side="BUY",
                quantity=0,
                order_type="LIMIT",
                limit_price=1.0,
            )
        )


def test_build_order_payload_rejects_no_limit() -> None:
    b = KISBroker(_cfg(), safe_mode=True)
    with pytest.raises(BrokerError, match="limit_price"):
        b.build_order_payload(
            BrokerOrderRequest(
                symbol="005930",
                side="BUY",
                quantity=1,
                order_type="LIMIT",
                limit_price=None,
            )
        )


def test_place_order_safe_mode_no_order_http() -> None:
    session = MagicMock()
    b = KISBroker(_cfg(), safe_mode=True, session=session)
    r = b.place_order(
        BrokerOrderRequest(
            symbol="005930",
            side="BUY",
            quantity=2,
            order_type="LIMIT",
            limit_price=68_000.0,
        )
    )
    assert r.status == "KIS_SAFE_MODE_BLOCKED"
    for call in session.post.call_args_list:
        url = call[0][0] if call[0] else ""
        assert "order-cash" not in str(url)


def test_place_order_safe_mode_false_still_blocks_send() -> None:
    session = MagicMock()
    b = KISBroker(_cfg(), safe_mode=False, session=session)
    r = b.place_order(
        BrokerOrderRequest(
            symbol="005930",
            side="BUY",
            quantity=1,
            order_type="LIMIT",
            limit_price=70_000.0,
        )
    )
    assert r.status == "KIS_ORDER_SEND_BLOCKED_PHASE3"
    for call in session.post.call_args_list:
        url = call[0][0] if call[0] else ""
        assert "order-cash" not in str(url)


def test_get_access_token_uses_session_mock(tmp_path) -> None:
    class Resp:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "tok123", "expires_in": 600}

    session = MagicMock()
    session.post.return_value = Resp()
    b = KISBroker(_cfg(), safe_mode=True, session=session, token_cache_path=tmp_path / "token.json")
    t1 = b.get_access_token()
    t2 = b.get_access_token()
    assert t1 == "tok123" and t2 == "tok123"
    assert session.post.call_count >= 1
    first_url = session.post.call_args_list[0][0][0]
    assert "oauth2/tokenP" in first_url


def test_get_access_token_uses_file_cache_across_brokers(tmp_path) -> None:
    class Resp:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "tok-file", "expires_in": 600}

    cache_path = tmp_path / "token.json"
    first_session = MagicMock()
    first_session.post.return_value = Resp()
    first = KISBroker(_cfg(), safe_mode=True, session=first_session, token_cache_path=cache_path)
    assert first.get_access_token() == "tok-file"
    assert first_session.post.call_count == 1

    second_session = MagicMock()
    second = KISBroker(_cfg(), safe_mode=True, session=second_session, token_cache_path=cache_path)
    assert second.get_access_token() == "tok-file"
    second_session.post.assert_not_called()


def test_place_order_execute_posts_order_cash_once() -> None:
    """[실전-4] execute=True + safe_mode=False → order-cash POST 1회 (mock)."""

    class TokResp:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "tok", "expires_in": 600}

    class OrderResp:
        status_code = 200
        text = '{"rt_cd":"0","msg1":"ok","output":{"ODNO":"999"}}'

        def json(self) -> dict:
            return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "999"}}

    session = MagicMock()

    def post_side_effect(url: str, **kwargs: object) -> TokResp | OrderResp:
        if "oauth2/tokenP" in url:
            return TokResp()
        if "order-cash" in url:
            return OrderResp()
        raise AssertionError(url)

    session.post.side_effect = post_side_effect
    cfg = KISConfig(
        app_key="app",
        app_secret="sec",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="live",
    )
    b = KISBroker(cfg, safe_mode=False, session=session)
    r = b.place_order(
        BrokerOrderRequest(
            symbol="005930",
            side="BUY",
            quantity=1,
            order_type="LIMIT",
            limit_price=70_000.0,
            estimated_value=70_000.0,
        ),
        execute=True,
    )
    assert r.status == "KIS_ORDER_SUBMITTED"
    order_posts = [c for c in session.post.call_args_list if "order-cash" in str(c[0][0])]
    assert len(order_posts) == 1
    assert isinstance(r.raw, dict)
    assert "response_body" in r.raw


def test_place_order_execute_failure_logged_in_raw() -> None:
    class TokResp:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "tok", "expires_in": 600}

    class BadOrderResp:
        status_code = 200
        text = '{"rt_cd":"1","msg1":"mock reject"}'

        def json(self) -> dict:
            return {"rt_cd": "1", "msg1": "mock reject"}

    session = MagicMock()

    def post_side_effect(url: str, **kwargs: object) -> TokResp | BadOrderResp:
        if "oauth2/tokenP" in url:
            return TokResp()
        if "order-cash" in url:
            return BadOrderResp()
        raise AssertionError(url)

    session.post.side_effect = post_side_effect
    cfg = KISConfig(
        app_key="app",
        app_secret="sec",
        account_no="12345678",
        account_product_code="01",
        hts_id=None,
        env="live",
    )
    b = KISBroker(cfg, safe_mode=False, session=session)
    r = b.place_order(
        BrokerOrderRequest(
            symbol="005930",
            side="BUY",
            quantity=1,
            order_type="LIMIT",
            limit_price=70_000.0,
            estimated_value=70_000.0,
        ),
        execute=True,
    )
    assert r.status == "KIS_ORDER_REJECTED"
    assert isinstance(r.raw, dict)
    assert r.raw.get("response_body") is not None


def test_post_kis_order_cash_request_signature() -> None:
    """post_kis_order_cash_request 헬퍼는 Session.post로 order-cash URL을 호출한다(호출부는 테스트에서 mock)."""
    session = MagicMock()
    session.post.return_value.status_code = 200
    cfg = _cfg()
    post_kis_order_cash_request(
        session,
        cfg.base_url,
        headers={"authorization": "Bearer x"},
        body={"CANO": "12345678"},
    )
    assert session.post.called


def test_get_positions_parses_inquire_balance() -> None:
    class Tok:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "t", "expires_in": 600}

    class Bal:
        status_code = 200

        def json(self) -> dict:
            return {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "3", "pchs_avg_pric": "1", "prpr": "2", "evlu_amt": "6"}],
                "output2": [{"dnca_tot_amt": "100", "ord_psbl_cash": "99"}],
            }

    s = MagicMock()

    def post(url: str, **kwargs: object) -> Tok:
        assert "oauth2/tokenP" in url
        return Tok()

    def get(url: str, **kwargs: object) -> Bal:
        assert "inquire-balance" in url
        return Bal()

    s.post.side_effect = post
    s.get.side_effect = get
    b = KISBroker(_cfg(), safe_mode=True, session=s)
    pos = b.get_positions()
    assert len(pos) == 1
    assert pos[0].symbol == "005930"
    assert pos[0].quantity == 3


def test_get_positions_uses_hldg_qty_before_ord_psbl_qty() -> None:
    class Tok:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "t", "expires_in": 600}

    class Bal:
        status_code = 200

        def json(self) -> dict:
            return {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "hldg_qty": "2", "ord_psbl_qty": "7"}],
                "output2": [],
            }

    s = MagicMock()
    s.post.side_effect = lambda url, **kw: Tok() if "oauth2/tokenP" in url else MagicMock()
    s.get.return_value = Bal()
    b = KISBroker(_cfg(), safe_mode=True, session=s)
    pos = b.get_positions()
    assert len(pos) == 1
    assert pos[0].quantity == 2


def test_get_positions_falls_back_to_ord_psbl_qty() -> None:
    class Tok:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "t", "expires_in": 600}

    class Bal:
        status_code = 200

        def json(self) -> dict:
            return {
                "rt_cd": "0",
                "output1": [{"pdno": "005930", "ord_psbl_qty": "1"}],
                "output2": [],
            }

    s = MagicMock()
    s.post.side_effect = lambda url, **kw: Tok() if "oauth2/tokenP" in url else MagicMock()
    s.get.return_value = Bal()
    b = KISBroker(_cfg(), safe_mode=True, session=s)
    pos = b.get_positions()
    assert len(pos) == 1
    assert pos[0].quantity == 1


def test_get_positions_excludes_zero_qty_and_bad_pdno() -> None:
    class Tok:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "t", "expires_in": 600}

    class Bal:
        status_code = 200

        def json(self) -> dict:
            return {
                "rt_cd": "0",
                "output1": [
                    {"pdno": "005930", "hldg_qty": "0", "ord_psbl_qty": "4"},
                    {"pdno": "ABC", "hldg_qty": "2"},
                    {"prdt_id": "000660", "hldg_qty": "3"},
                    {"pdno": "000660", "hldg_qty": "1"},
                ],
                "output2": [],
            }

    s = MagicMock()
    s.post.side_effect = lambda url, **kw: Tok() if "oauth2/tokenP" in url else MagicMock()
    s.get.return_value = Bal()
    b = KISBroker(_cfg(), safe_mode=True, session=s)
    pos = b.get_positions()
    assert len(pos) == 1
    assert pos[0].symbol == "000660"
    assert pos[0].quantity == 1


def test_get_order_status_parses_inquire_daily_ccld() -> None:
    class Tok:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict:
            return {"access_token": "t", "expires_in": 600}

    class Ord:
        status_code = 200

        def json(self) -> dict:
            return {
                "rt_cd": "0",
                "output1": [
                    {
                        "odno": "9",
                        "pdno": "005930",
                        "ord_qty": "1",
                        "tot_ccld_qty": "0",
                        "rmnd_qty": "1",
                        "ord_unpr": "70000",
                        "sll_buy_dvsn_cd": "02",
                    }
                ],
            }

    s = MagicMock()
    s.post.side_effect = lambda url, **kw: Tok() if "oauth2/tokenP" in url else MagicMock()
    s.get.return_value = Ord()
    b = KISBroker(_cfg(), safe_mode=True, session=s)
    st = b.get_order_status(order_id="9", start_date="20260101", end_date="20260131")
    assert len(st) >= 1
    assert st[0].order_id == "9"
    assert st[0].symbol == "005930"
