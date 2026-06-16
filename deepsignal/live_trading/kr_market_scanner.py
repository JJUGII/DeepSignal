"""국내주식 전 시장 스냅샷 스캐너 — KIS 순위 API 기반 급등주 시야 확보.

KIS 웹소켓 워치리스트(~47종목) 밖의 급등·고거래대금 종목을
순위 API(전 시장 1콜)로 잡아 signals DB(kr_movers_v1)에 기록한다.
기록된 신호는 기존 AI 추천 엔진(daily-ai-trade-plan)이 그대로 소비하고,
실행은 기존 무승인 자동매매 게이트 경로를 탄다(새 주문 경로 없음).

기본 OFF — 공격성 다이얼 L9-10에서 KR_SCANNER_ENABLED=true 로 켜진다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

_KST = timezone(timedelta(hours=9))

STRATEGY_NAME = "kr_movers_v1"

# 우선주/스팩/리츠 등 단타 부적합 이름 패턴
_NAME_EXCLUDES = ("스팩", "SPAC", "리츠", "ETN")


def scanner_enabled() -> bool:
    return os.environ.get("KR_SCANNER_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _min_turnover_krw() -> float:
    """일 누적 거래대금 하한 (유동성 필터). 기본 30억."""
    try:
        return float(os.environ.get("KR_SCANNER_MIN_TURNOVER_KRW", "3000000000") or 3e9)
    except ValueError:
        return 3e9


def _max_change_pct() -> float:
    """등락률 상한 (기본 29.5% — 상한가 직전까지 허용. 추격 캡은 다이얼이 결정)."""
    try:
        return float(os.environ.get("KR_SCANNER_MAX_CHANGE_PCT", "29.5") or 29.5)
    except ValueError:
        return 29.5


def _is_kr_market_hours(now: datetime | None = None) -> bool:
    n = now or datetime.now(_KST)
    if n.weekday() >= 5:
        return False
    hm = n.hour * 100 + n.minute
    return 900 <= hm <= 1520  # 마감 직전 10분은 신규 신호 제외


def _name_ok(name: str) -> bool:
    nm = str(name or "")
    if not nm:
        return False
    if any(x in nm for x in _NAME_EXCLUDES):
        return False
    if nm.endswith("우") or nm.endswith("우B") or nm.endswith("우C"):  # 우선주
        return False
    return True


def scan_kr_movers(broker: Any) -> list[dict[str, Any]]:
    """KIS 순위 API로 전 시장 급등(등락률 상위) + 거래대금 상위를 스캔.

    Returns: [{symbol, name, price, change_pct, turnover_krw, rank_src}]
    """
    out: dict[str, dict[str, Any]] = {}

    # ① 등락률 상위 (전 시장)
    try:
        params = {
            "fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20170",
            "fid_input_iscd": "0000", "fid_rank_sort_cls_code": "0",
            "fid_input_cnt_1": "0", "fid_prc_cls_code": "0",
            "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "",
            "fid_trgt_cls_code": "0", "fid_trgt_exls_cls_code": "0",
            "fid_div_cls_code": "0", "fid_rsfl_rate1": "", "fid_rsfl_rate2": "",
        }
        body, _ = broker._kis_get_json("FHPST01700000", "/uapi/domestic-stock/v1/ranking/fluctuation", params)
        for r in (body.get("output") or []):
            sym = str(r.get("stck_shrn_iscd") or "").strip()
            if not sym or not sym.isdigit():
                continue
            price = float(r.get("stck_prpr") or 0)
            chg = float(r.get("prdy_ctrt") or 0)
            vol = float(r.get("acml_vol") or 0)
            out[sym] = {
                "symbol": sym.zfill(6), "name": str(r.get("hts_kor_isnm") or sym),
                "price": price, "change_pct": chg,
                "turnover_krw": price * vol, "rank_src": "fluctuation",
            }
    except Exception:
        pass

    # ② 거래대금 상위 (전 시장) — 대금 큰 활성 종목 보강
    try:
        p2 = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "3", "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "000000", "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
        }
        b2, _ = broker._kis_get_json("FHPST01710000", "/uapi/domestic-stock/v1/quotations/volume-rank", p2)
        for r in (b2.get("output") or []):
            sym = str(r.get("mksc_shrn_iscd") or "").strip()
            if not sym or not sym.isdigit():
                continue
            sym6 = sym.zfill(6)
            price = float(r.get("stck_prpr") or 0)
            chg = float(r.get("prdy_ctrt") or 0)
            turn = float(r.get("acml_tr_pbmn") or 0) or price * float(r.get("acml_vol") or 0)
            if sym6 in out:
                out[sym6]["turnover_krw"] = max(out[sym6]["turnover_krw"], turn)
                out[sym6]["rank_src"] += "+volume"
            else:
                out[sym6] = {
                    "symbol": sym6, "name": str(r.get("hts_kor_isnm") or sym6),
                    "price": price, "change_pct": chg,
                    "turnover_krw": turn, "rank_src": "volume",
                }
    except Exception:
        pass

    # 필터: 이름·가격·거래대금·등락률(상한가 붙박이 제외)·하락종목 제외
    floor = _min_turnover_krw()
    cap = _max_change_pct()
    movers = []
    for m in out.values():
        if not _name_ok(m["name"]):
            continue
        if m["price"] < 500:           # 동전주 제외
            continue
        if m["turnover_krw"] < floor:  # 유동성 부족
            continue
        if m["change_pct"] <= 0.5:     # 상승 모멘텀만 (하락 추격 안 함)
            continue
        if m["change_pct"] > cap:      # 상한가 붙박이(체결 불가) 제외
            continue
        movers.append(m)
    movers.sort(key=lambda x: (-x["change_pct"], -x["turnover_krw"]))
    return movers


def _sweet_band() -> tuple[float, float]:
    """매수 가능한 '돌파 초입' 등락률 밴드 — 이 구간이 점수 정점.

    이 위로 갈수록 단기과열·투자경고 지정 위험이 커져(=KIS 매수제한·단일가매매로
    못 사고·꼭지) 감점한다. 기본 +5~10% — 60건 실측에서 이 구간이 다음날 +1.86%·
    승률 77%로 최고였고, <5%는 −0.16%(노이즈)·+15%↑는 −4.68%(과열사망)였다.
    KR_SCANNER_SWEET_MIN/MAX_PCT로 조정.
    """
    try:
        lo = float(os.environ.get("KR_SCANNER_SWEET_MIN_PCT", "5.0") or 5.0)
        hi = float(os.environ.get("KR_SCANNER_SWEET_MAX_PCT", "10.0") or 10.0)
    except ValueError:
        lo, hi = 5.0, 10.0
    return (lo, hi) if hi > lo else (5.0, 10.0)


def _score(m: dict[str, Any]) -> float:
    """돌파 초입(sweet band)에서 정점, 과대상승(단기과열 임박)일수록 감점.

    기존엔 등락률에 단조 비례(+20%가 최고점)라 매번 단기과열 지정 종목이 1등 →
    KIS 매수제한으로 0매수였다. *살 수 있는* 초입 모멘텀을 상위로 끌어올린다.
    recommendation 매수 문턱(DEEPSIGNAL_STOCK_MIN_SCORE)과 같은 0~100 스케일.
    """
    chg = float(m.get("change_pct") or 0)
    lo, hi = _sweet_band()
    if chg < lo:
        base = 45.0 + 30.0 * (chg / lo)                 # 0→45, lo→75 (모멘텀 진입 중)
    elif chg <= hi:
        base = 78.0 + 12.0 * (chg - lo) / (hi - lo)      # lo→78, hi→90 (정점)
    else:
        base = 90.0 - min(58.0, (chg - hi) * 5.0)        # 과대상승 급감 (+20%≈40)
    turn = float(m.get("turnover_krw") or 0)
    bonus = 6.0 if turn >= 5e10 else (3.0 if turn >= 1e10 else 0.0)
    return round(max(10.0, min(97.0, base + bonus)), 1)


def record_mover_signals(movers: list[dict[str, Any]], *, db_path: str | None = None,
                         max_records: int = 20) -> dict[str, int]:
    """급등주를 signals DB(kr_movers_v1)에 upsert — 당일 최고점 유지."""
    import sqlite3
    from pathlib import Path
    from deepsignal.config.settings import load_settings
    path = db_path or load_settings().db_path
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    sql = (
        "INSERT INTO signals (symbol, signal_date, strategy_name, technical_score, "
        "final_score, action, confidence, reason, raw_json) "
        f"VALUES (?, ?, '{STRATEGY_NAME}', ?, ?, 'BUY', ?, ?, ?) "
        "ON CONFLICT(symbol, signal_date, strategy_name) DO UPDATE SET "
        "  technical_score = MAX(technical_score, excluded.technical_score), "
        "  final_score = MAX(final_score, excluded.final_score), "
        "  reason = CASE WHEN excluded.final_score >= final_score THEN excluded.reason ELSE reason END, "
        "  raw_json = CASE WHEN excluded.final_score >= final_score THEN excluded.raw_json ELSE raw_json END"
    )
    n_ok = n_fail = 0
    # 기록은 등락률 순이 아니라 *점수 순*(돌파 초입 정점)으로 — 살 수 있는 종목이
    # 과대상승주에 밀려 상위 N 컷오프에서 잘리지 않게 한다.
    movers = sorted(movers, key=_score, reverse=True)
    with sqlite3.connect(str(Path(path).expanduser().resolve())) as conn:
        for m in movers[:max_records]:
            try:
                sc = _score(m)
                # 신뢰도도 초입에서 높고 과대상승(단기과열 임박)이면 낮춘다
                _chg = float(m["change_pct"])
                _lo, _hi = _sweet_band()
                conf = round(min(0.85, 0.45 + _chg / 30.0) if _chg <= _hi
                             else max(0.30, 0.85 - (_chg - _hi) / 25.0), 2)
                reason = (f"전시장 급등 스캔: {m['name']} {m['change_pct']:+.1f}% "
                          f"거래대금 {m['turnover_krw']/1e8:.0f}억 ({m['rank_src']})")
                conn.execute(sql, (m["symbol"], today, sc, sc, conf, reason,
                                   json.dumps(m, ensure_ascii=False)))
                n_ok += 1
            except Exception:
                n_fail += 1
        conn.commit()
    return {"recorded": n_ok, "failed": n_fail}


def run_kr_scan(*, db_path: str | None = None, broker: Any = None) -> dict[str, Any]:
    """스캔 1회: 순위 조회 → 필터 → 신호 기록. 장외/비활성이면 no-op."""
    if not scanner_enabled():
        return {"skipped": "KR_SCANNER_ENABLED=off"}
    if not _is_kr_market_hours():
        return {"skipped": "market_closed"}
    if broker is None:
        from deepsignal.live_trading.broker.kis_broker import KISBroker
        from deepsignal.live_trading.broker.kis_config import load_kis_config_from_env
        broker = KISBroker(load_kis_config_from_env())
    movers = scan_kr_movers(broker)
    res = record_mover_signals(movers, db_path=db_path)
    top = [f"{m['name']}{m['change_pct']:+.1f}%" for m in movers[:5]]
    return {"scanned": len(movers), **res, "top": top}
