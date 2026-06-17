"""신규상장 "안정화 후 첫 눌림목" 진입 가설 백테스트 (빗썸 공개 캔들 API).

배경: 상장 직후 펌핑 *추격* 매수는 -EV(기각). 이번 질문은 다른 타이밍 —
펌핑이 끝나고 고점 대비 X% 빠진 뒤(안정화), 첫 되돌림에서 사면 +EV인가?

방법(생존편향 회피, 로컬 데이터 불필요):
 1) 빗썸 KRW 마켓 중 '신규상장' = 일봉 히스토리가 LISTING_MAX_DAYS일 미만인 코인.
    (일봉 count=400 요청 시 200 미만 반환 = 그만큼 어린 코인. 빗썸은 최대 200 반환하므로
     200을 다 채우면 >=200일된 구상장으로 간주해 제외.)
 2) 각 신규상장 코인의 60분봉(시간봉)을 상장 시점부터 최대치로 받아 시계열 구성.
 3) 두 전략을 같은 코인·같은 데이터로 나란히 평가:
    - CHASE(추격 baseline): 상장 후 펌핑(고점 형성)을 보고 그 직후 봉 종가에 매수.
    - DIP(가설): 고점 대비 -X% 하락(안정화) 후, 첫 되돌림(직전 단기고점 돌파) 시 매수.
 4) 진입 후 forward return: +4h, +1d, +3d (시간봉 기준 4/24/72봉). 수수료 차감.

사용: ./.venv/bin/python scripts/backtest_listing_dip_entry.py [max_markets]
"""
from __future__ import annotations

import sys
import time
import statistics as st

import requests

BASE = "https://api.bithumb.com/v1"
LISTING_MAX_DAYS = 150        # 일봉이 이만큼 미만이면 신규상장으로 간주
FEE_ROUNDTRIP = 0.5           # 왕복 수수료/슬리피지 가정 %
HORIZONS_H = {"+4h": 4, "+1d": 24, "+3d": 72}   # 60분봉 개수
PUMP_PCT = 15.0               # 상장 후 '펌핑'으로 볼 누적 상승(시작가 대비) %
DIP_VARIANTS = [15.0, 25.0, 35.0]   # 고점 대비 -X% 하락 = 안정화 임계
REBOUND_LOOKBACK = 6          # 되돌림 판정: 직전 N봉 고가 돌파
MAX_HOLD_AFTER_PUMP = 24 * 7  # 펌핑 후 진입 탐색 최대 봉수(7일)


def get(path, params):
    r = requests.get(f"{BASE}{path}", params=params, timeout=20)
    return r.json()


def krw_markets():
    d = get("/market/all", {})
    return [m["market"] for m in d if str(m.get("market", "")).startswith("KRW-")]


def daily_count(market):
    d = get("/candles/days", {"market": market, "count": 200})
    return len(d) if isinstance(d, list) else 0


def hours(market, count=200, to=None):
    p = {"market": market, "count": count}
    if to:
        p["to"] = to
    d = get("/candles/minutes/60", p)
    return d if isinstance(d, list) else []


def full_hourly(market, max_bars=2000):
    """상장 이후 전체 60분봉(오름차순 closes/highs/lows)."""
    out = []
    to = None
    seen = set()
    while len(out) < max_bars:
        chunk = hours(market, 200, to)
        time.sleep(0.06)
        if not chunk:
            break
        new = [c for c in chunk if c.get("candle_date_time_kst") not in seen]
        if not new:
            break
        for c in new:
            seen.add(c.get("candle_date_time_kst"))
        out.extend(new)
        if len(chunk) < 200:
            break
        to = chunk[-1].get("candle_date_time_kst")  # 더 과거로
    out = sorted(out, key=lambda c: str(c.get("candle_date_time_kst") or ""))
    closes = [float(c.get("trade_price") or 0) for c in out]
    highs = [float(c.get("high_price") or 0) for c in out]
    lows = [float(c.get("low_price") or 0) for c in out]
    return closes, highs, lows


def fwd_rets(closes, entry_i):
    entry = closes[entry_i]
    out = {}
    for label, h in HORIZONS_H.items():
        j = entry_i + h
        if entry > 0 and j < len(closes):
            out[label] = (closes[j] - entry) / entry * 100.0 - FEE_ROUNDTRIP
    return out


