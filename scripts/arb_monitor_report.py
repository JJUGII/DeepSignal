"""arb_monitor.py가 모은 원자료(arb_ticks.jsonl)를 분석 — 스프레드 수익 vs 재고 변동.

핵심 질문: "스프레드로 버는 기대치 > 재고(롱) 가격변동 리스크 인가?"
종목별로:
  - 스프레드: 순스프레드>임계인 시간비율, 평균/최대 순스프레드, 체결가능금액
  - 재고리스크: 업비트 mid 가격의 관측구간 변동성(표준편차%/최대낙폭%)
둘을 나란히 놓고, 메이저 vs 잡코인을 대조한다.

사용: ./.venv/bin/python scripts/arb_monitor_report.py [fee_pct] [min_size_krw]
"""
from __future__ import annotations
import json
import os
import statistics
import sys

FEE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.25      # 왕복 수수료% 가정
MIN_SIZE = float(sys.argv[2]) if len(sys.argv) > 2 else 50_000

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "output", "arb_monitor", "arb_ticks.jsonl")


def main() -> None:
    if not os.path.exists(PATH):
        print(f"기록 없음: {PATH}\n먼저 scripts/arb_monitor.py 를 돌리세요.")
        return
    per: dict[str, dict] = {}
    n_lines = 0
    with open(PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            n_lines += 1
            ua, ub, ba, bb = r["ua"], r["ub"], r["ba"], r["bb"]
            if min(ua, ub, ba, bb) <= 0:
                continue
            arbA = (ub - ba) / ba * 100 - FEE          # 빗썸매수→업비트매도(순)
            arbB = (bb - ua) / ua * 100 - FEE          # 업비트매수→빗썸매도(순)
            net = max(arbA, arbB)
            if arbA >= arbB:
                size = min(ba * r["bas"], ub * r["ubs"])
            else:
                size = min(ua * r["uas"], bb * r["bbs"])
            up_mid = (ua + ub) / 2                       # 재고는 업비트 보유 → 업비트 mid 기준
            d = per.setdefault(r["coin"], {
                "major": r["major"], "n": 0, "hit": 0, "nets": [], "sizes": [], "mids": [],
            })
            d["n"] += 1
            d["mids"].append(up_mid)
            if net > 0 and size >= MIN_SIZE:
                d["hit"] += 1
                d["nets"].append(net)
                d["sizes"].append(size)

    if not per:
        print("유효 데이터 없음.")
        return

    print(f"분석: {n_lines:,}줄 · 수수료 {FEE}% · 최소체결 {MIN_SIZE:,.0f}원\n")
    print(f"{'종목':<13}{'구분':<5}{'스냅':>5}{'기회%':>6}{'평균순%':>8}{'최대순%':>8}{'평균체결원':>11}{'재고변동σ%':>10}{'재고최대낙폭%':>12}")
    print("-" * 92)

    def row_sort(kv):
        # 메이저 먼저, 그다음 기회비율 높은 순
        coin, d = kv
        return (0 if d["major"] else 1, -(d["hit"] / d["n"] if d["n"] else 0))

    for coin, d in sorted(per.items(), key=row_sort):
        n = d["n"]
        if n < 2:
            continue
        hit_pct = d["hit"] / n * 100
        avg_net = statistics.mean(d["nets"]) if d["nets"] else 0.0
        max_net = max(d["nets"]) if d["nets"] else 0.0
        avg_sz = statistics.mean(d["sizes"]) if d["sizes"] else 0.0
        mids = d["mids"]
        mean_mid = statistics.mean(mids)
        vol = (statistics.pstdev(mids) / mean_mid * 100) if mean_mid else 0.0
        mdd = (max(mids) - min(mids)) / max(mids) * 100 if max(mids) else 0.0  # 관측구간 고저폭%
        tag = "메이저" if d["major"] else "잡코인"
        print(f"{coin:<13}{tag:<5}{n:>5}{hit_pct:>6.0f}{avg_net:>8.2f}{max_net:>8.2f}"
              f"{avg_sz:>11,.0f}{vol:>10.2f}{mdd:>12.2f}")

    print("\n해석:")
    print("  · 기회% = 순스프레드>0 & 체결가능한 스냅 비율 (높을수록 자주 먹을 기회)")
    print("  · 재고변동σ% / 최대낙폭% = 그 코인을 들고 있을 때의 가격 리스크 (낮을수록 안전)")
    print("  · 판정: '평균순%×기회빈도' 가 '재고 일중변동'을 압도해야 현물차익이 의미.")
    print("    메이저는 순%↓지만 재고변동↓ / 잡코인은 순%↑지만 재고변동↑ — 어느쪽이 유리한지 비교.")


if __name__ == "__main__":
    main()
