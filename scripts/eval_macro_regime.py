"""거시 레짐 타이밍 리서치 — 100년 거시 데이터로 OOS 검증.

규칙 고정(미적합)이므로 전체 표본 OOS. 인덱스(S&P500)에 대해:
- buyhold : 항상 보유
- sma200  : 전일 종가 > 200일 SMA면 보유, 아니면 현금(US13W 단기금리)  ← 추세추종
- vix20   : 전일 VIX < 20이면 보유, 아니면 현금                       ← 변동성 레짐
- combo   : sma200 AND vix<25 일 때만 보유

벤치마크(buyhold) 대비 CAGR·Sharpe·MDD. 추세추종은 보통 수익은 비슷하되 MDD를 크게 줄여
Sharpe를 높인다(2008·2000 회피) — 진짜 레짐 엣지인지 확인.

사용: PYTHONPATH=. ./.venv/bin/python scripts/eval_macro_regime.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlite3
import numpy as np
import pandas as pd

from deepsignal.config.settings import load_settings


def load_ind(conn, name) -> pd.Series:
    rows = conn.execute(
        "SELECT indicator_date, value FROM economic_indicators "
        "WHERE indicator_name=? AND value IS NOT NULL ORDER BY indicator_date", (name,)).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r[0][:10] for r in rows])
    s = pd.Series([float(r[1]) for r in rows], index=idx)
    return s[~s.index.duplicated(keep="last")]


def perf(daily_ret: pd.Series, label: str) -> dict:
    r = daily_ret.dropna()
    if len(r) < 252:
        return {}
    yrs = len(r) / 252
    eq = (1 + r).cumprod()
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    sharpe = (r.mean() / (r.std() + 1e-12)) * np.sqrt(252)
    mdd = (eq / eq.cummax() - 1).min()
    return {"label": label, "cagr": float(cagr), "sharpe": float(sharpe),
            "mdd": float(mdd), "yrs": round(yrs, 1)}


def main():
    conn = sqlite3.connect(load_settings().db_path)
    sp = load_ind(conn, "SP500")
    vix = load_ind(conn, "VIX")
    tb = load_ind(conn, "US13W")  # 단기금리 %(연율)
    conn.close()
    if sp.empty:
        print("SP500 데이터 없음")
        return

    df = pd.DataFrame({"sp": sp}).sort_index()
    df["ret"] = df["sp"].pct_change()
    df["sma200"] = df["sp"].rolling(200).mean()
    df["vix"] = vix.reindex(df.index).ffill()
    df["tb"] = tb.reindex(df.index).ffill()
    df["cash_ret"] = (df["tb"] / 100.0) / 252.0  # 일간 현금수익

    # 전일 신호로 당일 포지션 결정 (look-ahead 없음)
    inmkt = df["sp"].shift(1) > df["sma200"].shift(1)
    vix_lo = df["vix"].shift(1) < 20
    vix_lo25 = df["vix"].shift(1) < 25

    def strat(mask):
        # mask=True면 시장수익, False면 현금수익
        return np.where(mask.fillna(False), df["ret"], df["cash_ret"])

    results = []
    results.append(perf(df["ret"], "buyhold"))
    results.append(perf(pd.Series(strat(inmkt), index=df.index), "sma200(추세추종)"))
    # VIX 전략은 VIX 존재 구간만
    has_vix = df["vix"].notna()
    results.append(perf(pd.Series(strat(vix_lo), index=df.index)[has_vix], "vix<20"))
    results.append(perf(pd.Series(strat(inmkt & vix_lo25), index=df.index)[has_vix], "combo(추세+vix<25)"))

    bh = results[0]
    print(f"=== S&P500 거시 레짐 타이밍 ({df.index[0].date()}~{df.index[-1].date()}) ===")
    print(f"{'전략':22} {'CAGR':>7} {'Sharpe':>7} {'MDD':>8} {'기간':>6}  vs벤치")
    for p in results:
        if not p:
            continue
        edge = "" if p["label"] == "buyhold" else f"  Sharpe {p['sharpe']-bh['sharpe']:+.2f}"
        flag = ""
        if p["label"] != "buyhold" and p["sharpe"] > bh["sharpe"] + 0.15:
            flag = "  ✅엣지후보"
        print(f"{p['label']:22} {p['cagr']*100:6.1f}% {p['sharpe']:7.2f} {p['mdd']*100:7.1f}% {p['yrs']:6.1f}{edge}{flag}")
    print("\n참고: 추세추종은 보통 CAGR 비슷·MDD 대폭 감소로 Sharpe↑. ✅=Sharpe 벤치 +0.15 초과.")


if __name__ == "__main__":
    main()
