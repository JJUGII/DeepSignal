"""김치프리미엄(Kimchi Premium) 계산 모듈.

premium_pct = (Upbit KRW 가격 / (Binance USDT 가격 × USD/KRW 환율) - 1) × 100

외부 의존: 공개 REST API (인증 불필요).
  - Upbit  : https://api.upbit.com/v1/ticker
  - Binance: https://api.binance.com/api/v3/ticker/price
  - 환율    : https://quotation-api-cdn.dunamu.com (Upbit 계열 Dunamu)
              fallback → exchangerate-api.com

캐시: USD/KRW 5분, 가격 쌍 60초.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

_LOG = logging.getLogger(__name__)

# ── 캐시 ─────────────────────────────────────────────────────────────────────
_FX_CACHE: tuple[float, float] | None = None   # (rate, ts)
_FX_TTL = 300                                   # 5분

_PRICE_CACHE: dict[str, tuple[dict, float]] = {}  # symbol → (data, ts)
_PRICE_TTL = 60                                    # 60초

# ── 기본 심볼 ──────────────────────────────────────────────────────────────────
DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "ADA"]

# USD/KRW 폴백 (API 전체 실패 시)
_FX_FALLBACK = 1_380.0

# 김치프리미엄 수준 분류 임계값
LEVEL_LOW    = 2.0   # % 미만 → 정상
LEVEL_MEDIUM = 5.0   # % 미만 → 주의
LEVEL_HIGH   = 8.0   # % 이상 → 경고


@dataclass
class KimchiPremium:
    symbol: str           # e.g. "BTC"
    upbit_krw: float      # Upbit 시세 (KRW)
    binance_usdt: float   # Binance 시세 (USDT)
    usd_krw_rate: float   # USD/KRW 환율
    fair_krw: float       # binance_usdt × usd_krw_rate
    premium_pct: float    # (upbit_krw / fair_krw − 1) × 100
    ts: float = field(default_factory=time.time)

    @property
    def level(self) -> str:
        """"low" | "medium" | "high" | "very_high" """
        p = self.premium_pct
        if p < LEVEL_LOW:
            return "low"
        if p < LEVEL_MEDIUM:
            return "medium"
        if p < LEVEL_HIGH:
            return "high"
        return "very_high"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "upbit_krw": self.upbit_krw,
            "binance_usdt": self.binance_usdt,
            "usd_krw_rate": self.usd_krw_rate,
            "fair_krw": round(self.fair_krw, 0),
            "premium_pct": round(self.premium_pct, 2),
            "level": self.level,
            "ts": self.ts,
        }


# ── 내부 HTTP 헬퍼 ────────────────────────────────────────────────────────────

def _fetch_json(url: str, timeout: float = 5.0) -> object:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "DeepSignal/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ── USD/KRW 환율 ──────────────────────────────────────────────────────────────

def get_usd_krw_rate() -> float:
    """USD/KRW 환율. 5분 캐시. 실패 시 폴백 1380."""
    global _FX_CACHE
    now = time.time()
    if _FX_CACHE and now - _FX_CACHE[1] < _FX_TTL:
        return _FX_CACHE[0]

    rate = _fetch_usd_krw_dunamu() or _fetch_usd_krw_exchangerate() or _FX_FALLBACK
    _FX_CACHE = (rate, now)
    _LOG.debug("[KimchiPremium] USD/KRW=%.1f", rate)
    return rate


def _fetch_usd_krw_dunamu() -> float | None:
    """Dunamu(Upbit 계열) 외환 API."""
    try:
        data = _fetch_json(
            "https://quotation-api-cdn.dunamu.com/v1/forex/recent?codes=FRX.KRWUSD"
        )
        if isinstance(data, list) and data:
            return float(data[0]["basePrice"])
    except Exception as e:
        _LOG.debug("[KimchiPremium] Dunamu FX 실패: %s", e)
    return None


def _fetch_usd_krw_exchangerate() -> float | None:
    """ExchangeRate-API.com 무인증 폴백."""
    try:
        data = _fetch_json("https://api.exchangerate-api.com/v4/latest/USD")
        return float(data["rates"]["KRW"])
    except Exception as e:
        _LOG.debug("[KimchiPremium] ExchangeRate API 실패: %s", e)
    return None


# ── 가격 조회 ─────────────────────────────────────────────────────────────────

def get_upbit_prices(symbols: list[str]) -> dict[str, float]:
    """Upbit KRW 시세 조회. {symbol: krw_price}. 실패 심볼 제외."""
    markets = ",".join(f"KRW-{s}" for s in symbols)
    try:
        data = _fetch_json(
            f"https://api.upbit.com/v1/ticker?markets={markets}"
        )
        result: dict[str, float] = {}
        for item in data:
            sym = item["market"].replace("KRW-", "")
            result[sym] = float(item["trade_price"])
        return result
    except Exception as e:
        _LOG.warning("[KimchiPremium] Upbit 가격 조회 실패: %s", e)
        return {}


def get_binance_prices(symbols: list[str]) -> dict[str, float]:
    """Binance USDT 시세 조회. {symbol: usdt_price}. 실패 심볼 제외."""
    # 먼저 로컬 live_state.json 에서 BTC 가격 시도
    local = _read_local_binance_prices()
    # 로컬에 없는 심볼만 REST 호출
    missing = [s for s in symbols if s not in local]

    if missing:
        remote = _fetch_binance_rest(missing)
        local.update(remote)
    return {s: local[s] for s in symbols if s in local}


def _read_local_binance_prices() -> dict[str, float]:
    """binance_stream/live_state.json 에서 최근 가격 추출."""
    try:
        import os
        # PROJECT_ROOT 탐색 (deepsignal 패키지 상위)
        _this = os.path.dirname(__file__)
        _proj = os.path.normpath(os.path.join(_this, "..", "..", ".."))
        path = os.path.join(_proj, "outputs", "binance_stream", "live_state.json")
        if not os.path.exists(path):
            return {}
        with open(path) as f:
            d = json.load(f)
        result: dict[str, float] = {}
        # btc 필드: {"symbol": "BTCUSDT", "price": 74068.07, ...}
        btc_trade = d.get("btc")
        if isinstance(btc_trade, dict) and btc_trade.get("symbol") == "BTCUSDT":
            result["BTC"] = float(btc_trade["price"])
        return result
    except Exception:
        return {}


def _fetch_binance_rest(symbols: list[str]) -> dict[str, float]:
    """Binance 공개 REST API로 USDT 가격 조회."""
    result: dict[str, float] = {}
    for sym in symbols:
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={sym}USDT"
            data = _fetch_json(url)
            if isinstance(data, dict) and "price" in data:
                result[sym] = float(data["price"])
        except Exception as e:
            _LOG.debug("[KimchiPremium] Binance %sUSDT 실패: %s", sym, e)
    return result


# ── 공개 인터페이스 ────────────────────────────────────────────────────────────

def get_premium(symbol: str) -> Optional[KimchiPremium]:
    """단일 심볼 김치프리미엄 계산. 가격 조회 실패 시 None."""
    result = get_all_premiums([symbol])
    return result.get(symbol)


def get_all_premiums(
    symbols: list[str] | None = None,
) -> dict[str, KimchiPremium]:
    """심볼 목록의 김치프리미엄을 일괄 계산.

    Returns:
        {symbol: KimchiPremium} — 조회 실패 심볼 제외.
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS

    cache_key = ",".join(sorted(symbols))
    now = time.time()
    cached = _PRICE_CACHE.get(cache_key)
    if cached and now - cached[1] < _PRICE_TTL:
        return cached[0]  # type: ignore[return-value]

    fx = get_usd_krw_rate()
    upbit = get_upbit_prices(symbols)
    binance = get_binance_prices(symbols)

    premiums: dict[str, KimchiPremium] = {}
    for sym in symbols:
        if sym not in upbit or sym not in binance:
            _LOG.debug("[KimchiPremium] %s 가격 없음 (upbit=%s, binance=%s)",
                       sym, sym in upbit, sym in binance)
            continue
        krw = upbit[sym]
        usdt = binance[sym]
        fair = usdt * fx
        if fair <= 0:
            continue
        pct = (krw / fair - 1.0) * 100.0
        premiums[sym] = KimchiPremium(
            symbol=sym,
            upbit_krw=krw,
            binance_usdt=usdt,
            usd_krw_rate=fx,
            fair_krw=fair,
            premium_pct=round(pct, 3),
            ts=now,
        )
        _LOG.debug(
            "[KimchiPremium] %s: Upbit %.0f KRW / Binance $%.2f × %.1f = %.0f KRW → %+.2f%%",
            sym, krw, usdt, fx, fair, pct,
        )

    _PRICE_CACHE[cache_key] = (premiums, now)
    return premiums


def score_penalty(premium_pct: float) -> tuple[float, str]:
    """프리미엄 수준에 따른 스코어 페널티 반환 (pt, reason).

    Levels:
        < 2%  : 페널티 없음
        2~5%  : -3pt  (주의)
        5~8%  : -7pt  (고평가)
        8%+   : -12pt (극단 고평가)
    """
    if premium_pct < LEVEL_LOW:
        return 0.0, ""
    if premium_pct < LEVEL_MEDIUM:
        return -3.0, f"김치프리미엄 {premium_pct:.1f}% (주의) -3pt"
    if premium_pct < LEVEL_HIGH:
        return -7.0, f"김치프리미엄 {premium_pct:.1f}% (고평가) -7pt"
    return -12.0, f"김치프리미엄 {premium_pct:.1f}% (극단) -12pt"
