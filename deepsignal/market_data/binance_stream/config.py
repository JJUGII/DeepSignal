"""Configuration for Binance WebSocket market stream."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BinanceStreamConfig:
    output_dir: str = "outputs/binance_stream"
    top_n: int = 30
    quote_asset: str = "USDT"
    symbols: tuple[str, ...] = ()
    depth_levels: int = 20
    timeframes_minutes: tuple[int, ...] = (1, 3, 15)
    btc_symbol: str = "BTCUSDT"
    include_funding: bool = True
    spot_ws_base: str = "wss://stream.binance.com:9443"
    futures_ws_base: str = "wss://fstream.binance.com"
    rest_base: str = "https://api.binance.com"
    state_flush_seconds: float = 5.0
    max_trades_buffered: int = 500
    ob_snapshot_seconds: float = 10.0
    stablecoin_bases: tuple[str, ...] = ("USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI")

    def resolved_symbols(self, fetched: list[str]) -> list[str]:
        if self.symbols:
            out = [s.upper() for s in self.symbols]
        else:
            out = [s.upper() for s in fetched[: self.top_n]]
        seen: set[str] = set()
        deduped: list[str] = []
        btc = self.btc_symbol.upper()
        if btc not in out:
            out.insert(0, btc)
        for sym in out:
            if sym not in seen:
                seen.add(sym)
                deduped.append(sym)
        if btc in seen:
            deduped = [btc] + [s for s in deduped if s != btc]
        return deduped
