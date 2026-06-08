"""거시지표 과거 시계열 백필 — 팩터/레짐 리서치용.

yfinance로 거시·시장 시리즈를 전체 history로 수집해 economic_indicators에 적재.
일일 사이클에서는 period=1mo로 호출해 최신분만 append(INSERT OR IGNORE 중복제거).

뉴스 과거 데이터는 RSS가 최근만 제공 → 백필 불가. 전방(forward) 축적만 가능(collect-news).

사용: PYTHONPATH=. ./.venv/bin/python scripts/backfill_macro_data.py [period]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yfinance as yf

from deepsignal.collector.economic.economic_collector import EconomicIndicator
from deepsignal.config.settings import load_settings
from deepsignal.storage.database import insert_economic_indicators

PERIOD = sys.argv[1] if len(sys.argv) > 1 else "max"

# (yfinance 티커, 표준 지표명)
MACRO = [
    ("^VIX", "VIX"),          # 변동성지수
    ("DX-Y.NYB", "DXY"),      # 달러인덱스
    ("^TNX", "US10Y"),        # 미국채 10년 금리
    ("^IRX", "US13W"),        # 미국 13주 단기금리
    ("^GSPC", "SP500"),       # S&P500
    ("^KS11", "KOSPI"),       # 코스피
    ("^KQ11", "KOSDAQ"),      # 코스닥
    ("KRW=X", "USDKRW"),      # 원/달러
    ("GC=F", "GOLD"),         # 금 선물
    ("CL=F", "WTI"),          # WTI 원유
    ("^TYX", "US30Y"),        # 미국채 30년
    ("BTC-USD", "BTCUSD"),    # 비트코인(거시 위험선호 프록시)
]


def _date_str(idx) -> str:
    try:
        return idx.strftime("%Y-%m-%d")
    except Exception:
        return str(idx)[:10]


def backfill() -> None:
    print(f"=== 거시지표 {len(MACRO)}개 × {PERIOD} 수집 ===", flush=True)
    total_ins = ok = fail = 0
    for tkr, name in MACRO:
        try:
            hist = yf.Ticker(tkr).history(period=PERIOD, interval="1d", auto_adjust=False)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}({tkr}): {e}", flush=True)
            fail += 1
            continue
        if hist is None or hist.empty:
            print(f"  ✗ {name}({tkr}): empty", flush=True)
            fail += 1
            continue
        items = []
        for idx, row in hist.iterrows():
            try:
                val = float(row["Close"])
            except (KeyError, TypeError, ValueError):
                continue
            if val != val:  # NaN
                continue
            items.append(EconomicIndicator(indicator_name=name, indicator_date=_date_str(idx),
                                           value=val, source="yfinance", raw={"ticker": tkr}))
        res = insert_economic_indicators(load_settings().db_path, items)
        ins = res.get("inserted", 0)
        total_ins += ins
        ok += 1
        print(f"  ✓ {name}: {len(items)}일 (신규 {ins}) {items[0].indicator_date}~{items[-1].indicator_date}" if items
              else f"  ~ {name}: 파싱 0", flush=True)
    print(f"--- 거시 완료: 성공 {ok} / 실패 {fail} / 신규삽입 {total_ins} ---", flush=True)


if __name__ == "__main__":
    backfill()
