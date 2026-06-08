"""팩터 엣지 리서치 — 50년 일봉으로 고전 팩터를 OOS 백테스트.

룰 고정(데이터에 미적합)이라 전체 표본이 OOS. 월간 리밸런스, long-only.
벤치마크(동일비중 buy&hold) 대비 연수익·Sharpe·MDD로 엣지 판정.

팩터:
- ts_mom   : 12-1개월 수익률 > 0 인 종목만 보유 (시계열/절대 모멘텀, 추세추종)
- xs_mom   : 12-1개월 수익률 상위 1/3 (횡단면 모멘텀)
- st_rev   : 직전 1개월 수익률 하위 1/3 (단기 리버설, 패자 매수)
- low_vol  : 트레일링 126일 변동성 하위 1/3 (저변동성)

사용: PYTHONPATH=. ./.venv/bin/python scripts/eval_factors.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlite3
import numpy as np
import pandas as pd

from deepsignal.config.settings import load_settings

FEE = {"US": 0.001, "KR": 0.0025}  # 리밸런스 회전율당 왕복 비용 근사
MIN_NAMES = 8                       # 횡단면 최소 종목수


def load_panel(conn, korean: bool) -> pd.DataFrame:
    syms = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM market_prices WHERE timeframe='1d'")]
    syms = [s for s in syms if (".K" in s) == korean]
    frames = {}
    for s in syms:
        rows = conn.execute(
            "SELECT bar_time, COALESCE(adjusted_close, close) FROM market_prices "
            "WHERE symbol=? AND timeframe='1d' ORDER BY bar_time", (s,)).fetchall()
        if len(rows) < 300:
            continue
        idx = pd.to_datetime([r[0][:10] for r in rows])
        frames[s] = pd.Series([float(r[1]) for r in rows], index=idx)
    px = pd.DataFrame(frames).sort_index()
    px = px[~px.index.duplicated(keep="last")]
    return px


def perf(monthly_ret: pd.Series) -> dict:
    r = monthly_ret.dropna()
    if len(r) < 12:
        return {}
    ann_ret = (1 + r).prod() ** (12 / len(r)) - 1
    sharpe = (r.mean() / (r.std() + 1e-12)) * np.sqrt(12)
    eq = (1 + r).cumprod()
    mdd = (eq / eq.cummax() - 1).min()
    return {"ann_ret": float(ann_ret), "sharpe": float(sharpe), "mdd": float(mdd), "months": int(len(r))}


def run_market(label: str, px: pd.DataFrame, fee: float):
    # 월말 가격 + 월간 수익률
    m = px.resample("ME").last()
    mret = m.pct_change()
    # 일간 수익률(변동성용)
    dret = px.pct_change()
    vol126 = dret.rolling(126).std().resample("ME").last()
    # 모멘텀: 12-1개월 (최근 1개월 스킵)
    mom = m.shift(1) / m.shift(12) - 1.0
    rev1 = m / m.shift(1) - 1.0  # 직전 1개월

    dates = m.index
    factors = {"bench": [], "ts_mom": [], "xs_mom": [], "st_rev": [], "low_vol": []}
    prev_w = {k: pd.Series(dtype=float) for k in factors}

    def book(name, w, t):
        nonlocal prev_w
        fwd = mret.loc[t]  # 이번 달 실현 수익률(t-1→t). t시점 가중은 t-1에 결정됨 → 아래서 shift 처리
        gross = float((w * fwd).sum()) if len(w) else 0.0
        turn = float((w.subtract(prev_w[name], fill_value=0).abs().sum()))
        factors[name].append(gross - fee * turn)
        prev_w[name] = w

    for i in range(1, len(dates)):
        t = dates[i]
        tprev = dates[i - 1]
        avail = m.loc[tprev].dropna().index  # tprev에 가격 있는 종목 (t-1 시점 정보로 가중 결정)
        if len(avail) < MIN_NAMES:
            for k in factors:
                factors[k].append(np.nan)
            continue
        ew = pd.Series(1.0 / len(avail), index=avail)
        book("bench", ew, t)
        # ts_mom: tprev 시점 12-1 모멘텀>0
        mvals = mom.loc[tprev, avail].dropna()
        win = mvals[mvals > 0].index
        book("ts_mom", pd.Series(1.0 / len(win), index=win) if len(win) else pd.Series(dtype=float), t)
        # xs_mom: 상위 1/3
        if len(mvals) >= MIN_NAMES:
            top = mvals.nlargest(max(1, len(mvals) // 3)).index
            book("xs_mom", pd.Series(1.0 / len(top), index=top), t)
        else:
            book("xs_mom", ew, t)
        # st_rev: 직전 1개월 하위 1/3
        rvals = rev1.loc[tprev, avail].dropna()
        if len(rvals) >= MIN_NAMES:
            los = rvals.nsmallest(max(1, len(rvals) // 3)).index
            book("st_rev", pd.Series(1.0 / len(los), index=los), t)
        else:
            book("st_rev", ew, t)
        # low_vol: 변동성 하위 1/3
        vvals = vol126.loc[tprev, avail].dropna()
        if len(vvals) >= MIN_NAMES:
            lo = vvals.nsmallest(max(1, len(vvals) // 3)).index
            book("low_vol", pd.Series(1.0 / len(lo), index=lo), t)
        else:
            book("low_vol", ew, t)

    print(f"\n=== [{label}] {px.shape[1]}종목 | {m.index[0].date()}~{m.index[-1].date()} | 수수료 {fee*100:.2f}%/회전 ===")
    print(f"{'팩터':10} {'연수익':>8} {'Sharpe':>8} {'MDD':>8} {'개월':>6}  vs벤치")
    bench = perf(pd.Series(factors["bench"], index=dates[1:]))
    for k in factors:
        p = perf(pd.Series(factors[k], index=dates[1:]))
        if not p:
            continue
        edge = "" if k == "bench" else (
            f"  Sharpe {p['sharpe']-bench['sharpe']:+.2f} / 연{(p['ann_ret']-bench['ann_ret'])*100:+.1f}%p")
        flag = ""
        if k != "bench" and p["sharpe"] > bench["sharpe"] + 0.15:
            flag = "  ✅엣지후보"
        print(f"{k:10} {p['ann_ret']*100:7.1f}% {p['sharpe']:8.2f} {p['mdd']*100:7.1f}% {p['months']:6d}{edge}{flag}")


def main():
    conn = sqlite3.connect(load_settings().db_path)
    for label, kor in (("미국주식", False), ("한국주식", True)):
        px = load_panel(conn, kor)
        if px.shape[1] >= MIN_NAMES:
            run_market(label, px, FEE["US" if not kor else "KR"])
        else:
            print(f"[{label}] 종목 부족 {px.shape[1]}")
    conn.close()
    print("\n참고: 벤치마크=동일비중 buy&hold. ✅=Sharpe가 벤치 +0.15 초과(엣지 후보).")


if __name__ == "__main__":
    main()
