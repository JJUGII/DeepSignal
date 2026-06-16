"""로컬 매매 장부 동기화 + 손익 리포트. (launchd 매일 / 수동 실행)

내 실제 체결을 trade_ledger.db에 누적하고, 매수·매도·실현손익을 정확히 보여준다.
사용: ./.venv/bin/python scripts/trade_ledger_sync.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepsignal.ledger.trade_ledger import sync_all


def main() -> None:
    r = sync_all()
    print("=== 로컬 매매 장부 동기화 ===")
    print(f"  신규 기록: 코인 {r['ingested_crypto']} · 국내 {r['ingested_domestic']} · 누적 fill {r['ledger_rows']}건\n")
    print("=== 내 실제 손익 (FIFO 실현) ===")
    print(f"  총 매수금액:   {r['bought_krw']:>12,}원")
    print(f"  총 매도금액:   {r['sold_krw']:>12,}원")
    print(f"  실현손익:      {r['realized_pnl_krw']:>+12,}원")
    print(f"    └ 번 것:     {r['gained_krw']:>+12,}원  ({r['win_count']}건)")
    print(f"    └ 잃은 것:   {r['lost_krw']:>+12,}원  ({r['loss_count']}건)")
    print(f"  청산 {r['closed_lots']}건 · 승률 {r['win_rate_pct']}%")
    if r.get("by_asset"):
        print("  자산군별 실현손익:")
        for a, v in r["by_asset"].items():
            print(f"    {a:<10} {v:>+12,}원")


if __name__ == "__main__":
    main()
