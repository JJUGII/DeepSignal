"""DM1 검증 — regime 3-상태(롱/현금/인버스) 숏레그가 실제로 수익인가.

질문: S&P500 200일선 하회 시 인버스 ETF 보유가 (a)롱전용 대비 Sharpe개선,
(b)MDD 악화없음, (c)거래비용+인버스 decay 반영 후 양(+)인가? 베어마켓 랠리에서
피흘리는가? 이 답이 '하락장 수익' 달성 가능여부 자체를 결정한다.

정직성 장치:
- 모든 신호는 .shift(1) (look-ahead 없음).
- 인버스 일일수익 = -ret - 보수(연0.9%/252). 일일복리라 변동성 decay 자동 반영.
- 상태 전환마다 왕복 거래비용(기본 0.10%) 차감.
- 단일 파라미터 체리피킹 방지: (band, persist) 그리드 + 숏-only 기여 분해.

사용: PYTHONPATH=. ./.venv/bin/python scripts/eval_regime_inverse.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlite3
import numpy as np
import pandas as pd

from deepsignal.config.settings import load_settings

INVERSE_EXPENSE_ANNUAL = 0.009   # SH류 1X 인버스 보수 ~0.9%/yr
SWITCH_COST = 0.0010             # 상태 전환 1회 왕복 비용(수수료+슬리피지) 0.10%
SLOPE_LOOKBACK = 20              # SMA200 기울기 측정 구간(일)


def load_ind(conn, name) -> pd.Series:
    rows = conn.execute(
        "SELECT indicator_date, value FROM economic_indicators "
        "WHERE indicator_name=? AND value IS NOT NULL ORDER BY indicator_date", (name,)).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r[0][:10] for r in rows])
    s = pd.Series([float(r[1]) for r in rows], index=idx)
    return s[~s.index.duplicated(keep="last")]


def perf(daily_ret: pd.Series, label: str, n_switches: int = 0) -> dict:
    r = daily_ret.dropna()
    if len(r) < 252:
        return {}
    yrs = len(r) / 252
    eq = (1 + r).cumprod()
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    sharpe = (r.mean() / (r.std() + 1e-12)) * np.sqrt(252)
    mdd = (eq / eq.cummax() - 1).min()
    return {"label": label, "cagr": float(cagr), "sharpe": float(sharpe),
            "mdd": float(mdd), "yrs": round(yrs, 1), "switches": n_switches}


def consecutive_below(below: pd.Series) -> pd.Series:
    """c < s 연속일수. (불리언 시리즈 → 연속 True 카운트)"""
    out = np.zeros(len(below), dtype=int)
    cnt = 0
    vals = below.fillna(False).to_numpy()
    for i, b in enumerate(vals):
        cnt = cnt + 1 if b else 0
        out[i] = cnt
    return pd.Series(out, index=below.index)


def build_states(df: pd.DataFrame, band: float, persist: int) -> pd.Series:
    """일별 상태 LONG/CASH/SHORT (전일 신호 기준, look-ahead 없음)."""
    c1, s1 = df["sp"].shift(1), df["sma200"].shift(1)
    slope1 = df["slope"].shift(1)
    below1 = df["days_below"].shift(1)
    long_sig = c1 > s1
    short_sig = (c1 < s1 * (1 - band)) & (slope1 < 0) & (below1 >= persist)
    state = pd.Series("CASH", index=df.index)
    state[long_sig.fillna(False)] = "LONG"
    state[short_sig.fillna(False)] = "SHORT"  # short_sig는 long_sig와 상호배타(c1<s1<s1면 long거짓)
    return state


def state_returns(df: pd.DataFrame, state: pd.Series) -> tuple[pd.Series, int]:
    """상태별 일일수익 + 전환횟수. 전환일엔 왕복비용 차감."""
    mkt = df["ret"]
    cash = df["cash_ret"]
    inv = -df["ret"] - (INVERSE_EXPENSE_ANNUAL / 252.0)
    ret = pd.Series(np.nan, index=df.index)
    ret[state == "LONG"] = mkt[state == "LONG"]
    ret[state == "CASH"] = cash[state == "CASH"]
    ret[state == "SHORT"] = inv[state == "SHORT"]
    switched = state != state.shift(1)
    switched.iloc[0] = False
    ret = ret - switched.astype(float) * SWITCH_COST
    return ret, int(switched.sum())


def main():
    conn = sqlite3.connect(load_settings().db_path)
    sp = load_ind(conn, "SP500")
    tb = load_ind(conn, "US13W")
    conn.close()
    if sp.empty:
        print("SP500 데이터 없음")
        return

    df = pd.DataFrame({"sp": sp}).sort_index()
    df["ret"] = df["sp"].pct_change()
    df["sma200"] = df["sp"].rolling(200).mean()
    df["slope"] = df["sma200"] - df["sma200"].shift(SLOPE_LOOKBACK)
    df["days_below"] = consecutive_below(df["sp"] < df["sma200"])
    df["tb"] = tb.reindex(df.index).ffill()
    df["cash_ret"] = (df["tb"].fillna(0) / 100.0) / 252.0

    span = f"{df.index[0].date()}~{df.index[-1].date()}"
    print(f"=== DM1 숏레그 검증: S&P500 3-상태 ({span}) ===")
    print(f"가정: 인버스보수 {INVERSE_EXPENSE_ANNUAL*100:.1f}%/yr, 전환비용 {SWITCH_COST*100:.2f}%/회, slope {SLOPE_LOOKBACK}d\n")

    # 기준선
    bh = perf(df["ret"], "buyhold")
    long_state = pd.Series(np.where((df["sp"].shift(1) > df["sma200"].shift(1)).fillna(False), "LONG", "CASH"), index=df.index)
    long_ret, long_sw = state_returns(df, long_state)
    longonly = perf(long_ret, "sma200 롱/현금", long_sw)

    print(f"{'전략':28} {'CAGR':>7} {'Sharpe':>7} {'MDD':>8} {'전환':>6}  vs롱전용")
    print("-" * 72)
    for p in (bh, longonly):
        print(f"{p['label']:28} {p['cagr']*100:6.1f}% {p['sharpe']:7.2f} {p['mdd']*100:7.1f}% {p.get('switches',0):6d}")

    # 3-상태 그리드
    print()
    best = None
    grid_rows = []
    for band in (0.00, 0.02, 0.05):
        for persist in (1, 10, 20):
            st = build_states(df, band, persist)
            r, sw = state_returns(df, st)
            p = perf(r, f"3상태 band{int(band*100)}% persist{persist}", sw)
            if not p:
                continue
            d_sharpe = p["sharpe"] - longonly["sharpe"]
            d_mdd = p["mdd"] - longonly["mdd"]  # 양수면 MDD 개선
            n_short = int((st == "SHORT").sum())
            grid_rows.append((p, d_sharpe, d_mdd, n_short))
            if best is None or p["sharpe"] > best[0]["sharpe"]:
                best = (p, d_sharpe, d_mdd, n_short)

    print(f"{'전략':28} {'CAGR':>7} {'Sharpe':>7} {'MDD':>8} {'전환':>6} {'숏일수':>6}  ΔSharpe")
    print("-" * 80)
    for p, ds, dm, ns in grid_rows:
        flag = "  ✅숏레그유효" if (ds > 0.05 and dm >= -0.02) else ("  ⚠️무효/악화" if ds <= 0 else "")
        print(f"{p['label']:28} {p['cagr']*100:6.1f}% {p['sharpe']:7.2f} {p['mdd']*100:7.1f}% {p['switches']:6d} {ns:6d} {ds:+7.2f}{flag}")

    # 숏-only 기여 분해: 숏 보유일의 인버스 누적수익(비용제외) — 숏 자체가 돈이 되는가
    print("\n[숏-only 기여 분해] 숏 보유일에 인버스 포지션이 실제로 번 누적수익")
    for band, persist in ((0.02, 10), (0.00, 1)):
        st = build_states(df, band, persist)
        short_mask = st == "SHORT"
        inv_daily = (-df["ret"] - INVERSE_EXPENSE_ANNUAL / 252.0)[short_mask].dropna()
        if len(inv_daily) < 5:
            print(f"  band{int(band*100)}% persist{persist}: 숏일수 부족({len(inv_daily)})")
            continue
        cum = (1 + inv_daily).prod() - 1
        winrate = (inv_daily > 0).mean()
        # 같은 날 인버스 대신 현금이었으면?
        cash_cum = (1 + df["cash_ret"][short_mask].dropna()).prod() - 1
        verdict = "숏>현금 ✅" if cum > cash_cum else "현금≥숏 ❌(숏이 손해)"
        print(f"  band{int(band*100)}% persist{persist}: 숏일수={len(inv_daily)} "
              f"인버스누적={cum*100:+.1f}% 현금누적={cash_cum*100:+.1f}% 일승률={winrate:.2f} → {verdict}")

    print("\n결론 판정:")
    if best and best[1] > 0.05 and best[2] >= -0.02:
        print(f"  ✅ 숏레그 유효 — 최적 {best[0]['label']} ΔSharpe {best[1]:+.2f}, MDD {best[0]['mdd']*100:.1f}%. DM1 구현 진행 검토.")
    else:
        print("  ❌ 숏레그 무효 — 어떤 파라미터도 롱전용 대비 Sharpe 개선 미달/MDD 악화.")
        print("     → 결론: '하락장엔 현금이 정답'. 목적을 '하락장에서 안 잃기'로 재정의, DM6(레짐 마스터게이트)로.")


if __name__ == "__main__":
    main()
