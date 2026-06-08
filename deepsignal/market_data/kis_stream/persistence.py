"""KIS 스트림 데이터 영속화 — JSONL 파일 저장."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from deepsignal.market_data.kis_stream.models import (
    KisOhlcvBar,
    KisOrderBookSnapshot,
    KisTradeTick,
)

logger = logging.getLogger(__name__)


class KisStreamPersistence:
    """틱/봉/호가 데이터를 JSONL 파일로 저장."""

    def __init__(self, output_dir: Path, max_recent_trades: int = 500) -> None:
        self.output_dir = output_dir
        self.max_recent_trades = max_recent_trades
        self._recent_trades: dict[str, list[dict]] = {}
        self._recent_obs: dict[str, dict] = {}
        self._last_flush = 0.0
        self._flush_interval = 5.0  # 초
        self._bars_dir = output_dir / "bars"
        self._ticks_dir = output_dir / "ticks"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self._bars_dir.mkdir(parents=True, exist_ok=True)
        self._ticks_dir.mkdir(parents=True, exist_ok=True)

    def on_tick(self, tick: KisTradeTick) -> None:
        sym = tick.symbol
        buf = self._recent_trades.setdefault(sym, [])
        buf.append(tick.to_dict())
        if len(buf) > self.max_recent_trades:
            buf.pop(0)

        now = time.monotonic()
        if now - self._last_flush > self._flush_interval:
            self._flush_ticks()
            self._last_flush = now

    def on_bar(self, bar: KisOhlcvBar) -> None:
        if not bar.closed:
            return
        path = self._bars_dir / f"{bar.symbol}_{bar.timeframe}.jsonl"
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(bar.to_dict(), ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("봉 저장 실패 [%s %s]: %s", bar.symbol, bar.timeframe, exc)

    def on_orderbook(self, ob: KisOrderBookSnapshot) -> None:
        self._recent_obs[ob.symbol] = ob.to_dict()

    def get_recent_trades(self, symbol: str) -> list[dict]:
        return list(self._recent_trades.get(symbol, []))

    def get_recent_orderbook(self, symbol: str) -> dict | None:
        return self._recent_obs.get(symbol)

    def get_all_symbols_state(self) -> dict:
        return {
            sym: {
                "trade_count": len(self._recent_trades.get(sym, [])),
                "latest_trade": self._recent_trades[sym][-1] if self._recent_trades.get(sym) else None,
                "orderbook": self._recent_obs.get(sym),
            }
            for sym in set(list(self._recent_trades.keys()) + list(self._recent_obs.keys()))
        }

    def _flush_ticks(self) -> None:
        for sym, buf in self._recent_trades.items():
            if not buf:
                continue
            path = self._ticks_dir / f"{sym}_recent.jsonl"
            try:
                with path.open("w", encoding="utf-8") as f:
                    for row in buf[-200:]:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except Exception:
                pass
