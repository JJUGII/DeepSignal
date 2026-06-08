"""KIS 실시간 스트림 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _PROJECT_ROOT / ".env"

# 기본 감시 종목 (코스피 대형주 + 코스닥 대표)
DEFAULT_SYMBOLS = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "005380",  # 현대차
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "035720",  # 카카오
    "207940",  # 삼성바이오로직스
    "068270",  # 셀트리온
    "096770",  # SK이노베이션
]

# 장 운영시간 (KST)
MARKET_OPEN_HOUR, MARKET_OPEN_MIN = 9, 5
MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN = 15, 15


@dataclass
class KisStreamConfig:
    symbols: list[str] = field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    output_dir: str = ""          # 비어있으면 환경변수/기본값 사용
    timeframes_minutes: tuple[int, ...] = (1, 5, 15)
    max_trades_buffered: int = 1000
    reconnect_delay_s: float = 3.0
    max_reconnect_attempts: int = 20
    ping_interval_s: float = 30.0  # KIS PINGPONG 주기
    use_live_ws: bool = False       # True = 실전 WS(21000), False = 모의(31000)
    # 호가 구독 여부
    subscribe_orderbook: bool = True
    # 자동 유니버스: True이면 장 시작 전 KIS REST API로 거래대금 상위 N개 종목 자동 선정
    auto_universe: bool = True
    universe_size: int = 30        # 자동 선정 종목 수 (KIS WS 구독 한도 ~40)
    universe_market: str = "J"     # J=KOSPI, Q=KOSDAQ, NQ=KOSPI+KOSDAQ

    @property
    def ws_url(self) -> str:
        # KIS WebSocket은 TLS 없는 plain ws:// 사용
        port = 21000 if self.use_live_ws else 31000
        return f"ws://ops.koreainvestment.com:{port}"

    @property
    def resolved_output_dir(self) -> Path:
        if self.output_dir:
            return Path(self.output_dir)
        base = os.getenv("DEEPSIGNAL_OUTPUT_DIR", str(_PROJECT_ROOT / "output"))
        return Path(base) / "kis_stream"


def load_kis_stream_config_from_env(
    *,
    load_dotenv_file: bool = True,
    symbols: list[str] | None = None,
    output_dir: str = "",
) -> KisStreamConfig:
    if load_dotenv_file:
        load_dotenv(dotenv_path=_ENV_FILE)

    kis_env = os.getenv("KIS_ENV", "paper").strip().lower()
    use_live_ws = kis_env == "live"

    raw_syms = os.getenv("KIS_STREAM_SYMBOLS", "")
    if symbols:
        sym_list = [s.strip() for s in symbols if s.strip()]
    elif raw_syms.strip():
        sym_list = [s.strip() for s in raw_syms.split(",") if s.strip()]
    else:
        sym_list = list(DEFAULT_SYMBOLS)

    auto_universe_env = os.getenv("KIS_STREAM_AUTO_UNIVERSE", "true").strip().lower()
    auto_universe = auto_universe_env in ("1", "true", "yes") and not symbols and not raw_syms.strip()

    universe_size = int(os.getenv("KIS_STREAM_UNIVERSE_SIZE", "30"))
    universe_market = os.getenv("KIS_STREAM_UNIVERSE_MARKET", "J")  # J=KOSPI

    return KisStreamConfig(
        symbols=sym_list,
        output_dir=output_dir,
        use_live_ws=use_live_ws,
        auto_universe=auto_universe,
        universe_size=universe_size,
        universe_market=universe_market,
    )
