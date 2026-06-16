"""거래소간 차익 심층검증 — 호가창 실체결가 기준 (진짜 차익 vs 허상).

앞선 스냅샷은 last-trade 기준이라 stale 가능. 여기선 양 거래소 호가창(bid/ask)을 떠서
*실제 체결가*로 양방향 차익을 계산하고, 수수료를 양쪽 다 빼고, 체결 가능 금액(호가 깊이)
까지 본다. 큰 괴리(BTT 18% 등)가 실제론 못 먹는 허상인지(얇은 호가/stale) 가린다.

차익 방향:
  A) 빗썸 ask 매수 → 업비트 bid 매도 : (up_bid - bi_ask)/bi_ask - 수수료
  B) 업비트 ask 매수 → 빗썸 bid 매도 : (bi_bid - up_ask)/up_ask - 수수료
체결가능금액 = min(매수쪽 ask 잔량가치, 매도쪽 bid 잔량가치).

사용: ./.venv/bin/python scripts/arb_depth_validate.py [fee_pct]
"""
from __future__ import annotations
import sys, time, requests

UP = "https://api.upbit.com/v1"
BI = "https://api.bithumb.com/v1"
FEE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.25   # 왕복(양 거래소 합) 수수료% 가정
MIN_SIZE_KRW = 50_000   # 의미있는 체결가능 최소금액


def krw(base):
    return {m["market"] for m in requests.get(f"{base}/market/all", timeout=15).json()
            if str(m.get("market", "")).startswith("KRW-")}


def orderbooks(base, markets):
    out = {}
    mk = list(markets)
    for i in range(0, len(mk), 80):
        chunk = ",".join(mk[i:i+80])
        try:
            d = requests.get(f"{base}/orderbook", params={"markets": chunk}, timeout=15).json()
            for r in (d if isinstance(d, list) else []):
                u = (r.get("orderbook_units") or [{}])[0]
                out[r.get("market")] = (
                    float(u.get("ask_price") or 0), float(u.get("bid_price") or 0),
                    float(u.get("ask_size") or 0), float(u.get("bid_size") or 0),
                )
        except Exception:
            pass
        time.sleep(0.05)
    return out


def main():
    common = sorted(krw(UP) & krw(BI))
    print(f"공통 {len(common)}종 · 호가창 실체결 검증 · 왕복수수료 {FEE}% · 최소체결 {MIN_SIZE_KRW:,}원\n")
    upob = orderbooks(UP, common)
    biob = orderbooks(BI, common)

    real = []  # (net_arb%, size_krw, coin, direction)
    for m in common:
        if m not in upob or m not in biob:
            continue
        ua, ub, uas, ubs = upob[m]
        ba, bb, bas, bbs = biob[m]
        if min(ua, ub, ba, bb) <= 0:
            continue
        # A: 빗썸 매수→업비트 매도
        arbA = (ub - ba) / ba * 100 - FEE
        sizeA = min(ba * bas, ub * ubs)
        # B: 업비트 매수→빗썸 매도
        arbB = (bb - ua) / ua * 100 - FEE
        sizeB = min(ua * uas, bb * bbs)
        if arbA >= arbB:
            best, size, d = arbA, sizeA, "빗썸→업비트"
        else:
            best, size, d = arbB, sizeB, "업비트→빗썸"
        if best > 0:
            real.append((best, size, m, d))

    real.sort(reverse=True)
    profitable = [x for x in real if x[0] > 0]
    tradable = [x for x in real if x[0] > 0 and x[1] >= MIN_SIZE_KRW]
    print(f"순차익>0 (수수료 후): {len(profitable)}종 / {len(common)}")
    print(f"그중 체결가능(≥{MIN_SIZE_KRW:,}원 깊이): {len(tradable)}종  ← 진짜 먹을 수 있는 것\n")
    print(f"{'순차익%':>7} {'체결가능원':>11} {'종목':<14} 방향")
    for best, size, m, d in tradable[:15]:
        print(f"{best:>7.2f} {size:>11,.0f} {m:<14} {d}")
    if not tradable:
        print("  (체결가능한 진짜 차익 없음 — 괴리는 다 얇은 호가/stale 허상)")
    print()
    # 큰 괴리지만 못 먹는(깊이 부족) 허상 카운트
    illusory = [x for x in real if x[0] > 1.0 and x[1] < MIN_SIZE_KRW]
    print(f"참고: 순차익>1%인데 깊이<{MIN_SIZE_KRW:,}원(못 먹는 허상): {len(illusory)}종")
    print("\n결론: '체결가능 진짜 차익' 종목이 多·꾸준하면 차익엔진 승산. 거의 0이면 괴리는 허상.")


if __name__ == "__main__":
    main()
