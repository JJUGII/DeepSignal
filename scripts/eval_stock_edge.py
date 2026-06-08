"""주식 일봉 전략 엣지 평가 (정직한 OOS).

SignalScorer의 기술점수가 선행 수익률을 예측하는지 평가한다.
룰기반(파라미터 미적합)이므로 전체 표본이 OOS. 인과적 지표(EMA/RSI)라 look-ahead 없음.
미국/한국 시장을 분리(수수료 차이). 임계값별 평균 순수익·승률·Sharpe.

사용: PYTHONPATH=. ./.venv/bin/python scripts/eval_stock_edge.py [horizon_days]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlite3
import numpy as np

from deepsignal.analyzer.technical.technical_analyzer import TechnicalAnalyzer
from deepsignal.scoring.signal_scorer import SignalScorer
from deepsignal.config.settings import load_settings

H = int(sys.argv[1]) if len(sys.argv) > 1 else 5  # 보유 거래일
FEE = {"US": 0.001, "KR": 0.0025}  # 왕복 수수료+세금+슬리피지 근사
MIN_TRADES = 100


def load_rows(conn, symbol):
    cur = conn.execute(
        "SELECT bar_time, open, high, low, close, volume FROM market_prices "
        "WHERE symbol=? AND timeframe='1d' ORDER BY bar_time", (symbol,))
    return [dict(zip(("bar_time", "open", "high", "low", "close", "volume"), r)) for r in cur.fetchall()]


def collect(conn, symbols):
    ta, sc = TechnicalAnalyzer(), SignalScorer()
    scores, rets = [], []
    for sym in symbols:
        rows = load_rows(conn, sym)
        if len(rows) < 40 + H:
            continue
        inds = ta.analyze_prices(sym, rows)
        closes = [i.close for i in inds]
        for i in range(len(inds) - H):
            s = sc.score_technical(inds[i])
            c0, cj = closes[i], closes[i + H]
            if s is None or c0 is None or cj is None or c0 <= 0:
                continue
            scores.append(float(s))
            rets.append(cj / c0 - 1.0)
    return np.array(scores), np.array(rets)


def sweep(label, scores, rets, fee):
    if len(scores) == 0:
        print(f"\n[{label}] 데이터 없음")
        return
    net_all = rets - fee
    print(f"\n=== [{label}] 샘플 {len(scores)} | 전체 평균순익 {net_all.mean()*100:+.3f}% | "
          f"점수분포 p50={np.percentile(scores,50):.0f} p80={np.percentile(scores,80):.0f} p95={np.percentile(scores,95):.0f} ===")
    print("점수≥  통과율  거래수   승률   평균순익%   Sharpe")
    best = None
    for th in [50, 55, 60, 65, 70, 75, 80]:
        sel = scores >= th
        n = int(sel.sum())
        if n == 0:
            print(f" {th:3d}    0%  (통과 없음)")
            continue
        net = rets[sel] - fee
        win = (rets[sel] > fee).mean()
        sharpe = net.mean() / (net.std() + 1e-9)
        mark = " *" if n >= MIN_TRADES else " (적음)"
        if n >= MIN_TRADES and (best is None or net.mean() > best[1]):
            best = (th, float(net.mean()), n, float(win), float(sharpe))
        print(f" {th:3d}  {sel.mean()*100:5.1f}%  {n:6d}  {win:.3f}  {net.mean()*100:+7.3f}   {sharpe:+.3f}{mark}")
    if best:
        print(f" ==> 최고 평균순익(거래≥{MIN_TRADES}): 점수≥{best[0]}, 순익 {best[1]*100:+.3f}%/{H}일, 승률 {best[3]:.3f}, Sharpe {best[4]:+.3f}")
    else:
        print(" ==> 충분한 거래수의 임계값 없음")


def main():
    conn = sqlite3.connect(load_settings().db_path)
    syms = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM market_prices WHERE timeframe='1d'")]
    us = [s for s in syms if ".K" not in s]
    kr = [s for s in syms if ".K" in s]
    print(f"보유기간 H={H}거래일 | 미국 {len(us)}종목 / 한국 {len(kr)}종목")
    su, ru = collect(conn, us)
    sk, rk = collect(conn, kr)
    sweep("미국주식", su, ru, FEE["US"])
    sweep("한국주식", sk, rk, FEE["KR"])
    # 벤치마크: 무작위 진입(전체) 평균
    print(f"\n[벤치마크] 무작위 {H}일 보유 평균순익: "
          f"미국 {(ru.mean()-FEE['US'])*100:+.3f}% / 한국 {(rk.mean()-FEE['KR'])*100:+.3f}%")


if __name__ == "__main__":
    main()
