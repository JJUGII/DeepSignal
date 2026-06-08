"""주식 과거 일봉 백필 — 엣지 리서치용 데이터 확보.

미국(yfinance 티커) + 한국(.KS/.KQ) 종목을 다년 일봉으로 수집해 market_prices에 적재.
사용: PYTHONPATH=. ./.venv/bin/python scripts/backfill_stock_data.py [period]
  period 기본 5y (예: 10y, max)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepsignal.collector.market.market_collector import MarketCollector
from deepsignal.storage.database import insert_market_prices
from deepsignal.config.settings import load_settings

PERIOD = sys.argv[1] if len(sys.argv) > 1 else "5y"

US = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AMD", "AVGO", "NFLX",
    "ADBE", "CRM", "ORCL", "INTC", "QCOM", "CSCO", "TXN", "AMAT", "MU", "PYPL",
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLI",
    "XLP", "XLU", "XLB", "GLD", "TLT",
]
# 한국 KOSPI/KOSDAQ 대형주 (yfinance .KS / .KQ)
KR = [
    "005930.KS", "000660.KS", "035420.KS", "035720.KS", "005380.KS", "000270.KS",
    "051910.KS", "006400.KS", "207940.KS", "005490.KS", "068270.KS", "105560.KS",
    "055550.KS", "012330.KS", "028260.KS", "066570.KS", "003550.KS", "015760.KS",
    "017670.KS", "030200.KS", "096770.KS", "034730.KS", "003670.KS", "010130.KS",
    "011200.KS", "009150.KS", "032830.KS", "086790.KS", "316140.KS", "259960.KS",
    "247540.KQ", "086520.KQ", "091990.KQ", "066970.KQ", "028300.KQ",
]


def backfill(label: str, symbols: list[str]) -> None:
    print(f"\n=== {label}: {len(symbols)}종목 × {PERIOD} 일봉 수집 ===", flush=True)
    db = load_settings().db_path
    coll = MarketCollector(symbols=symbols, period=PERIOD, interval="1d")
    total_ins = total_bars = ok = fail = 0
    for sym, batch, err in coll.collect_per_symbol():
        if err or not batch:
            fail += 1
            print(f"  ✗ {sym}: {err or 'empty'}", flush=True)
            continue
        res = insert_market_prices(db, batch, timeframe="1d")
        ins = res.get("inserted", 0)
        total_ins += ins
        total_bars += len(batch)
        ok += 1
        print(f"  ✓ {sym}: {len(batch)}봉 (신규 {ins})", flush=True)
    print(f"--- {label} 완료: 성공 {ok} / 실패 {fail} / 수집 {total_bars}봉 / 신규삽입 {total_ins} ---", flush=True)


def main() -> None:
    backfill("미국주식", US)
    backfill("한국주식", KR)
    print("\n백필 종료.", flush=True)


if __name__ == "__main__":
    main()
