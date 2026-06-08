"""넓은 유니버스 백필 — 모멘텀 생존편향 재검증용.

심볼 파일(쉼표/줄바꿈 구분) + period를 받아 market_prices에 일봉 적재.
사용: PYTHONPATH=. ./.venv/bin/python scripts/backfill_universe.py <symbols_file> [period]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepsignal.collector.market.market_collector import MarketCollector
from deepsignal.config.settings import load_settings
from deepsignal.storage.database import insert_market_prices

path = sys.argv[1]
period = sys.argv[2] if len(sys.argv) > 2 else "15y"
raw = Path(path).read_text(encoding="utf-8")
syms = [s.strip().upper() for s in raw.replace("\n", ",").split(",") if s.strip()]
print(f"=== {len(syms)}종목 × {period} 백필 ===", flush=True)

db = load_settings().db_path
ok = fail = total = 0
coll = MarketCollector(symbols=syms, period=period, interval="1d")
for i, (sym, batch, err) in enumerate(coll.collect_per_symbol()):
    if err or not batch:
        fail += 1
        continue
    insert_market_prices(db, batch, timeframe="1d")
    total += len(batch)
    ok += 1
    if (i + 1) % 50 == 0:
        print(f"  ...{i+1}/{len(syms)} 처리 (성공 {ok}, 누적 {total}봉)", flush=True)
print(f"--- 완료: 성공 {ok} / 실패 {fail} / 수집 {total}봉 ---", flush=True)
