"""Persist stream snapshots and closed OHLCV bars."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepsignal.live_trading.time_utils import now_kst_iso
from deepsignal.market_data.binance_stream.models import (
    FundingSnapshot,
    OhlcvBar,
    OrderBookSnapshot,
    TradeTick,
)


@dataclass
class StreamPersistence:
    output_dir: Path
    max_recent_trades: int = 500
    _recent_trades: dict[str, deque[dict[str, Any]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "bars").mkdir(parents=True, exist_ok=True)

    def append_closed_bar(self, bar: OhlcvBar) -> Path:
        path = self.output_dir / "bars" / f"{bar.symbol}_{bar.timeframe}.jsonl"
        line = json.dumps(bar.to_dict(), ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return path

    def record_trade(self, tick: TradeTick) -> None:
        dq = self._recent_trades.setdefault(tick.symbol, deque(maxlen=self.max_recent_trades))
        dq.append(tick.to_dict())

    def write_live_state(
        self,
        *,
        symbols: list[str],
        orderbooks: dict[str, OrderBookSnapshot],
        funding: dict[str, FundingSnapshot],
        open_interest: dict[str, Any] | None = None,
        open_bars: list[OhlcvBar],
        btc: TradeTick | None,
        stats: dict[str, Any],
    ) -> Path:
        path = self.output_dir / "live_state.json"
        payload = {
            "generated_at": now_kst_iso(),
            "symbols": symbols,
            "btc": btc.to_dict() if btc else None,
            "stats": stats,
            "orderbooks": {k: v.to_dict() for k, v in orderbooks.items()},
            "funding": {k: v.to_dict() for k, v in funding.items()},
            "open_interest": open_interest or {},
            "open_bars": [b.to_dict() for b in open_bars],
            "recent_trades": {sym: list(dq) for sym, dq in self._recent_trades.items()},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
