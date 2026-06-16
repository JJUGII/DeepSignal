"""반등(평균회귀) 백테스트 — 추격의 반대. "급락일에 사면(반등 노림) 돈 되나?"

추격(펌핑일 매수)은 -EV로 기각됨. 반대 방향(급락일 매수=oversold 반등)을 같은 방법으로 검증.
빗썸 공개 일봉 API. 급락일(당일 -DROP% 이하) 종가 매수 → N일 후 수익(수수료 차감).
+EV면 평균회귀(반등)는 추격과 달리 먹힌다는 뜻.
사용: ./.venv/bin/python scripts/backtest_rebound.py [max_markets]
"""
from __future__ import annotations
import sys, time, requests

BASE = "https://api.bithumb.com/v1"
DROPS = (-10.0, -15.0, -20.0)   # 여러 급락 임계
FEE = 0.5
HORIZONS = (1, 3, 7)


def krw_markets():
    return [m["market"] for m in requests.get(f"{BASE}/market/all", timeout=15).json()
            if str(m.get("market", "")).startswith("KRW-")]


def daily(mk, n=200):
    d = requests.get(f"{BASE}/candles/days", params={"market": mk, "count": n}, timeout=15).json()
    return d if isinstance(d, list) else []


def main():
    mkts = krw_markets()[: int(sys.argv[1]) if len(sys.argv) > 1 else 250]
    print(f"빗썸 KRW {len(mkts)}개 · 반등 검증(급락일 종가 매수→N일) · 수수료 {FEE}%\n")
    # {drop: {h: [returns]}}
    res = {d: {h: [] for h in HORIZONS} for d in DROPS}
    cnt = {d: 0 for d in DROPS}
    used = 0
    for mk in mkts:
        try:
            rows = daily(mk, 200); time.sleep(0.04)
        except Exception:
            continue
        if len(rows) < max(HORIZONS) + 8:
            continue
        rows = sorted(rows, key=lambda c: str(c.get("candle_date_time_kst") or ""))
        cl = [float(c.get("trade_price") or 0) for c in rows]
        used += 1
        n = len(cl)
        for t in range(2, n - max(HORIZONS)):
            prev = cl[t - 1]
            if prev <= 0:
                continue
            dret = (cl[t] - prev) / prev * 100.0
            for D in DROPS:
                if dret <= D:
                    cnt[D] += 1
                    for h in HORIZONS:
                        res[D][h].append((cl[t + h] - cl[t]) / cl[t] * 100.0 - FEE)
                    break  # 가장 깊은 구간 하나만
    print(f"사용 마켓 {used}\n")
    for D in DROPS:
        if cnt[D] == 0:
            continue
        print(f"── 급락 {D}% 이하 매수 (샘플 {cnt[D]}건) ──")
        print(f"   {'보유':>4} {'평균%':>8} {'중앙%':>8} {'승률':>6}  판정")
        for h in HORIZONS:
            r = sorted(res[D][h])
            if not r:
                continue
            mean = sum(r) / len(r); med = r[len(r)//2]; win = sum(1 for x in r if x > 0)/len(r)
            v = "✅+EV(반등 먹힘)" if (mean > 0 and win >= 0.5) else "❌-EV"
            print(f"   {h:>3}일 {mean:>8.2f} {med:>8.2f} {win:>6.2f}  {v}")
        print()
    print("결론: 평균% 양수+승률≥0.5면 '급락 후 반등 매수'는 추격과 달리 먹힌다.")


if __name__ == "__main__":
    main()
