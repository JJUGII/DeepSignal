"""거래소간 차익 스캔 — 업비트 vs 빗썸 같은 코인 가격차(추격 아닌 구조적 빠른 엣지).

두 거래소 공통 KRW 코인의 현재가를 동시 조회해 가격차%를 본다. 수수료(왕복) 넘는
괴리가 얼마나 자주·크게 나는지 = 차익 기회 빈도. 공개 ticker API(인증 불요).
한계: 단일 스냅샷(여러 번 떠서 지속성 확인). 실제 차익은 인벤토리/이체 고려 필요.
사용: ./.venv/bin/python scripts/arb_scan.py [snapshots]
"""
from __future__ import annotations
import sys, time, requests

UPBIT = "https://api.upbit.com/v1"
BITHUMB = "https://api.bithumb.com/v1"
FEE_ROUNDTRIP = 0.5  # 양 거래소 수수료+슬리피지 가정(%)


def up_markets():
    return {m["market"] for m in requests.get(f"{UPBIT}/market/all", timeout=15).json()
            if str(m.get("market", "")).startswith("KRW-")}


def bi_markets():
    return {m["market"] for m in requests.get(f"{BITHUMB}/market/all", timeout=15).json()
            if str(m.get("market", "")).startswith("KRW-")}


def tickers(base, markets):
    out = {}
    mk = list(markets)
    for i in range(0, len(mk), 100):
        chunk = ",".join(mk[i:i+100])
        try:
            d = requests.get(f"{base}/ticker", params={"markets": chunk}, timeout=15).json()
            for r in (d if isinstance(d, list) else []):
                out[r.get("market")] = float(r.get("trade_price") or 0)
        except Exception:
            pass
        time.sleep(0.05)
    return out


def main():
    snaps = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    common = sorted(up_markets() & bi_markets())
    print(f"공통 KRW 코인 {len(common)}개 · 스냅샷 {snaps}회 · 왕복수수료 {FEE_ROUNDTRIP}%\n")

    over_fee_counts = []
    big_gaps_all = {}
    for s in range(snaps):
        up = tickers(UPBIT, common)
        bi = tickers(BITHUMB, common)
        gaps = []
        for m in common:
            u, b = up.get(m, 0), bi.get(m, 0)
            if u > 0 and b > 0:
                g = abs(u - b) / b * 100.0
                gaps.append((g, m, u, b))
        gaps.sort(reverse=True)
        over = [x for x in gaps if x[0] > FEE_ROUNDTRIP]
        over_fee_counts.append(len(over))
        for g, m, u, b in gaps[:5]:
            big_gaps_all.setdefault(m, []).append(g)
        avg = sum(x[0] for x in gaps)/len(gaps) if gaps else 0
        print(f"[스냅 {s+1}] 평가 {len(gaps)}종 · 평균괴리 {avg:.3f}% · 수수료({FEE_ROUNDTRIP}%) 초과 {len(over)}종 · 최대 {gaps[0][0]:.2f}%({gaps[0][1]})" if gaps else f"[스냅 {s+1}] 데이터 없음")
        if s < snaps - 1:
            time.sleep(8)

    print()
    avg_over = sum(over_fee_counts)/len(over_fee_counts) if over_fee_counts else 0
    print(f"평균 '수수료 초과 괴리' 종목수: {avg_over:.1f} / {len(common)}")
    print("\n상위 괴리 종목(스냅 평균, 지속성 = 매번 떴나):")
    ranked = sorted(big_gaps_all.items(), key=lambda kv: -sum(kv[1])/len(kv[1]))[:8]
    for m, gs in ranked:
        print(f"  {m:14} 평균괴리 {sum(gs)/len(gs):.2f}% (포착 {len(gs)}/{snaps}회)")
    print("\n결론: '수수료 초과 괴리'가 꾸준히 多 = 차익 기회. 단 실현은 양 거래소 인벤토리 운용 필요.")


if __name__ == "__main__":
    main()
