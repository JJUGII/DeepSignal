"""10-second order book snapshots → bars/{symbol}_ob.jsonl."""

from __future__ import annotations

import json
import time
from pathlib import Path

from deepsignal.market_data.binance_stream.models import OrderBookSnapshot


class OrderBookHistoryRecorder:
    """Append depth snapshots at a fixed wall-clock interval (default 10s)."""

    def __init__(
        self,
        bars_dir: Path,
        *,
        interval_seconds: float = 10.0,
    ) -> None:
        self.bars_dir = Path(bars_dir)
        self.bars_dir.mkdir(parents=True, exist_ok=True)
        self.interval_ms = int(max(1.0, float(interval_seconds)) * 1000)
        self._last_write_ms: dict[str, int] = {}
        self.snapshots_written: int = 0

    def ob_path(self, symbol: str) -> Path:
        return self.bars_dir / f"{symbol.upper()}_ob.jsonl"

    def maybe_record(self, book: OrderBookSnapshot, *, now_ms: int | None = None) -> bool:
        sym = book.symbol.upper()
        if not book.bids or not book.asks:
            return False
        ts_ms = int(now_ms if now_ms is not None else (book.ts_ms or int(time.time() * 1000)))
        last = self._last_write_ms.get(sym, 0)
        if last > 0 and (ts_ms - last) < self.interval_ms:
            return False

        row = {
            "ts": int(ts_ms // 1000),
            "ts_ms": ts_ms,
            "bids": [[float(p), float(q)] for p, q in book.bids[:20]],
            "asks": [[float(p), float(q)] for p, q in book.asks[:20]],
        }
        path = self.ob_path(sym)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._last_write_ms[sym] = ts_ms
        self.snapshots_written += 1
        return True
