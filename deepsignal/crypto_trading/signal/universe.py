"""Upbit KRW market universe — core (BTC/ETH/XRP) or all KRW markets with liquidity cap."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_market_data import DEFAULT_CRYPTO_MARKETS
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitTicker
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto

CORE_CRYPTO_MARKETS = DEFAULT_CRYPTO_MARKETS
UNIVERSE_CORE = "core"
UNIVERSE_ALL_KRW = "all_krw"

_MOCK_KRW_MARKETS: tuple[str, ...] = (
    "KRW-BTC",
    "KRW-ETH",
    "KRW-XRP",
    "KRW-SOL",
    "KRW-DOGE",
    "KRW-ADA",
    "KRW-AVAX",
    "KRW-LINK",
    "KRW-DOT",
    "KRW-ATOM",
)


@dataclass
class CryptoUniverseConfig:
    universe: str = _CRYPTO.market_universe
    max_buy_scan_markets: int = _CRYPTO.max_buy_scan_markets
    min_acc_trade_price_24h: float = _CRYPTO.min_acc_trade_price_24h
    ticker_batch_size: int = _CRYPTO.ticker_batch_size
    exclude_market_warning: bool = _CRYPTO.exclude_market_warning
    extra_markets: tuple[str, ...] = ()


def _normalized_universe(value: str | None) -> str:
    raw = str(value or UNIVERSE_ALL_KRW).strip().lower()
    if raw in {UNIVERSE_CORE, "btc_eth_xrp", "default", "3"}:
        return UNIVERSE_CORE
    return UNIVERSE_ALL_KRW


def market_display_name(market: str, *, korean_name: str | None = None) -> str:
    if korean_name and str(korean_name).strip():
        return str(korean_name).strip()
    from deepsignal.crypto_trading.crypto_recommendation import MARKET_DISPLAY_KO

    return MARKET_DISPLAY_KO.get(market.upper(), market.upper())


def _chunked(items: list[str], size: int) -> list[list[str]]:
    n = max(1, int(size))
    return [items[i : i + n] for i in range(0, len(items), n)]


def list_upbit_krw_markets(
    broker: UpbitBroker,
    *,
    is_details: bool = True,
    exclude_warning: bool = True,
) -> tuple[list[str], dict[str, str]]:
    """Fetch KRW-* markets from GET /market/all. Returns (markets, korean_name_by_market)."""
    if broker.config.dry_run and broker.config.access_key == "dry-run-key":
        names = {m: market_display_name(m) for m in _MOCK_KRW_MARKETS}
        return list(_MOCK_KRW_MARKETS), names

    params: dict[str, Any] = {}
    if is_details:
        params["is_details"] = "true"
    rows = broker._request("GET", "/market/all", params=params or None)
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected market/all response: {rows!r}")

    markets: list[str] = []
    names: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        market = str(row.get("market") or "").strip().upper()
        if not market.startswith("KRW-"):
            continue
        if exclude_warning and is_details:
            event = row.get("market_event")
            if isinstance(event, dict) and bool(event.get("warning")):
                continue
        markets.append(market)
        ko = str(row.get("korean_name") or "").strip()
        if ko:
            names[market] = ko
    markets.sort()
    return markets, names


_UPBIT_KRW_SET_CACHE: frozenset[str] | None = None
_UPBIT_KRW_SET_CACHE_AT: float = 0.0
UPBIT_KRW_MARKETS_CACHE_JSON = "UPBIT_KRW_MARKETS_CACHE.json"


def get_upbit_krw_market_set(
    broker: UpbitBroker,
    *,
    output_dir: str | Path | None = None,
    max_age_seconds: float = 3600.0,
) -> frozenset[str]:
    """Cached Upbit KRW market codes (filters Binance-only symbols from live_state)."""
    global _UPBIT_KRW_SET_CACHE, _UPBIT_KRW_SET_CACHE_AT
    if broker.config.dry_run and broker.config.access_key == "dry-run-key":
        return frozenset(_MOCK_KRW_MARKETS)

    now = time.time()
    if _UPBIT_KRW_SET_CACHE is not None and (now - _UPBIT_KRW_SET_CACHE_AT) < max_age_seconds:
        return _UPBIT_KRW_SET_CACHE

    if output_dir is not None:
        cache_path = Path(output_dir) / UPBIT_KRW_MARKETS_CACHE_JSON
        if cache_path.is_file():
            try:
                doc = json.loads(cache_path.read_text(encoding="utf-8"))
                ts = float(doc.get("cached_at_ts") or 0)
                markets = doc.get("markets") or []
                if (now - ts) < max_age_seconds and isinstance(markets, list):
                    codes = frozenset(str(m).upper() for m in markets if str(m).strip())
                    if codes:
                        _UPBIT_KRW_SET_CACHE = codes
                        _UPBIT_KRW_SET_CACHE_AT = now
                        return codes
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass

    markets, _ = list_upbit_krw_markets(broker, exclude_warning=True)
    codes = frozenset(markets)
    _UPBIT_KRW_SET_CACHE = codes
    _UPBIT_KRW_SET_CACHE_AT = now
    if output_dir is not None:
        cache_path = Path(output_dir) / UPBIT_KRW_MARKETS_CACHE_JSON
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {"cached_at_ts": now, "markets": sorted(codes)},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    return codes


def filter_to_upbit_markets(markets: list[str] | tuple[str, ...], valid: frozenset[str]) -> list[str]:
    return [m.upper() for m in markets if str(m).upper() in valid]


def fetch_tickers_batched(
    broker: UpbitBroker,
    markets: list[str],
    *,
    batch_size: int = 100,
    valid_markets: frozenset[str] | None = None,
) -> dict[str, UpbitTicker]:
    normalized = [m.strip().upper() for m in markets if m and m.strip()]
    if valid_markets is not None:
        normalized = [m for m in normalized if m in valid_markets]
    out: dict[str, UpbitTicker] = {}
    for chunk in _chunked(normalized, batch_size):
        if chunk:
            out.update(broker.get_tickers(chunk))
    return out


def select_markets_for_buy_scan(
    ticker_map: dict[str, UpbitTicker],
    *,
    min_acc_trade_price_24h: float,
    max_markets: int,
    always_include: tuple[str, ...] = CORE_CRYPTO_MARKETS,
) -> list[str]:
    """Keep holdings/core symbols, then top 24h KRW volume up to max_markets."""
    max_n = max(1, int(max_markets))
    ranked: list[tuple[str, float]] = []
    for market, ticker in ticker_map.items():
        acc = float(ticker.acc_trade_price_24h or 0)
        if acc >= float(min_acc_trade_price_24h):
            ranked.append((market, acc))
    ranked.sort(key=lambda row: row[1], reverse=True)

    selected: list[str] = []
    seen: set[str] = set()
    for m in always_include:
        mu = m.upper()
        if mu in ticker_map and mu not in seen:
            selected.append(mu)
            seen.add(mu)
    for market, _acc in ranked:
        if market in seen:
            continue
        selected.append(market)
        seen.add(market)
        if len(selected) >= max_n:
            break
    return selected


@dataclass
class CryptoUniverseResult:
    markets: tuple[str, ...]
    universe: str
    total_krw_markets: int
    scanned_for_buy: int
    display_names: dict[str, str]
    min_acc_trade_price_24h: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "total_krw_markets": self.total_krw_markets,
            "scanned_for_buy": self.scanned_for_buy,
            "markets": list(self.markets),
            "min_acc_trade_price_24h": self.min_acc_trade_price_24h,
        }


def resolve_crypto_markets(
    broker: UpbitBroker,
    *,
    config: CryptoUniverseConfig | None = None,
    holdings_markets: tuple[str, ...] | None = None,
) -> CryptoUniverseResult:
    """Resolve markets for BUY scan / diagnostics."""
    cfg = config or CryptoUniverseConfig()
    universe = _normalized_universe(cfg.universe)
    display: dict[str, str] = {}

    if universe == UNIVERSE_CORE:
        markets = list(CORE_CRYPTO_MARKETS)
        for m in cfg.extra_markets:
            mu = m.strip().upper()
            if mu and mu not in markets:
                markets.append(mu)
        if holdings_markets:
            for m in holdings_markets:
                mu = m.strip().upper()
                if mu and mu not in markets:
                    markets.append(mu)
        for m in markets:
            display[m] = market_display_name(m)
        return CryptoUniverseResult(
            markets=tuple(markets),
            universe=universe,
            total_krw_markets=len(markets),
            scanned_for_buy=len(markets),
            display_names=display,
            min_acc_trade_price_24h=float(cfg.min_acc_trade_price_24h),
        )

    all_markets, name_map = list_upbit_krw_markets(
        broker,
        exclude_warning=bool(cfg.exclude_market_warning),
    )
    display.update(name_map)
    ticker_map = fetch_tickers_batched(
        broker,
        all_markets,
        batch_size=int(cfg.ticker_batch_size),
    )
    hold = tuple(h.strip().upper() for h in (holdings_markets or ()) if h and h.strip())
    always = tuple(dict.fromkeys((*CORE_CRYPTO_MARKETS, *cfg.extra_markets, *hold)))
    selected = select_markets_for_buy_scan(
        ticker_map,
        min_acc_trade_price_24h=float(cfg.min_acc_trade_price_24h),
        max_markets=int(cfg.max_buy_scan_markets),
        always_include=always,
    )
    for m in selected:
        if m not in display:
            display[m] = market_display_name(m, korean_name=name_map.get(m))

    return CryptoUniverseResult(
        markets=tuple(selected),
        universe=universe,
        total_krw_markets=len(all_markets),
        scanned_for_buy=len(selected),
        display_names=display,
        min_acc_trade_price_24h=float(cfg.min_acc_trade_price_24h),
    )


def parse_extra_markets(raw: str | None) -> tuple[str, ...]:
    if not raw or not str(raw).strip():
        return ()
    out: list[str] = []
    for part in str(raw).replace(" ", "").split(","):
        if not part:
            continue
        mu = part.upper()
        if not mu.startswith("KRW-"):
            mu = f"KRW-{mu}"
        out.append(mu)
    return tuple(dict.fromkeys(out))


def crypto_universe_config_from_args(
    args: Any,
    *,
    extra_markets: tuple[str, ...] | None = None,
) -> CryptoUniverseConfig:
    extra = extra_markets if extra_markets is not None else parse_extra_markets(
        getattr(args, "crypto_markets", None)
    )
    return CryptoUniverseConfig(
        universe=str(getattr(args, "crypto_universe", _CRYPTO.market_universe) or _CRYPTO.market_universe),
        max_buy_scan_markets=int(getattr(args, "max_scan_markets", _CRYPTO.max_buy_scan_markets) or _CRYPTO.max_buy_scan_markets),
        min_acc_trade_price_24h=float(
            getattr(args, "min_acc_trade_24h", _CRYPTO.min_acc_trade_price_24h) or _CRYPTO.min_acc_trade_price_24h
        ),
        ticker_batch_size=int(getattr(args, "ticker_batch_size", _CRYPTO.ticker_batch_size) or _CRYPTO.ticker_batch_size),
        extra_markets=extra,
    )


def add_crypto_universe_cli_args(parser: Any, *, default_universe: str = UNIVERSE_ALL_KRW) -> None:
    parser.add_argument(
        "--crypto-universe",
        type=str,
        default=default_universe,
        choices=[UNIVERSE_CORE, UNIVERSE_ALL_KRW],
        help="core=BTC/ETH/XRP only, all_krw=Upbit KRW 전 종목(유동성 상위 N개 스캔)",
    )
    parser.add_argument(
        "--max-scan-markets",
        type=int,
        default=int(_CRYPTO.max_buy_scan_markets),
        metavar="N",
        help="all_krw일 때 RSI/ATR 정밀 스캔 최대 종목 수 (기본 80)",
    )
    parser.add_argument(
        "--crypto-markets",
        type=str,
        default="",
        metavar="LIST",
        help="쉼표 구분 마켓 고정 (예: KRW-BTC,KRW-SOL). 지정 시 universe 대신 이 목록만 사용",
    )


def save_crypto_universe_snapshot(output_dir: str | Path, result: CryptoUniverseResult) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "CRYPTO_UNIVERSE_SNAPSHOT.json"
    payload = result.to_dict()
    payload["display_names"] = dict(result.display_names)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
