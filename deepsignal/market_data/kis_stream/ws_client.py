"""KIS WebSocket 클라이언트.

한국투자증권 Open API WebSocket 연결·구독·재연결을 관리.

프로토콜:
  - Approval Key: POST /oauth2/Approval (REST) → approval_key
  - 구독: JSON {"header": {...}, "body": {"input": {"tr_id": "H0STCNT0", "tr_key": "005930"}}}
  - 데이터: pipe-delimited 텍스트 ("0|H0STCNT0|1|005930^...")
  - PINGPONG: {"header": {"tr_id": "PINGPONG"}} → 동일 메시지로 응답
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# TR_TYPE: "1" = 등록, "2" = 해제
_TR_TYPE_REGISTER = "1"
_TR_TYPE_UNREGISTER = "2"


class KisWebSocketClient:
    """KIS WebSocket 연결 관리 클라이언트.

    사용 예:
        client = KisWebSocketClient(
            ws_url="wss://ops.koreainvestment.com:31000",
            approval_key="...",
            symbols=["005930", "000660"],
            on_message=my_handler,
            subscribe_orderbook=True,
        )
        await client.run()
    """

    def __init__(
        self,
        ws_url: str,
        approval_key: str,
        symbols: list[str],
        on_message: Callable[[str], Awaitable[None]] | Callable[[str], None],
        subscribe_orderbook: bool = True,
        subscribe_kospi: bool = True,
        reconnect_delay_s: float = 3.0,
        max_reconnect_attempts: int = 20,
        ping_interval_s: float = 30.0,
    ) -> None:
        self.ws_url = ws_url
        self.approval_key = approval_key
        self.symbols = [s.strip() for s in symbols if s.strip()]
        self._on_message = on_message
        self.subscribe_orderbook = subscribe_orderbook
        self.subscribe_kospi = subscribe_kospi
        self.reconnect_delay_s = reconnect_delay_s
        self.max_reconnect_attempts = max_reconnect_attempts
        self.ping_interval_s = ping_interval_s

        self._stop_event = asyncio.Event()
        self._ws: Any = None
        self._connected = False
        self._attempt = 0
        self.stats: dict[str, int] = {
            "messages": 0,
            "trades": 0,
            "orderbooks": 0,
            "pings": 0,
            "errors": 0,
            "reconnects": 0,
        }

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        """재연결 루프. stop() 호출 또는 max_reconnect_attempts 초과 시 종료."""
        while not self._stop_event.is_set():
            if self._attempt >= self.max_reconnect_attempts:
                logger.error(
                    "KIS WS 최대 재연결 횟수(%d) 초과 — 종료",
                    self.max_reconnect_attempts,
                )
                break
            try:
                await self._connect_and_loop()
                self._attempt = 0  # 정상 종료 시 카운터 리셋
            except Exception as exc:
                self._attempt += 1
                self.stats["errors"] = self.stats.get("errors", 0) + 1
                wait = min(self.reconnect_delay_s * (1.5 ** min(self._attempt, 8)), 60.0)
                logger.warning(
                    "KIS WS 연결 끊김 (시도 %d/%d): %s — %.1f초 후 재연결",
                    self._attempt, self.max_reconnect_attempts, exc, wait,
                )
                if not self._stop_event.is_set():
                    self.stats["reconnects"] = self.stats.get("reconnects", 0) + 1
                    await asyncio.sleep(wait)

    async def _connect_and_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            raise RuntimeError(
                "websockets 패키지가 없습니다. pip install websockets"
            )

        logger.info("KIS WS 연결: %s (%d 심볼)", self.ws_url, len(self.symbols))
        async with websockets.connect(
            self.ws_url,
            ping_interval=None,  # 직접 PINGPONG 처리
            close_timeout=5,
            max_size=2**20,
        ) as ws:
            self._ws = ws
            self._connected = True
            logger.info("KIS WS 연결 성공")

            # 전 심볼 구독
            await self._subscribe_all(ws)

            # 메시지 루프 + PINGPONG 타이머 병렬 실행
            await asyncio.gather(
                self._recv_loop(ws),
                self._ping_loop(ws),
            )

    async def _subscribe_all(self, ws: Any) -> None:
        for sym in self.symbols:
            await self._send_subscribe(ws, "H0STCNT0", sym)
            if self.subscribe_orderbook:
                await self._send_subscribe(ws, "H0STASP0", sym)
        # KOSPI 지수 구독 (H0UPCNT0, 코드 "0001")
        if self.subscribe_kospi:
            await self._send_subscribe(ws, "H0UPCNT0", "0001")
        logger.info(
            "구독 완료: %d 심볼 × %s%s",
            len(self.symbols),
            "체결+호가" if self.subscribe_orderbook else "체결만",
            " + KOSPI지수" if self.subscribe_kospi else "",
        )

    async def _send_subscribe(self, ws: Any, tr_id: str, tr_key: str) -> None:
        msg = json.dumps({
            "header": {
                "approval_key": self.approval_key,
                "custtype": "P",
                "tr_type": _TR_TYPE_REGISTER,
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": tr_id,
                    "tr_key": tr_key,
                },
            },
        })
        await ws.send(msg)
        logger.debug("구독 요청: %s %s", tr_id, tr_key)

    async def _recv_loop(self, ws: Any) -> None:
        async for raw in ws:
            if self._stop_event.is_set():
                break
            self.stats["messages"] = self.stats.get("messages", 0) + 1
            try:
                await self._dispatch(ws, raw)
            except Exception as exc:
                logger.debug("메시지 처리 오류: %s", exc)
                self.stats["errors"] = self.stats.get("errors", 0) + 1

    async def _dispatch(self, ws: Any, raw: str) -> None:
        # PINGPONG 제어 메시지
        if raw.strip().startswith("{"):
            try:
                ctrl = json.loads(raw)
            except json.JSONDecodeError:
                return
            tr_id = (ctrl.get("header") or {}).get("tr_id", "")
            if tr_id == "PINGPONG":
                await ws.send(raw)  # 동일 메시지로 응답
                self.stats["pings"] = self.stats.get("pings", 0) + 1
                logger.debug("PINGPONG 응답")
                return
            # 구독 확인 응답
            rsp_cd = (ctrl.get("body") or {}).get("rt_cd", "")
            rsp_msg = (ctrl.get("body") or {}).get("msg1", "")
            if rsp_cd == "0":
                logger.debug("구독 확인: %s", rsp_msg)
            else:
                logger.warning("구독 오류 응답: code=%s msg=%s", rsp_cd, rsp_msg)
            return

        # 실시간 데이터 메시지 → 상위 핸들러로 전달
        parts = raw.split("|", 3)
        if len(parts) >= 4:
            tr_id = parts[1]
            if tr_id == "H0STCNT0":
                self.stats["trades"] = self.stats.get("trades", 0) + 1
            elif tr_id == "H0STASP0":
                self.stats["orderbooks"] = self.stats.get("orderbooks", 0) + 1
            elif tr_id == "H0UPCNT0":
                self.stats["index_ticks"] = self.stats.get("index_ticks", 0) + 1

        if asyncio.iscoroutinefunction(self._on_message):
            await self._on_message(raw)
        else:
            self._on_message(raw)

    async def _ping_loop(self, ws: Any) -> None:
        """KIS 서버가 PINGPONG을 보내지 않는 경우를 대비한 능동 유지."""
        while not self._stop_event.is_set():
            await asyncio.sleep(self.ping_interval_s)
            if self._stop_event.is_set():
                break
            # websockets 라이브러리 내장 ping 대신 KIS 형식 PINGPONG 전송
            try:
                await ws.send(json.dumps({"header": {"tr_id": "PINGPONG"}}))
                logger.debug("PINGPONG 전송")
            except Exception:
                break  # 연결 끊김 → _recv_loop도 종료됨


async def get_approval_key(
    base_url: str,
    app_key: str,
    app_secret: str,
) -> str:
    """KIS WebSocket 전용 approval_key 취득 (REST).

    POST {base_url}/oauth2/Approval
    Body: {"grant_type": "client_credentials", "appkey": ..., "secretkey": ...}
    """
    try:
        import aiohttp
    except ImportError:
        raise RuntimeError("aiohttp 패키지가 없습니다. pip install aiohttp")

    url = f"{base_url.rstrip('/')}/oauth2/Approval"
    body = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "secretkey": app_secret,
    }
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=15)
    ) as session:
        async with session.post(
            url,
            json=body,
            headers={"content-type": "application/json; charset=utf-8"},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(
                    f"Approval Key 취득 실패 (HTTP {resp.status}): {text[:200]}"
                )
            data = await resp.json()

    key = data.get("approval_key")
    if not key or not isinstance(key, str):
        raise RuntimeError(f"approval_key 없음 — 응답: {data!r}")

    logger.info("✅ KIS Approval Key 취득 완료 (length=%d)", len(key))
    return key
