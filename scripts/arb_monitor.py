"""업비트·빗썸 거래소간 차익 모니터 — read-only 실측 수집기 (실주문 없음).

목적: "현물 차익이 진짜 돈 되나"를 실거래 없이 데이터로 판정.
초안 검증의 함정(잡코인 스프레드만 보고 좋아보임)을 피해, 메이저 중심으로
*스프레드 수익*과 *재고 가격변동(롱 노출 리스크)*을 함께 기록한다.

매 스냅샷마다 양 거래소 호가창(최우선호가)을 동시에 떠서 코인별 1줄씩 JSONL append.
순스프레드·체결가능금액·재고변동은 분석은 arb_monitor_report.py가 사후 계산(원자료 보존).

사용:
  ./.venv/bin/python scripts/arb_monitor.py [interval_sec]
  (기본 30초. Ctrl-C 또는 launchd로 며칠 돌린 뒤 arb_monitor_report.py로 분석)
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

UP = "https://api.upbit.com/v1"
BI = "https://api.bithumb.com/v1"
INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 30

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "output", "arb_monitor")
OUT_PATH = os.path.join(OUT_DIR, "arb_ticks.jsonl")

# 재고로 "들고 있을 만한" 메이저 우선. 잡코인은 대조군으로 소수만.
MAJORS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA", "KRW-DOGE",
    "KRW-TRX", "KRW-AVAX", "KRW-LINK", "KRW-DOT", "KRW-BCH", "KRW-SUI",
    "KRW-XLM", "KRW-ETC", "KRW-HBAR",
]
# 대조군(고스프레드 잡코인) — 스프레드 커도 재고변동 큰지 비교용
ALTS_CONTRAST = ["KRW-LAYER", "KRW-VVV", "KRW-PLUME", "KRW-BABY", "KRW-SKR"]


def krw_markets(base: str) -> set[str]:
    try:
        data = requests.get(f"{base}/market/all", timeout=15).json()
        return {m["market"] for m in data if str(m.get("market", "")).startswith("KRW-")}
    except Exception:
        return set()


def top_orderbook(base: str, markets: list[str]) -> dict[str, tuple]:
    """각 마켓 최우선호가 (ask_price, bid_price, ask_size, bid_size)."""
    out: dict[str, tuple] = {}
    for i in range(0, len(markets), 80):
        chunk = ",".join(markets[i:i + 80])
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


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    common = krw_markets(UP) & krw_markets(BI)
    watch = [m for m in (MAJORS + ALTS_CONTRAST) if m in common]
    majors = set(MAJORS)
    print(f"[arb_monitor] 감시 {len(watch)}종 (메이저 {sum(m in majors for m in watch)} + 대조 {sum(m not in majors for m in watch)})")
    print(f"[arb_monitor] interval={INTERVAL}s · 기록 → {OUT_PATH}")
    print("[arb_monitor] read-only · 실주문 없음 · Ctrl-C로 종료\n")

    n = 0
    while True:
        loop_start = time.time()
        up = top_orderbook(UP, watch)
        bi = top_orderbook(BI, watch)
        ts = datetime.now(timezone.utc).isoformat()
        rows = 0
        best_line = ""
        best_net = -99.0
        with open(OUT_PATH, "a") as f:
            for m in watch:
                if m not in up or m not in bi:
                    continue
                ua, ub, uas, ubs = up[m]
                ba, bb, bas, bbs = bi[m]
                if min(ua, ub, ba, bb) <= 0:
                    continue
                # gross 스프레드(수수료 전). 방향: A 빗썸매수→업비트매도 / B 업비트매수→빗썸매도
                arbA = (ub - ba) / ba * 100
                arbB = (bb - ua) / ua * 100
                f.write(json.dumps({
                    "ts": ts, "coin": m, "major": m in majors,
                    "ua": ua, "ub": ub, "ba": ba, "bb": bb,
                    "uas": uas, "ubs": ubs, "bas": bas, "bbs": bbs,
                }, ensure_ascii=False) + "\n")
                rows += 1
                gross = max(arbA, arbB)
                if gross > best_net:
                    best_net = gross
                    d = "빗썸→업비트" if arbA >= arbB else "업비트→빗썸"
                    best_line = f"{m} gross {gross:+.2f}% ({d})"
        n += 1
        print(f"[{ts}] #{n} {rows}종 기록 · 최대 gross {best_line}")
        # interval 보정(요청 시간 차감)
        sleep = INTERVAL - (time.time() - loop_start)
        if sleep > 0:
            time.sleep(sleep)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[arb_monitor] 종료. arb_monitor_report.py로 분석하세요.")
