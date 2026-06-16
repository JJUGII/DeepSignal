"""상장감시 신호 백테스트 — "펌핑 본 뒤 사면 돈 되나?" (빗썸 공개 API 직접)

상장감시는 이미 급등(+15%↑·거래량급증)한 빗썸 종목을 상단에 보여준다. 질문:
그 펌핑일 종가에 사면 이후 1/3/7일 수익이 +인가 −인가(=추격이 먹히나)?

방법(생존편향 회피): 빗썸 전체 KRW 마켓의 일봉 200개를 훑어, '상장감시에 떴을 법한'
펌핑일(당일 +PUMP_PCT 이상 + 거래량 spike)을 *과거 전부* 찾아 그 종가 매수 →
N일 후 종가 매도 수익을 수수료 차감 후 집계. 각 과거 펌핑일이 독립 OOS 샘플.

빗썸 브로커가 dry-run/demo라 mock 캔들을 주므로, 공개 캔들 API를 직접 호출한다(인증 불요).
사용: ./.venv/bin/python scripts/backtest_listing_pump.py [max_markets]
"""

from __future__ import annotations

import sys
import time

import requests

BASE = "https://api.bithumb.com/v1"
PUMP_PCT = 15.0
VOL_SPIKE = 2.0
FEE_ROUNDTRIP = 0.5
HORIZONS = (1, 3, 7)


def krw_markets() -> list[str]:
    r = requests.get(f"{BASE}/market/all", timeout=15)
    return [m["market"] for m in r.json() if str(m.get("market", "")).startswith("KRW-")]


def daily(market: str, count: int = 200) -> list[dict]:
    r = requests.get(f"{BASE}/candles/days", params={"market": market, "count": count}, timeout=15)
    d = r.json()
    return d if isinstance(d, list) else []


def main():
    max_n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    mkts = krw_markets()[:max_n]
    print(f"빗썸 KRW {len(mkts)}개 스캔 · 펌핑일=당일+{PUMP_PCT}%&거래량{VOL_SPIKE}x↑ · 수수료 {FEE_ROUNDTRIP}%\n")

    rets = {h: [] for h in HORIZONS}
    n_pumps = used = errs = 0
    for mk in mkts:
        try:
            rows = daily(mk, 200)
            time.sleep(0.05)
        except Exception:
            errs += 1
            continue
        if len(rows) < max(HORIZONS) + 8:
            continue
        rows = sorted(rows, key=lambda c: str(c.get("candle_date_time_kst") or ""))  # 오름차순
        closes = [float(c.get("trade_price") or 0) for c in rows]
        turn = [float(c.get("candle_acc_trade_price") or 0) for c in rows]
        used += 1
        n = len(closes)
        for t in range(5, n - max(HORIZONS)):
            prev = closes[t - 1]
            if prev <= 0:
                continue
            day_ret = (closes[t] - prev) / prev * 100.0
            base = sum(turn[t - 5:t]) / 5 if t >= 5 else 0
            vr = turn[t] / base if base > 0 else 0.0
            if day_ret >= PUMP_PCT and vr >= VOL_SPIKE:
                n_pumps += 1
                entry = closes[t]
                for h in HORIZONS:
                    rets[h].append((closes[t + h] - entry) / entry * 100.0 - FEE_ROUNDTRIP)

    print(f"사용 마켓 {used} · 실패 {errs} · 과거 펌핑일 {n_pumps}건\n")
    if not n_pumps:
        print("펌핑 샘플 0 — 임계 확인 필요")
        return
    print(f"{'보유':>4} {'평균%':>8} {'중앙%':>8} {'승률':>6} {'최악%':>8} {'최선%':>8}  판정")
    for h in HORIZONS:
        r = sorted(rets[h])
        if not r:
            continue
        mean = sum(r) / len(r)
        med = r[len(r) // 2]
        win = sum(1 for x in r if x > 0) / len(r)
        v = "✅+EV" if (mean > 0 and win > 0.5) else "❌-EV(추격 손해)"
        print(f"{h:>3}일 {mean:>8.2f} {med:>8.2f} {win:>6.2f} {r[0]:>8.1f} {r[-1]:>8.1f}  {v}")
    print("\n결론: 평균%가 음수면 '펌핑 보고 사기(상장감시 추격)'는 수수료 후 손해.")


if __name__ == "__main__":
    main()
