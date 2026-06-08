"""네트워크 없는 가짜 브로커: 주문 결과만 생성."""



from __future__ import annotations



import uuid

from typing import Any, Mapping



from deepsignal.live_trading.broker_interface import (
    BrokerInterface,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
)





class DryRunBroker(BrokerInterface):

    """실제 주문·API 호출 없이 `BrokerOrderResult`만 반환한다."""



    def connect(self) -> None:

        return



    def submit_order(self, order: Mapping[str, Any]) -> Mapping[str, Any]:

        return {

            "status": "DRY_RUN_LEGACY_SUBMIT_NOT_USED",

            "message": "Use place_order(BrokerOrderRequest) for [실전-2]+ paths.",

            "order": dict(order),

        }



    def get_positions(self) -> list[BrokerPosition]:

        return []



    def place_order(
        self,
        request: BrokerOrderRequest,
        *,
        execute: bool = False,
    ) -> BrokerOrderResult:

        oid = f"dryrun_{uuid.uuid4().hex[:16]}"

        raw: dict[str, Any] = {

            "request": {

                "symbol": request.symbol,

                "side": request.side,

                "quantity": request.quantity,

                "order_type": request.order_type,

                "limit_price": request.limit_price,

                "estimated_value": request.estimated_value,

                "client_order_id": request.client_order_id,

                "source_plan_id": request.source_plan_id,

            },

            "dry_run": True,

            "실제_주문_없음": True,

        }

        return BrokerOrderResult(

            symbol=request.symbol,

            side=request.side,

            quantity=request.quantity,

            order_type=request.order_type,

            status="DRY_RUN_ACCEPTED",

            broker_order_id=oid,

            submitted_price=request.limit_price,

            message="Dry-run only. No broker API call.",

            raw=raw,

        )