def backtest_one(closes, highs, lows):
    """한 코인에서 CHASE 1건 + DIP 변형별 1건 진입 후보 산출."""
    res = {"chase": None, "dip": {x: None for x in DIP_VARIANTS}}
    n = len(closes)
    if n < 30:
        return res
    start = closes[0]
    if start <= 0:
        return res
    # 펌핑 정점(시작가 대비 PUMP_PCT 이상 상승한 첫 구간의 최고가 인덱스)
    peak_i = None
    run_max = start
    run_max_i = 0
    for i in range(1, min(n, MAX_HOLD_AFTER_PUMP)):
        if highs[i] > run_max:
            run_max = highs[i]
            run_max_i = i
        if (run_max - start) / start * 100.0 >= PUMP_PCT:
            peak_i = run_max_i
            break
    if peak_i is None:
        return res  # 펌핑 없는 코인 — 가설/추격 둘 다 대상 아님

    # CHASE baseline: 펌핑 확인된 그 봉(peak 직후) 종가에 매수
    chase_i = min(peak_i + 1, n - 1)
    res["chase"] = fwd_rets(closes, chase_i)

    # 펌핑 정점 이후로 고점 추적하며 DIP 진입 탐색
    for X in DIP_VARIANTS:
        hi = run_max
        hi_i = peak_i
        entered = None
        for i in range(peak_i + 1, n - 1):
            if highs[i] > hi:
                hi = highs[i]
                hi_i = i
            drop = (hi - lows[i]) / hi * 100.0 if hi > 0 else 0.0
            if drop >= X:
                # 안정화 도달. 이제 첫 되돌림(직전 REBOUND_LOOKBACK봉 고가 돌파) 대기
                for j in range(i + 1, min(i + 1 + MAX_HOLD_AFTER_PUMP, n - 1)):
                    lookback_hi = max(highs[max(peak_i, j - REBOUND_LOOKBACK):j] or [0])
                    if closes[j] > lookback_hi:
                        entered = j
                        break
                break
        if entered is not None:
            res["dip"][X] = fwd_rets(closes, entered)
    return res


def agg(rows, label):
    vals = [r[label] for r in rows if label in r]
    if not vals:
        return None
    mean = st.mean(vals)
    med = st.median(vals)
    win = sum(1 for v in vals if v > 0) / len(vals)
    return mean, med, win, len(vals)


def main():
    max_n = int(sys.argv[1]) if len(sys.argv) > 1 else 463
    mkts = krw_markets()
    print(f"빗썸 KRW {len(mkts)}개. 신규상장(일봉<{LISTING_MAX_DAYS}일) 탐색 중...", flush=True)

    new_listings = []
    scanned = 0
    for mk in mkts[:max_n]:
        try:
            dc = daily_count(mk)
            time.sleep(0.05)
        except Exception:
            continue
        scanned += 1
        if 0 < dc < LISTING_MAX_DAYS:
            new_listings.append((mk, dc))
    print(f"스캔 {scanned}개 · 신규상장 후보 {len(new_listings)}건 "
          f"(일봉 {LISTING_MAX_DAYS}일 미만)", flush=True)
    for mk, dc in new_listings:
        print(f"   {mk}: {dc}일", flush=True)

    chase_rows, dip_rows = [], {x: [] for x in DIP_VARIANTS}
    pump_coins = 0
    for mk, dc in new_listings:
        try:
            closes, highs, lows = full_hourly(mk)
        except Exception as e:
            print(f"  ! {mk} 시간봉 실패 {e}")
            continue
        if len(closes) < 30:
            continue
        r = backtest_one(closes, highs, lows)
        if r["chase"]:
            pump_coins += 1
            chase_rows.append(r["chase"])
        for X in DIP_VARIANTS:
            if r["dip"][X]:
                dip_rows[X].append(r["dip"][X])

    print(f"\n펌핑 발생 신규상장 코인 {pump_coins}건 (이게 유효 표본)\n")
    print(f"{'전략':<18}{'호라이즌':<7}{'평균%':>9}{'중앙%':>9}{'승률':>7}{'N':>5}  판정")
    print("-" * 64)

    def emit(name, rows):
        for label in HORIZONS_H:
            a = agg(rows, label)
            if not a:
                continue
            mean, med, win, nn = a
            verdict = "+EV" if (mean > 0 and win >= 0.5) else "-EV"
            flag = " (N<20 약함)" if nn < 20 else ""
            print(f"{name:<18}{label:<7}{mean:>9.2f}{med:>9.2f}{win:>7.2f}{nn:>5}  {verdict}{flag}")

    emit("CHASE(추격)", chase_rows)
    for X in DIP_VARIANTS:
        emit(f"DIP -{X:.0f}%(눌림목)", dip_rows[X])

    print("\n주의: 표본<20이면 통계적으로 약함. 신규상장은 드물어 N이 작을 수 있음.")


if __name__ == "__main__":
    main()
