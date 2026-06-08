"""KIS 실시간 스트림 데이터 모델."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class KisTradeTick:
    """H0STCNT0 체결 틱."""

    symbol: str          # 종목코드 (6자리)
    price: int           # 체결가 (원)
    qty: int             # 체결거래량
    ts_ms: int           # 체결시각 (epoch ms, KST)
    is_buyer: bool       # True = 매수체결 (CCLS_DVSN == "1")
    ask_price: int = 0   # 매도1호가
    bid_price: int = 0   # 매수1호가
    acml_vol: int = 0    # 누적거래량
    acml_val: int = 0    # 누적거래대금 (원)
    open_price: int = 0  # 시가
    high_price: int = 0  # 고가
    low_price: int = 0   # 저가
    strength: float = 0.0  # 체결강도

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KisOrderBookLevel:
    price: int
    qty: int


@dataclass
class KisOrderBookSnapshot:
    """H0STASP0 호가 스냅샷 (최우선 5단계)."""

    symbol: str
    ts_ms: int
    asks: list[KisOrderBookLevel] = field(default_factory=list)  # 매도호가 (낮→높)
    bids: list[KisOrderBookLevel] = field(default_factory=list)  # 매수호가 (높→낮)
    total_ask_qty: int = 0  # 총 매도호가 잔량
    total_bid_qty: int = 0  # 총 매수호가 잔량

    @property
    def best_ask(self) -> int | None:
        return self.asks[0].price if self.asks else None

    @property
    def best_bid(self) -> int | None:
        return self.bids[0].price if self.bids else None

    @property
    def spread_bps(self) -> float | None:
        ba, bb = self.best_ask, self.best_bid
        if ba and bb and bb > 0:
            mid = (ba + bb) / 2.0
            return (ba - bb) / mid * 10_000.0
        return None

    @property
    def bid_ask_ratio(self) -> float | None:
        """총 매수잔량 / 총 매도잔량."""
        if self.total_ask_qty > 0:
            return self.total_bid_qty / self.total_ask_qty
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ts_ms": self.ts_ms,
            "asks": [[l.price, l.qty] for l in self.asks],
            "bids": [[l.price, l.qty] for l in self.bids],
            "total_ask_qty": self.total_ask_qty,
            "total_bid_qty": self.total_bid_qty,
            "best_ask": self.best_ask,
            "best_bid": self.best_bid,
            "spread_bps": self.spread_bps,
            "bid_ask_ratio": self.bid_ask_ratio,
        }


@dataclass
class KisOhlcvBar:
    """KIS 국내주식 OHLCV 봉."""

    symbol: str
    timeframe: str     # "1m", "5m", "15m"
    open_ts_ms: int    # 봉 시작 epoch ms (KST 기준)
    open: int
    high: int
    low: int
    close: int
    volume: int        # 거래량 (주)
    trade_value: int   # 거래대금 (원)
    trade_count: int   # 체결건수
    buy_ratio: float = 0.0   # 매수비율 (매수건수 / 전체건수)
    closed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KisOhlcvBar":
        return cls(
            symbol=str(d["symbol"]),
            timeframe=str(d["timeframe"]),
            open_ts_ms=int(d["open_ts_ms"]),
            open=int(d["open"]),
            high=int(d["high"]),
            low=int(d["low"]),
            close=int(d["close"]),
            volume=int(d["volume"]),
            trade_value=int(d.get("trade_value", 0)),
            trade_count=int(d.get("trade_count", 0)),
            buy_ratio=float(d.get("buy_ratio", 0.0)),
            closed=bool(d.get("closed", True)),
        )
