"""KIS 해외주식 실시간 스트림 파이프라인 오케스트레이터.

흐름:
  1. KIS Approval Key 취득
  2. WebSocket 연결 → HDFSCNT0(해외 체결) 구독
  3. 틱 → OhlcvAggregator → 봉 생성 → 피처엔진 → K-GSQS 채점
  4. 미국 정규장(22:30–05:00 KST) 외에는 대기
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from deepsignal.market_data.kis_stream.aggregator import KisOhlcvAggregator
from deepsignal.market_data.kis_stream.models import KisOhlcvBar, KisTradeTick
from deepsignal.market_data.kis_stream.persistence import KisStreamPersistence
from deepsignal.market_data.kis_stream.ws_client import KisWebSocketClient, get_approval_key

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# ── 기본 감시 심볼 (거래소, 티커) ─────────────────────────────────────────────
DEFAULT_OVERSEAS_SYMBOLS: list[tuple[str, str]] = [
    # Mega-cap stocks
    ("NASD", "NVDA"), ("NASD", "AAPL"), ("NASD", "MSFT"), ("NASD", "TSLA"),
    ("NASD", "META"), ("NASD", "AMZN"), ("NASD", "GOOGL"), ("NASD", "AVGO"),
    ("NASD", "AMD"), ("NASD", "MU"), ("NASD", "INTC"),
    # Finance / Energy
    ("NYSE", "JPM"), ("NYSE", "GS"), ("NYSE", "XOM"),
    # Regular ETFs
    ("AMEX", "SPY"), ("NASD", "QQQ"), ("NASD", "ONEQ"),
    # Leveraged/Inverse ETFs (ALL included)
    ("NASD", "TQQQ"), ("NASD", "SQQQ"), ("NYSE", "SPXL"), ("NYSE", "SPXS"),
    ("NYSE", "SOXL"), ("NYSE", "SOXS"), ("NASD", "FNGU"), ("NASD", "FNGD"),
    ("NYSE", "LABU"), ("NYSE", "LABD"), ("NYSE", "UVXY"),
    # Bonds / Commodities
    ("NYSE", "TLT"), ("NYSE", "GLD"), ("NYSE", "SLV"),
    # China ADRs
    ("NYSE", "BABA"), ("NASD", "JD"), ("NASD", "PDD"),
]


# ── 미국 시장 운영시간 ────────────────────────────────────────────────────────

def is_us_market_hours(now_kst: datetime | None = None) -> bool:
    """현재 시각이 미국 정규장(평일 22:30~05:00 KST) 내인지 확인.

    미국장은 EST 기준 월~금 09:30~16:00 → KST 변환 시 전날 22:30 ~ 당일 06:00.
    KST 요일 매핑:
      - 저녁(22:30~23:59): 토(5)·일(6) KST = 미국 토·일 → 휴장
      - 새벽(00:00~05:00): 일(6)·월(0) KST = 미국 토·일 → 휴장
    """
    if now_kst is None:
        now_kst = datetime.now(KST)
    h, m = now_kst.hour, now_kst.minute
    wd = now_kst.weekday()  # 0=월 … 5=토, 6=일

    # 22:30 ~ next day 05:00 KST 범위 확인
    if h == 22:
        in_hours = m >= 30
    elif h == 23:
        in_hours = True
    elif 0 <= h < 5:
        in_hours = True
    elif h == 5:
        in_hours = m == 0
    else:
        return False

    if not in_hours:
        return False

    # 주말 제외
    if h >= 22:
        # 저녁 구간: 토(5)·일(6) KST → 미국 토·일 → 휴장
        if wd in (5, 6):
            return False
    else:
        # 새벽 구간: 일(6)·월(0) KST → 미국 토·일 → 휴장
        if wd in (6, 0):
            return False

    return True


def seconds_to_us_market_open() -> float:
    """미국 장 시작까지 남은 초. 이미 장 중이면 0 반환."""
    now = datetime.now(KST)
    if is_us_market_hours(now):
        return 0.0
    open_today = now.replace(hour=22, minute=30, second=0, microsecond=0)
    if now < open_today:
        return (open_today - now).total_seconds()
    from datetime import timedelta
    open_tomorrow = open_today + timedelta(days=1)
    return (open_tomorrow - now).total_seconds()


# ── 설정 데이터클래스 ─────────────────────────────────────────────────────────

@dataclass
class OverseasStreamConfig:
    symbols: list[tuple[str, str]] = field(default_factory=lambda: list(DEFAULT_OVERSEAS_SYMBOLS))
    timeframes_minutes: tuple[int, ...] = (1, 5, 15)
    output_dir: str = ""
    reconnect_delay_s: float = 3.0
    max_reconnect_attempts: int = 20
    ping_interval_s: float = 30.0
    use_live_ws: bool = False
    # 동적 유니버스: True이면 미국 장 시작 전 KIS REST API로 거래대금 상위 종목 자동 선정
    auto_universe: bool = True
    universe_size: int = 30         # 자동 선정 종목 수

    @property
    def ws_url(self) -> str:
        port = 21000 if self.use_live_ws else 31000
        return f"ws://ops.koreainvestment.com:{port}"

    @property
    def resolved_output_dir(self) -> Path:
        import os
        base = os.getenv("DEEPSIGNAL_OUTPUT_DIR", str(Path(__file__).resolve().parents[3] / "output"))
        if self.output_dir:
            return Path(self.output_dir)
        return Path(base) / "kis_overseas"

    def symbol_ids(self) -> list[str]:
        return [f"{e}:{t}" for e, t in self.symbols]

    def tr_keys(self) -> list[str]:
        return [f"{e}{t}" for e, t in self.symbols]


# ── 파이프라인 ────────────────────────────────────────────────────────────────

class KisOverseasPipeline:
    """KIS 해외주식 실시간 데이터 파이프라인."""

    def __init__(
        self,
        cfg: OverseasStreamConfig,
        app_key: str,
        app_secret: str,
        rest_base_url: str,
    ) -> None:
        self.cfg = cfg
        self.app_key = app_key
        self.app_secret = app_secret
        self.rest_base_url = rest_base_url

        # 심볼 ID 목록: "NASD:NVDA" 형식
        self.symbol_ids: list[str] = cfg.symbol_ids()

        # 역방향 조회용: ticker → exchange (e.g. "NVDA" → "NASD")
        # 같은 티커가 여러 거래소에 있을 수 있으나 일반적으로 없음
        self._ticker_to_exchange: dict[str, str] = {
            ticker: exchange for exchange, ticker in cfg.symbols
        }

        self.aggregators: dict[str, KisOhlcvAggregator] = {}

        output_dir = cfg.resolved_output_dir
        self.persistence = KisStreamPersistence(
            output_dir=output_dir,
            max_recent_trades=500,
        )

        self.stats: dict[str, Any] = {
            "trades": 0,
            "bars_closed": 0,
            "messages": 0,
            "errors": 0,
            "start_time": None,
        }

        from deepsignal.market_data.kis_stream.feature_engine import StockFeatureEngine
        from deepsignal.market_data.kis_stream.signal_bridge import KStockSignalBridge
        self._feature_engine: Any = StockFeatureEngine()
        self._signal_bridge: Any = KStockSignalBridge(
            output_dir=cfg.resolved_output_dir,
            enable_telegram=True,
        )
        self._ws_client: KisWebSocketClient | None = None
        self._stop_event = asyncio.Event()
        self._warmed_up: bool = False
        # 유니버스 갱신 지원
        self._session_stop = asyncio.Event()
        self._refresh_universe_pending: list[tuple[str, str]] | None = None

    # ─────────────────────────────────────────────────────
    # 공개 API
    # ─────────────────────────────────────────────────────

    def prepare(self) -> list[str]:
        """어그리게이터 초기화."""
        self.aggregators = {
            sym_id: KisOhlcvAggregator(sym_id, timeframes_minutes=self.cfg.timeframes_minutes)
            for sym_id in self.symbol_ids
        }
        logger.info("KIS 해외주식 파이프라인 준비: %d 심볼", len(self.symbol_ids))
        return self.symbol_ids

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws_client:
            self._ws_client.stop()

    async def run(self) -> None:
        """파이프라인 메인 루프 (미국 장 시간 대기 + WebSocket)."""
        self.prepare()
        self.stats["start_time"] = time.time()

        while not self._stop_event.is_set():
            # 미국 장 운영시간 확인
            wait_s = seconds_to_us_market_open()
            if wait_s > 0:
                logger.info(
                    "미국 장 시작까지 %.0f분 대기 (현재: %s KST)",
                    wait_s / 60,
                    datetime.now(KST).strftime("%H:%M:%S"),
                )
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._stop_event.wait()),
                        timeout=min(wait_s, 300),
                    )
                except asyncio.TimeoutError:
                    pass
                continue

            # 자동 유니버스 — 미국 장 시작 전 1회 (세션 시작 시 갱신)
            if not self._warmed_up:
                if self.cfg.auto_universe:
                    try:
                        new_syms = await self._fetch_overseas_universe()
                        if new_syms:
                            self.cfg.symbols = new_syms
                            self.symbol_ids = self.cfg.symbol_ids()
                            self._ticker_to_exchange = {
                                ticker: exchange for exchange, ticker in self.cfg.symbols
                            }
                            self.aggregators = {
                                sym_id: KisOhlcvAggregator(sym_id, self.cfg.timeframes_minutes)
                                for sym_id in self.symbol_ids
                            }
                            logger.info(
                                "해외 유니버스 자동 선정: %d종목 — %s ...",
                                len(self.symbol_ids),
                                ", ".join(self.symbol_ids[:5]),
                            )
                    except Exception as exc:
                        logger.warning("해외 유니버스 선정 실패 (기본 목록 사용): %s", exc)
                self._warmed_up = True

            # Approval Key 취득
            try:
                approval_key = await get_approval_key(
                    self.rest_base_url,
                    self.app_key,
                    self.app_secret,
                )
            except Exception as exc:
                logger.error("Approval Key 취득 실패: %s — 30초 후 재시도", exc)
                await asyncio.sleep(30)
                continue

            # WebSocket 실행 (해외주식은 호가 구독 없음)
            # tr_keys: "NASDNVDA" 형식 문자열 목록
            tr_keys = self.cfg.tr_keys()
            self._ws_client = _OverseasWebSocketClient(
                ws_url=self.cfg.ws_url,
                approval_key=approval_key,
                tr_keys=tr_keys,
                on_message=self._handle_message,
                reconnect_delay_s=self.cfg.reconnect_delay_s,
                max_reconnect_attempts=self.cfg.max_reconnect_attempts,
                ping_interval_s=self.cfg.ping_interval_s,
            )

            # 장 마감 감시 + 유니버스 갱신 감시 + WS 동시 실행
            self._session_stop.clear()
            await asyncio.gather(
                self._ws_client.run(),
                self._market_close_watcher(),
                self._universe_refresh_watcher(),
            )

            if self._refresh_universe_pending is not None:
                # 유니버스 갱신으로 인한 세션 재시작 — 기존 어그리게이터 최대 보존
                new_syms = self._refresh_universe_pending
                self._refresh_universe_pending = None
                old_set = set(self.cfg.symbol_ids())
                self.cfg.symbols = new_syms
                self.symbol_ids = self.cfg.symbol_ids()
                self._ticker_to_exchange = {
                    ticker: exchange for exchange, ticker in self.cfg.symbols
                }
                new_set = set(self.symbol_ids)
                added   = [s for s in self.symbol_ids if s not in old_set]
                removed = [s for s in old_set if s not in new_set]
                for sym in removed:
                    self.aggregators.pop(sym, None)
                for sym in added:
                    self.aggregators[sym] = KisOhlcvAggregator(sym, self.cfg.timeframes_minutes)
                logger.info(
                    "해외 유니버스 갱신 후 WS 재연결: 심볼 %d개 (추가 %d, 제거 %d)",
                    len(self.symbol_ids), len(added), len(removed),
                )
                # _warmed_up 유지 → 다음 루프에서 유니버스 재조회 스킵
            else:
                # 장 마감 후 어그리게이터 초기화 (다음 날 다시 시작)
                for agg in self.aggregators.values():
                    agg.reset()
                self._warmed_up = False
                logger.info("미국 장 마감 — 어그리게이터 초기화 완료")

    # ─────────────────────────────────────────────────────
    # 내부 메서드
    # ─────────────────────────────────────────────────────

    async def _universe_refresh_watcher(self) -> None:
        """30분마다 해외 유니버스 갱신 확인 (auto_universe=True 시에만)."""
        if not self.cfg.auto_universe:
            return

        _REFRESH_INTERVAL_S = 1800  # 30분
        _CHANGE_THRESHOLD   = 0.20  # 20% 이상 변경 시 재시작

        # 첫 갱신은 30분 후
        try:
            await asyncio.wait_for(
                asyncio.shield(self._session_stop.wait()),
                timeout=_REFRESH_INTERVAL_S,
            )
        except asyncio.TimeoutError:
            pass
        if self._session_stop.is_set():
            return

        while not self._stop_event.is_set() and not self._session_stop.is_set():
            if not is_us_market_hours():
                return
            try:
                new_syms = await self._fetch_overseas_universe()
                if new_syms:
                    cur_set = set(self.cfg.symbol_ids())
                    new_ids = {f"{e}:{t}" for e, t in new_syms}
                    union   = cur_set | new_ids
                    changed = len(cur_set ^ new_ids)
                    ratio   = changed / max(len(union), 1)
                    if ratio >= _CHANGE_THRESHOLD:
                        logger.info(
                            "해외 유니버스 변경 감지 (%.1f%%) — WS 세션 재시작",
                            ratio * 100,
                        )
                        self._refresh_universe_pending = new_syms
                        self._session_stop.set()
                        if self._ws_client:
                            self._ws_client.stop()
                        return
                    else:
                        logger.debug(
                            "해외 유니버스 소폭 변경 (%.1f%%) — 갱신 스킵",
                            ratio * 100,
                        )
            except Exception as exc:
                logger.debug("해외 유니버스 주기 갱신 실패 (무시): %s", exc)

            try:
                await asyncio.wait_for(
                    asyncio.shield(self._session_stop.wait()),
                    timeout=_REFRESH_INTERVAL_S,
                )
            except asyncio.TimeoutError:
                pass

    async def _fetch_overseas_universe(self) -> list[tuple[str, str]]:
        """KIS REST API(HHDFS76410000)로 해외 거래량 상위 종목 조회.

        미국 3대 거래소(NASD/NYSE/AMEX)를 순회하여 거래량 상위 종목을 취합하고
        필수 포함 심볼(레버리지 ETF 등)과 합산하여 반환합니다.

        실패 시 DEFAULT_OVERSEAS_SYMBOLS를 반환합니다.
        """
        try:
            import aiohttp
        except ImportError:
            raise RuntimeError("aiohttp 패키지가 없습니다.")

        base = self.rest_base_url.rstrip("/")
        token_url = f"{base}/oauth2/tokenP"

        # 필수 포함 심볼 (레버리지 ETF + 대표 지수 ETF)
        MUST_INCLUDE: list[tuple[str, str]] = [
            ("AMEX", "SPY"), ("NASD", "QQQ"), ("NASD", "ONEQ"),
            ("NASD", "TQQQ"), ("NASD", "SQQQ"), ("NYSE", "SPXL"), ("NYSE", "SPXS"),
            ("NYSE", "SOXL"), ("NYSE", "SOXS"), ("NASD", "FNGU"), ("NASD", "FNGD"),
            ("NYSE", "LABU"), ("NYSE", "LABD"), ("NYSE", "UVXY"),
            ("NYSE", "TLT"), ("NYSE", "GLD"),
        ]
        must_set = {t for _, t in MUST_INCLUDE}

        # KIS REST exchange code → pipeline exchange code 매핑
        EXCD_MAP = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as sess:
                # 액세스 토큰 취득
                r = await sess.post(
                    token_url,
                    json={
                        "grant_type": "client_credentials",
                        "appkey": self.app_key,
                        "appsecret": self.app_secret,
                    },
                    headers={"content-type": "application/json"},
                )
                if r.status != 200:
                    raise RuntimeError(f"토큰 취득 실패 (HTTP {r.status})")
                token = (await r.json()).get("access_token")
                if not token:
                    raise RuntimeError("access_token 없음")

                headers = {
                    "authorization": f"Bearer {token}",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                    "tr_id": "HHDFS76410000",
                    "custtype": "P",
                    "content-type": "application/json; charset=utf-8",
                }
                rank_url = f"{base}/uapi/overseas-stock/v1/quotations/volume-rank"

                ranked_symbols: list[tuple[str, str]] = []
                seen: set[str] = set(must_set)

                for excd in ("NAS", "NYS", "AMS"):
                    try:
                        r2 = await sess.get(
                            rank_url,
                            headers=headers,
                            params={
                                "AUTH": "",
                                "EXCD": excd,
                                "KEYB": "",
                            },
                        )
                        if r2.status != 200:
                            continue
                        body = await r2.json()
                        output = body.get("output") or []
                        exchange = EXCD_MAP.get(excd, excd)
                        for row in output:
                            ticker = (
                                row.get("symb") or row.get("SYMB") or
                                row.get("ovrs_pdno") or row.get("OVRS_PDNO") or ""
                            ).strip().upper()
                            if not ticker or ticker in seen:
                                continue
                            seen.add(ticker)
                            ranked_symbols.append((exchange, ticker))
                    except Exception:
                        continue

            # 필수 심볼 + 거래량 순위 심볼을 universe_size 이내로 취합
            combined: list[tuple[str, str]] = list(MUST_INCLUDE)
            remaining = self.cfg.universe_size - len(combined)
            for sym in ranked_symbols:
                if remaining <= 0:
                    break
                if sym[1] not in {t for _, t in combined}:
                    combined.append(sym)
                    remaining -= 1

            if len(combined) >= len(MUST_INCLUDE):
                logger.info(
                    "해외 유니버스 API 조회 성공: %d종목 (필수 %d + 순위 %d)",
                    len(combined), len(MUST_INCLUDE), len(combined) - len(MUST_INCLUDE),
                )
                return combined
        except Exception as exc:
            logger.warning("해외 유니버스 API 조회 실패 → 기본 목록 사용: %s", exc)

        return list(DEFAULT_OVERSEAS_SYMBOLS)

    async def _market_close_watcher(self) -> None:
        """미국 장 마감(05:00 KST) 감지 시 WS 중단."""
        while not self._stop_event.is_set():
            # 30초마다 확인 — 유니버스 갱신 세션 중단 신호도 함께 감시
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._session_stop.wait()),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                pass
            if self._session_stop.is_set():
                break  # 유니버스 갱신 신호 → gather() 정리
            if not is_us_market_hours():
                logger.info("미국 장 마감 감지 — WebSocket 종료")
                if self._ws_client:
                    self._ws_client.stop()
                self._session_stop.set()
                break

    async def _handle_message(self, raw: str) -> None:
        """WebSocket 메시지 처리 진입점."""
        self.stats["messages"] = int(self.stats.get("messages", 0)) + 1
        try:
            # 제어 메시지(JSON) 무시
            if raw.strip().startswith("{"):
                return

            parts = raw.split("|", 3)
            if len(parts) < 4:
                return

            tr_id = parts[1]
            if tr_id != "HDFSCNT0":
                return

            data_part = parts[3]
            # 복수 건이 들어올 때 '^' 구분자로 분리 — KIS 해외주식은 건당 1레코드
            # 하지만 멀티 레코드의 경우를 대비해 count 처리
            count_str = parts[2]
            try:
                count = int(count_str)
            except ValueError:
                count = 1

            fields = data_part.split("^")
            # 단일 레코드 처리 (HDFSCNT0 필드 수: 10)
            n_fields = 10
            for i in range(count):
                start = i * n_fields
                rec = fields[start:start + n_fields]
                if len(rec) < n_fields:
                    break
                self._process_overseas_tick(rec)

        except Exception as exc:
            self.stats["errors"] = int(self.stats.get("errors", 0)) + 1
            logger.debug("해외 메시지 처리 오류: %s", exc)

    def _process_overseas_tick(self, fields: list[str]) -> None:
        """HDFSCNT0 필드 파싱 → KisTradeTick 생성 → 파이프라인 처리."""
        try:
            # [0] realtime_dvsn_cd
            # [1] mksc_shrn_iscd (ticker only, without exchange)
            # [2] stck_cntg_hour (HHMMSS KST)
            # [3] stck_prpr (current price)
            # [4] prdy_vrss_sign
            # [5] prdy_vrss (change)
            # [6] prdy_ctrt (change rate %)
            # [7] cntg_vol (tick volume)
            # [8] acml_vol (cumulative volume)
            # [9] acml_tr_pbmn (cumulative trade value)

            ticker = fields[1].strip()
            exchange = self._ticker_to_exchange.get(ticker)
            if exchange is None:
                # 알 수 없는 티커 — 무시
                return

            symbol_id = f"{exchange}:{ticker}"

            time_str = fields[2].strip()  # HHMMSS
            price_str = fields[3].strip()
            vol_str = fields[7].strip()
            acml_vol_str = fields[8].strip()
            acml_val_str = fields[9].strip()

            price = int(float(price_str)) if price_str else 0
            qty = int(float(vol_str)) if vol_str else 0
            acml_vol = int(float(acml_vol_str)) if acml_vol_str else 0
            acml_val = int(float(acml_val_str)) if acml_val_str else 0

            if price <= 0:
                return

            # 체결 시각 → epoch ms (KST)
            now_kst = datetime.now(KST)
            try:
                h = int(time_str[0:2])
                m = int(time_str[2:4])
                s = int(time_str[4:6])
                ts_dt = now_kst.replace(hour=h, minute=m, second=s, microsecond=0)
                ts_ms = int(ts_dt.timestamp() * 1000)
            except Exception:
                ts_ms = int(time_now_ms())

            tick = KisTradeTick(
                symbol=symbol_id,
                price=price,
                qty=qty,
                ts_ms=ts_ms,
                is_buyer=True,  # 해외 체결은 방향 정보 없음 — 기본 매수로 처리
                acml_vol=acml_vol,
                acml_val=acml_val,
            )

            self._on_trade(tick)

        except Exception as exc:
            logger.debug("해외 틱 파싱 오류: %s (fields=%s)", exc, fields[:5])

    def _on_trade(self, tick: KisTradeTick) -> None:
        self.stats["trades"] = int(self.stats.get("trades", 0)) + 1
        self.persistence.on_tick(tick)
        if self._feature_engine is not None:
            self._feature_engine.on_tick(tick)

        agg = self.aggregators.get(tick.symbol)
        if agg is None:
            agg = KisOhlcvAggregator(tick.symbol, self.cfg.timeframes_minutes)
            self.aggregators[tick.symbol] = agg

        closed_bars = agg.on_tick(tick)
        for bar in closed_bars:
            self._on_bar(bar)

    def _on_bar(self, bar: KisOhlcvBar) -> None:
        self.stats["bars_closed"] = int(self.stats.get("bars_closed", 0)) + 1
        self.persistence.on_bar(bar)

        if self._feature_engine is not None:
            try:
                self._feature_engine.on_bar(bar)
                if bar.timeframe == "1m":
                    self._try_score(bar.symbol)
            except Exception as exc:
                logger.debug("피처엔진 on_bar 오류 [%s %s]: %s", bar.symbol, bar.timeframe, exc)

        logger.debug(
            "봉 완성 [%s %s] O=%d H=%d L=%d C=%d V=%d",
            bar.symbol, bar.timeframe,
            bar.open, bar.high, bar.low, bar.close, bar.volume,
        )

    def _try_score(self, symbol: str) -> None:
        """피처 계산 → K-GSQS 채점 → 신호 로그."""
        if self._feature_engine is None:
            return
        from deepsignal.scoring.kstock_scorer import compute_kgsqs, THRESHOLD_NOTIFY
        try:
            features = self._feature_engine.build_features(symbol)
            if features is None:
                return
            signal = compute_kgsqs(features)
            if signal.hard_blocked:
                logger.debug("[%s] 하드블록: %s", symbol, signal.blocked_reason)
                return
            if signal.total_score >= THRESHOLD_NOTIFY:
                logger.info(
                    "K-GSQS 해외 신호 [%s] %.1f pt → %s  sub=%s",
                    symbol,
                    signal.total_score,
                    signal.action,
                    {k: f"{v:.0f}" for k, v in signal.sub_scores.items()},
                )
                if self._signal_bridge is not None:
                    self._signal_bridge.on_signal(signal, current_price=float(features.price))
            else:
                logger.debug("[%s] K-GSQS %.1f pt (%s)", symbol, signal.total_score, signal.action)

            # 사후 수익률 체크
            if self._signal_bridge is not None:
                current_prices = {
                    sym: float(state.last_tick.price)
                    for sym, state in self._feature_engine._states.items()
                    if state.last_tick is not None
                }
                completed = self._signal_bridge.check_outcomes(current_prices)
                if completed and completed > 0:
                    self._maybe_optimize_weights()
            else:
                self._maybe_optimize_weights()
        except Exception as exc:
            logger.debug("채점 오류 [%s]: %s", symbol, exc)

    def _maybe_optimize_weights(self) -> None:
        """K-GSQS 해외주식 가중치 자동최적화."""
        try:
            if not hasattr(self, "_kstock_weight_optimizer"):
                from deepsignal.scoring.kstock_weight_optimizer import KStockWeightOptimizer
                self._kstock_weight_optimizer = KStockWeightOptimizer(
                    self.persistence.output_dir,
                    horizon_minutes=5,
                    asset_label="해외",
                )
                try:
                    from deepsignal.scoring import kstock_scorer
                    saved_w = self._kstock_weight_optimizer.load_weights()
                    kstock_scorer.WEIGHTS.update(saved_w)
                    logger.debug("저장된 K-GSQS 해외 가중치 복원: %s", saved_w)
                except Exception as _we:
                    logger.debug("K-GSQS 해외 가중치 복원 실패 (무시): %s", _we)

            opt = self._kstock_weight_optimizer
            if opt.should_run():
                logger.info("K-GSQS 해외 가중치 최적화 실행 중...")
                opt_result = opt.run()
                if "error" not in opt_result:
                    logger.info(
                        "K-GSQS 해외 가중치 최적화 완료: 승률 %.1f%% → %.1f%% (개선 %.1f%%) applied=%s",
                        opt_result.get("default_win_rate", 0) * 100,
                        opt_result.get("expected_win_rate", 0) * 100,
                        opt_result.get("improvement", 0) * 100,
                        opt_result.get("applied", True),
                    )
                else:
                    logger.debug("K-GSQS 해외 가중치 최적화 스킵: %s", opt_result.get("error"))
        except Exception as exc:
            logger.debug("K-GSQS 해외 가중치 최적화 실패 (무시): %s", exc)

    def get_status(self) -> dict[str, Any]:
        """파이프라인 현재 상태 (웹 UI용)."""
        ws_stats = self._ws_client.stats if self._ws_client else {}
        return {
            "running": not self._stop_event.is_set(),
            "market_hours": is_us_market_hours(),
            "symbols": self.symbol_ids,
            "auto_universe": self.cfg.auto_universe,
            "universe_size": self.cfg.universe_size,
            "stats": {**self.stats, **ws_stats},
            "output_dir": str(self.persistence.output_dir),
        }


def time_now_ms() -> float:
    return time.time() * 1000


# ── 해외주식 전용 WebSocket 클라이언트 ────────────────────────────────────────

class _OverseasWebSocketClient(KisWebSocketClient):
    """HDFSCNT0 전용 WebSocket 클라이언트 (호가 구독 없음)."""

    def __init__(
        self,
        ws_url: str,
        approval_key: str,
        tr_keys: list[str],
        on_message: Any,
        reconnect_delay_s: float = 3.0,
        max_reconnect_attempts: int = 20,
        ping_interval_s: float = 30.0,
    ) -> None:
        # 부모 클래스는 symbols를 str 리스트로 받음
        # tr_keys는 "NASDNVDA" 형식 — 부모의 symbols 대신 사용
        super().__init__(
            ws_url=ws_url,
            approval_key=approval_key,
            symbols=tr_keys,  # tr_keys를 symbols로 전달
            on_message=on_message,
            subscribe_orderbook=False,
            subscribe_kospi=False,
            reconnect_delay_s=reconnect_delay_s,
            max_reconnect_attempts=max_reconnect_attempts,
            ping_interval_s=ping_interval_s,
        )

    async def _subscribe_all(self, ws: Any) -> None:
        """HDFSCNT0만 구독 (호가·지수 없음)."""
        for tr_key in self.symbols:
            await self._send_subscribe(ws, "HDFSCNT0", tr_key)
        logger.info("해외주식 구독 완료: %d 심볼 (체결만)", len(self.symbols))
