"""해외주식(미국) 전 시장 스냅샷 스캐너 — KIS 해외 조건검색 API.

국내 스캐너(kr_market_scanner)와 동일 구조의 미국장 버전.
나스닥·뉴욕·아멕스 등락률 상위 급등주를 1콜씩 조회해 점수화하고,
overseas_plan 의 compute_overseas_scores 와 합쳐질 스냅샷을 캐시에 쓴다.

기본 OFF — 공격성 다이얼 L9-10에서 OVERSEAS_SCANNER_ENABLED=true.
스트림 봉(bars) 기반 K-GSQS 가 비어있을 때의 급등주 시야를 보강한다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))
_CACHE = "overseas_movers.json"
_EXCHANGES = ("NAS", "NYS", "AMS")   # 나스닥/뉴욕/아멕스
_NAME_EXCLUDES = ("WARRANT", "RIGHT", "UNIT", "ETF", "ETN", "DAILY", "2X", "3X",
                  "DIREXION", "PROSHARES", "T-REX", "TRADR", "LEVERAGED", "INVERSE", "BULL", "BEAR")


def scanner_enabled() -> bool:
    return os.environ.get("OVERSEAS_SCANNER_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _min_change() -> float:
    try:
        return float(os.environ.get("OVERSEAS_SCANNER_MIN_CHANGE_PCT", "3.0") or 3.0)
    except ValueError:
        return 3.0


def _max_change() -> float:
    try:
        return float(os.environ.get("OVERSEAS_SCANNER_MAX_CHANGE_PCT", "40.0") or 40.0)
    except ValueError:
        return 40.0


def _min_turnover_usd() -> float:
    try:
        return float(os.environ.get("OVERSEAS_SCANNER_MIN_TURNOVER_USD", "3000000") or 3e6)
    except ValueError:
        return 3e6


def _is_us_market_hours(now: datetime | None = None) -> bool:
    """미국 정규장 (한국시간 22:30~05:00, 평일)."""
    n = now or datetime.now(_KST)
    wd, hm = n.weekday(), n.hour * 100 + n.minute
    if wd < 5 and (hm >= 2230 or hm <= 500):
        return True
    if wd == 5 and hm <= 500:  # 토 새벽 = 금 미국장
        return True
    return False


def _name_ok(name: str) -> bool:
    nm = str(name or "").upper()
    return bool(nm) and not any(x in nm for x in _NAME_EXCLUDES)


def scan_overseas_movers(broker: Any) -> list[dict[str, Any]]:
    """KIS 해외 조건검색으로 등락률 상위 급등주를 거래소별 수집."""
    out: dict[str, dict[str, Any]] = {}
    lo, hi = _min_change(), _max_change()
    for excd in _EXCHANGES:
        params = {
            "AUTH": "", "EXCD": excd,
            "CO_YN_PRICECUR": "", "CO_ST_PRICECUR": "", "CO_EN_PRICECUR": "",
            "CO_YN_RATE": "1", "CO_ST_RATE": str(lo), "CO_EN_RATE": str(hi),
            "CO_YN_VALX": "", "CO_ST_VALX": "", "CO_EN_VALX": "",
            "CO_YN_SHAR": "", "CO_ST_SHAR": "", "CO_EN_SHAR": "",
            "CO_YN_VOLUME": "", "CO_ST_VOLUME": "", "CO_EN_VOLUME": "",
            "CO_YN_AMT": "", "CO_ST_AMT": "", "CO_EN_AMT": "",
            "CO_YN_EPS": "", "CO_ST_EPS": "", "CO_EN_EPS": "",
            "CO_YN_PER": "", "CO_ST_PER": "", "CO_EN_PER": "", "KEYB": "",
        }
        try:
            body, _ = broker._kis_get_json(
                "HHDFS76410000", "/uapi/overseas-price/v1/quotations/inquire-search", params)
            rows = body.get("output2") or []
        except Exception:
            rows = []
        # KIS 거래소코드 → 주문용 거래소
        exch_map = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}
        for r in rows:
            tick = str(r.get("symb") or "").strip().upper()
            if not tick:
                continue
            try:
                price = float(r.get("last") or 0)
                chg = float(r.get("rate") or 0)
                vol = float(r.get("tvol") or 0)
            except (TypeError, ValueError):
                continue
            turnover = price * vol
            key = f"{exch_map.get(excd,'NASD')}:{tick}"
            if key not in out or chg > out[key]["change_pct"]:
                out[key] = {
                    "symbol": key, "ticker": tick, "exchange": exch_map.get(excd, "NASD"),
                    "name": str(r.get("name") or tick), "price": price,
                    "change_pct": chg, "turnover_usd": turnover,
                }
    floor = _min_turnover_usd()
    movers = [m for m in out.values()
              if _name_ok(m["name"]) and m["price"] >= 1.0 and m["turnover_usd"] >= floor]
    movers.sort(key=lambda x: (-x["change_pct"], -x["turnover_usd"]))
    return movers


def cache_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / _CACHE


def run_overseas_scan(*, output_dir: str | Path = "outputs", broker: Any = None) -> dict[str, Any]:
    """스캔 1회 → outputs/overseas_movers.json 캐시. overseas_plan이 합쳐 읽음."""
    if not scanner_enabled():
        return {"skipped": "OVERSEAS_SCANNER_ENABLED=off"}
    if not _is_us_market_hours():
        return {"skipped": "market_closed"}
    if broker is None:
        from deepsignal.live_trading.broker.kis_broker import KISBroker
        from deepsignal.live_trading.broker.kis_config import load_kis_config_from_env
        broker = KISBroker(load_kis_config_from_env())
    movers = scan_overseas_movers(broker)
    payload = {"updated_at": datetime.now(_KST).isoformat(timespec="seconds"),
               "movers": movers[:30]}
    p = cache_path(output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"scanned": len(movers), "cached": len(payload["movers"]),
            "top": [f"{m['ticker']}{m['change_pct']:+.1f}%" for m in movers[:5]]}


def load_overseas_movers(output_dir: str | Path = "outputs", *, ttl_min: int = 10) -> list[dict[str, Any]]:
    """캐시된 급등주 (overseas_plan이 K-GSQS 스코어와 합칠 때 사용). 만료/없음 → []."""
    p = cache_path(output_dir)
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(d.get("updated_at")))
        if datetime.now(_KST) - ts > timedelta(minutes=ttl_min):
            return []
        return list(d.get("movers") or [])
    except Exception:
        return []
