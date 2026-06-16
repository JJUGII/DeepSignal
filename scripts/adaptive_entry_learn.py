"""적응형 진입기준 학습 — 매일 데이터로 '이기는 진입 등락률대'를 재계산.

kr_movers 신호 + KIS 다음날 종가로, 진입 등락률대별 승률·평균수익을 구해
가장 좋은 구간을 outputs/ADAPTIVE_ENTRY_BAND.json 에 쓴다. 스캐너가 이 파일을
읽으면(KR_SCANNER_ADAPTIVE=true) sweet band가 매일 자동 갱신된다.

표본 부족하면 갱신 안 함(정적 기본 유지). 매일 launchd로 실행.
사용: ./.venv/bin/python scripts/adaptive_entry_learn.py [lookback_days]
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _l in open(".env") if Path(".env").is_file() else []:
    if _l.strip() and "=" in _l and not _l.startswith("#"):
        _k, _v = _l.strip().split("=", 1)
        os.environ.setdefault(_k, _v)

LOOKBACK = int(sys.argv[1]) if len(sys.argv) > 1 else 10
OUT = Path("outputs/ADAPTIVE_ENTRY_BAND.json")
MIN_N = 12             # 이보다 표본 적으면 갱신 안 함
BANDS = [(0, 5), (5, 10), (10, 15), (15, 100)]


def _kis_daily(cfg, b, sym, d0):
    import requests
    for _ in range(4):
        try:
            d = requests.get(f"{cfg.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                headers=b._inquire_headers("FHKST03010100"),
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": sym, "FID_INPUT_DATE_1": d0,
                        "FID_INPUT_DATE_2": datetime.now().strftime("%Y%m%d"),
                        "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}, timeout=8).json()
            if str(d.get("msg_cd")) == "EGW00201":
                time.sleep(0.6); continue
            return sorted([(x.get("stck_bsop_date"), float(x.get("stck_clpr") or 0))
                           for x in (d.get("output2") or []) if x.get("stck_clpr")])
        except Exception:
            time.sleep(0.4)
    return []


def main() -> None:
    import sqlite3
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.kis_config import load_kis_config_from_env
    from deepsignal.live_trading.broker.kis_broker import KISBroker

    cfg = load_kis_config_from_env(load_dotenv_file=False)
    b = KISBroker(cfg)
    c = sqlite3.connect(load_settings().db_path)
    cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = c.execute(
        "SELECT created_at, signal_date, symbol, raw_json FROM signals "
        "WHERE strategy_name='kr_movers_v1' AND signal_date >= date(?, ?) ORDER BY created_at",
        (cutoff, f"-{LOOKBACK} days"),
    ).fetchall()

    samples = []  # (chg, fwd_ret)
    seen = set()
    for created, sdate, sym, raw in rows:
        if (sym, sdate) in seen:
            continue
        seen.add((sym, sdate))
        try:
            j = json.loads(raw); chg = float(j.get("change_pct") or 0); px0 = float(j.get("price") or 0)
        except Exception:
            continue
        if px0 <= 0:
            continue
        px = _kis_daily(cfg, b, sym, sdate.replace("-", "")); time.sleep(0.2)
        if len(px) < 2:
            continue
        fwd = (px[1][1] - px0) / px0 * 100
        samples.append((chg, fwd))
        if len(samples) >= 80:
            break

    by = defaultdict(list)
    for chg, fwd in samples:
        for lo, hi in BANDS:
            if lo <= chg < hi:
                by[(lo, hi)].append(fwd)
                break

    stats = {}
    best = None
    for band in BANDS:
        v = by.get(band, [])
        if not v:
            continue
        avg = statistics.mean(v); wr = sum(1 for x in v if x > 0) / len(v) * 100
        stats[f"{band[0]}-{band[1]}"] = {"n": len(v), "avg_ret": round(avg, 2), "win_rate": round(wr, 1)}
        if len(v) >= 4 and avg > 0 and (best is None or avg > best[1]):
            best = (band, avg, wr, len(v))

    print(f"학습 표본 {len(samples)}건 · 구간별: {json.dumps(stats, ensure_ascii=False)}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if len(samples) >= MIN_N and best:
        band, avg, wr, n = best
        payload = {"sweet_min": float(band[0]), "sweet_max": float(band[1]),
                   "avg_ret": round(avg, 2), "win_rate": round(wr, 1), "n": n,
                   "sample_total": len(samples), "updated": datetime.now(timezone.utc).isoformat(),
                   "all_bands": stats}
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        print(f"✅ 적응형 밴드 갱신: +{band[0]}~{band[1]}% (평균 {avg:+.2f}%·승률 {wr:.0f}%·N={n}) → {OUT}")
    else:
        print(f"표본 부족({len(samples)}<{MIN_N}) 또는 양수밴드 없음 — 정적 기본(5~10) 유지, 미갱신")


if __name__ == "__main__":
    main()
