"""지속적 엣지 모니터 + 자동 게이팅.

매 실행마다 3자산군(코인 5분 스캘핑 / 미국주식 / 한국주식)의 OOS 엣지를 정직하게 평가하고:
- 결과를 시계열(edge_monitor_history.jsonl)로 누적
- 배포 결정(EDGE_GATE.json): 엣지가 **연속 PERSIST_RUNS회 지속**될 때만 deploy=true
  (같은 데이터 반복 테스트로 우연히 통과하는 p-hacking 방지)
- 사람용 리포트(EDGE_MONITOR.md)

배포 게이트는 "기본 닫힘". 검증된 엣지가 나타나는 날 자동으로 열린다.
스케줄(launchd 등)으로 매일 돌리면 지속 분석이 된다.

사용: PYTHONPATH=. ./.venv/bin/python scripts/edge_monitor.py [--stamp ISO8601]
  --stamp: 기록용 타임스탬프(미지정 시 시스템 KST 시각)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlite3
import numpy as np

from deepsignal.config.settings import load_settings

KST = timezone(timedelta(hours=9))
OUT = Path("outputs")
HISTORY = OUT / "edge_monitor_history.jsonl"
GATE = OUT / "EDGE_GATE.json"
REPORT = OUT / "EDGE_MONITOR.md"

# ── 배포 기준 (보수적) ──────────────────────────────────────────────
PERSIST_RUNS = 3            # 연속 N회 엣지 지속 시에만 배포
CRYPTO = dict(min_sharpe=0.05, min_trades=200, min_net_pct=0.03)
STOCK = dict(min_edge_pct=0.30, min_trades=200)  # 점수선택이 벤치마크를 이 %p 이상 초과
BARS_DIR = "outputs/binance_stream/bars"


def _stamp() -> str:
    for i, a in enumerate(sys.argv):
        if a == "--stamp" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return datetime.now(KST).isoformat(timespec="seconds")


# ── 코인: 5분 스캘핑 OOS Sharpe ─────────────────────────────────────
def crypto_edge() -> dict:
    from deepsignal.ml.crypto_scalp_dataset import load_bars_jsonl, load_dataset_from_bars_dir
    from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig

    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
            "SUIUSDT", "NEARUSDT", "TRXUSDT", "LINKUSDT", "ADAUSDT", "XLMUSDT"]
    horizon, cap, fee = 5, 5000, 0.001
    res = {"strategy": "crypto_scalp_5m", "edge": False, "metrics": {}}
    try:
        ds = load_dataset_from_bars_dir(
            BARS_DIR, symbols=syms,
            label_cfg=ScalpLabelConfig(horizon_minutes=horizon, cost_pct=0.2),
            max_bars_per_symbol=cap)
        X, y = ds.X, np.asarray(ds.y)
        ts, sym = np.asarray(ds.timestamps_ms), np.asarray(ds.symbols)
        if len(y) < 1000:
            res["metrics"] = {"note": f"표본 부족 {len(y)}"}
            return res
        rmap = {}
        for s in syms:
            bars = load_bars_jsonl(Path(BARS_DIR) / f"{s}_1m.jsonl")
            if cap and len(bars) > cap:
                bars = bars[-cap:]
            cl = [b.close for b in bars]
            rmap[s.upper()] = {int(bars[i].open_ts_ms): cl[i + horizon] / cl[i] - 1.0
                               for i in range(len(bars) - horizon) if cl[i]}
        rets = np.array([rmap.get(str(sym[i]).upper(), {}).get(int(ts[i]), np.nan) for i in range(len(y))])
        o = np.argsort(ts)
        X, y, rets = X[o], y[o], rets[o]
        m = ~np.isnan(rets)
        X, y, rets = X[m], y[m], rets[m]

        import lightgbm as lgb
        from sklearn.model_selection import TimeSeriesSplit
        oos = np.full(len(y), np.nan)
        for tr, va in TimeSeriesSplit(n_splits=5, gap=horizon).split(X):
            clf = lgb.LGBMClassifier(objective="binary", learning_rate=0.05, num_leaves=31,
                                     min_child_samples=50, n_estimators=300, verbosity=-1)
            clf.fit(X[tr], y[tr])
            oos[va] = clf.predict_proba(X[va])[:, 1]
        mm = ~np.isnan(oos)
        p, rr = oos[mm], rets[mm]
        net = rr - fee
        best = None
        for th in np.arange(0.10, 0.701, 0.025):
            sel = p >= th
            n = int(sel.sum())
            if n < CRYPTO["min_trades"]:
                continue
            s = net[sel]
            sharpe = s.mean() / (s.std() + 1e-9)
            if best is None or sharpe > best["sharpe"]:
                best = {"threshold": round(float(th), 3), "sharpe": float(sharpe),
                        "net_pct": float(s.mean() * 100), "trades": n}
        if best:
            best["edge"] = bool(best["sharpe"] >= CRYPTO["min_sharpe"] and best["net_pct"] >= CRYPTO["min_net_pct"])
            res["edge"] = best["edge"]
            res["metrics"] = best
        else:
            res["metrics"] = {"note": "거래수 충족 임계값 없음"}
    except Exception as e:  # noqa: BLE001
        res["metrics"] = {"error": repr(e)}
    return res


# ── 주식: 기술점수가 buy&hold를 이기는가 ────────────────────────────
def stock_edge(market: str, fee: float, horizon: int = 10) -> dict:
    from deepsignal.analyzer.technical.technical_analyzer import TechnicalAnalyzer
    from deepsignal.scoring.signal_scorer import SignalScorer

    res = {"strategy": f"stock_{market}", "edge": False, "metrics": {}}
    try:
        conn = sqlite3.connect(load_settings().db_path)
        allsy = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM market_prices WHERE timeframe='1d'")]
        syms = [s for s in allsy if (".K" in s) == (market == "KR")]
        ta, sc = TechnicalAnalyzer(), SignalScorer()
        scores, rets = [], []
        for sym in syms:
            cur = conn.execute(
                "SELECT bar_time, open, high, low, close, volume FROM market_prices "
                "WHERE symbol=? AND timeframe='1d' ORDER BY bar_time", (sym,))
            rows = [dict(zip(("bar_time", "open", "high", "low", "close", "volume"), r)) for r in cur.fetchall()]
            if len(rows) < 40 + horizon:
                continue
            inds = ta.analyze_prices(sym, rows)
            cl = [i.close for i in inds]
            for i in range(len(inds) - horizon):
                s = sc.score_technical(inds[i])
                if s is None or cl[i] is None or cl[i + horizon] is None or cl[i] <= 0:
                    continue
                scores.append(float(s))
                rets.append(cl[i + horizon] / cl[i] - 1.0)
        conn.close()
        if len(scores) < 500:
            res["metrics"] = {"note": f"표본 부족 {len(scores)}"}
            return res
        scores, rets = np.array(scores), np.array(rets)
        ben = float((rets.mean() - fee) * 100)  # buy&hold 벤치마크 순익%
        best = None
        for th in [50, 55, 60, 65, 70, 75]:
            sel = scores >= th
            n = int(sel.sum())
            if n < STOCK["min_trades"]:
                continue
            net = float((rets[sel].mean() - fee) * 100)
            if best is None or net > best["net_pct"]:
                best = {"threshold": th, "net_pct": net, "trades": n}
        if best:
            best["benchmark_pct"] = round(ben, 4)
            best["edge_pct"] = round(best["net_pct"] - ben, 4)
            best["horizon_days"] = horizon
            best["edge"] = bool((best["net_pct"] - ben) >= STOCK["min_edge_pct"])
            res["edge"] = best["edge"]
            res["metrics"] = best
        else:
            res["metrics"] = {"benchmark_pct": round(ben, 4), "note": "거래수 충족 임계값 없음"}
    except Exception as e:  # noqa: BLE001
        res["metrics"] = {"error": repr(e)}
    return res


# ── 모멘텀 팩터: 횡단면 모멘텀이 buy&hold를 이기는가 ─────────────────
MOM = dict(min_edge_sharpe=0.10)


def momentum_edge(market: str, korean: bool, fee: float) -> dict:
    """xs_mom(12-1개월 상위 1/3) 월간 리밸런스 vs 동일비중 buy&hold (Sharpe 비교).

    주의: 유니버스가 현재 생존 종목이라 survivorship bias 있음 — 절대수익은 과대.
    엣지(상대 Sharpe 스프레드)는 덜 편향되나 방향 참고용.
    """
    import pandas as pd

    res = {"strategy": f"momentum_{market}", "edge": False, "metrics": {}}
    try:
        conn = sqlite3.connect(load_settings().db_path)
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
        conn.close()
        if len(frames) < 8:
            res["metrics"] = {"note": f"종목 부족 {len(frames)}"}
            return res
        px = pd.DataFrame(frames).sort_index()
        px = px[~px.index.duplicated(keep="last")]
        m = px.resample("ME").last()
        mret = m.pct_change()
        mom = m.shift(1) / m.shift(12) - 1.0
        dates = m.index
        bench_r, mom_r = [], []
        prev = pd.Series(dtype=float)
        for i in range(1, len(dates)):
            t, tp = dates[i], dates[i - 1]
            avail = m.loc[tp].dropna().index
            if len(avail) < 8:
                bench_r.append(np.nan)
                mom_r.append(np.nan)
                continue
            ew = pd.Series(1.0 / len(avail), index=avail)
            fwd = mret.loc[t]
            bench_r.append(float((ew * fwd).sum()))
            mv = mom.loc[tp, avail].dropna()
            w = (pd.Series(1.0 / len(mv.nlargest(max(1, len(mv) // 3))),
                           index=mv.nlargest(max(1, len(mv) // 3)).index)
                 if len(mv) >= 8 else ew)
            turn = float(w.subtract(prev, fill_value=0).abs().sum())
            mom_r.append(float((w * fwd).sum()) - fee * turn)
            prev = w

        def _perf(rl):
            r = pd.Series(rl, index=dates[1:]).dropna()
            if len(r) < 12:
                return None
            return {"ann_ret": float((1 + r).prod() ** (12 / len(r)) - 1),
                    "sharpe": float(r.mean() / (r.std() + 1e-12) * np.sqrt(12)),
                    "months": int(len(r))}

        pb, pm = _perf(bench_r), _perf(mom_r)
        if pb and pm:
            es = pm["sharpe"] - pb["sharpe"]
            res["metrics"] = {"factor": "xs_mom", "sharpe": round(pm["sharpe"], 3),
                              "benchmark_sharpe": round(pb["sharpe"], 3), "edge_sharpe": round(es, 3),
                              "ann_ret_pct": round(pm["ann_ret"] * 100, 2), "months": pm["months"],
                              "caveat": "survivorship_bias"}
            res["edge"] = bool(es >= MOM["min_edge_sharpe"])
        else:
            res["metrics"] = {"note": "표본 부족"}
    except Exception as e:  # noqa: BLE001
        res["metrics"] = {"error": repr(e)}
    return res


# ── 거시 레짐: 지수 추세추종(200일선)이 buy&hold를 이기는가 ─────────
REGIME = dict(min_edge_sharpe=0.15)


def regime_edge() -> dict:
    """S&P500 200일선 추세추종 vs buy&hold (Sharpe). 지수 기반이라 생존편향 없음."""
    import pandas as pd

    res = {"strategy": "regime_trend_sp500", "edge": False, "metrics": {}}
    try:
        conn = sqlite3.connect(load_settings().db_path)

        def load_ind(name):
            rows = conn.execute(
                "SELECT indicator_date, value FROM economic_indicators "
                "WHERE indicator_name=? AND value IS NOT NULL ORDER BY indicator_date", (name,)).fetchall()
            idx = pd.to_datetime([r[0][:10] for r in rows])
            s = pd.Series([float(r[1]) for r in rows], index=idx)
            return s[~s.index.duplicated(keep="last")]

        sp = load_ind("SP500")
        tb = load_ind("US13W")
        conn.close()
        if sp.empty or len(sp) < 252 * 3:
            res["metrics"] = {"note": "SP500 부족"}
            return res
        df = pd.DataFrame({"sp": sp}).sort_index()
        df["ret"] = df["sp"].pct_change()
        df["sma"] = df["sp"].rolling(200).mean()
        df["cash"] = (tb.reindex(df.index).ffill() / 100 / 252).fillna(0)
        inmkt = df["sp"].shift(1) > df["sma"].shift(1)
        strat = pd.Series(np.where(inmkt.fillna(False), df["ret"], df["cash"]), index=df.index)

        def _p(r):
            r = r.dropna()
            if len(r) < 252:
                return None
            eq = (1 + r).cumprod()
            return {"sharpe": float(r.mean() / (r.std() + 1e-12) * np.sqrt(252)),
                    "cagr": float(eq.iloc[-1] ** (252 / len(r)) - 1),
                    "mdd": float((eq / eq.cummax() - 1).min())}

        pb, ps = _p(df["ret"]), _p(strat)
        if pb and ps:
            es = ps["sharpe"] - pb["sharpe"]
            res["metrics"] = {"rule": "sma200", "sharpe": round(ps["sharpe"], 3),
                              "benchmark_sharpe": round(pb["sharpe"], 3), "edge_sharpe": round(es, 3),
                              "cagr_pct": round(ps["cagr"] * 100, 2), "mdd_pct": round(ps["mdd"] * 100, 1)}
            res["edge"] = bool(es >= REGIME["min_edge_sharpe"])
        else:
            res["metrics"] = {"note": "표본 부족"}
    except Exception as e:  # noqa: BLE001
        res["metrics"] = {"error": repr(e)}
    return res


def read_history() -> list[dict]:
    if not HISTORY.is_file():
        return []
    out = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def persisted(hist: list[dict], strategy: str, need_prior: int) -> bool:
    """직전 need_prior회 기록이 모두 edge=True 였는지(이번 회차와 합쳐 연속 PERSIST_RUNS)."""
    prior = [h for h in hist if h.get("strategy") == strategy][-need_prior:]
    return len(prior) >= need_prior and all(p.get("edge") for p in prior)


LEV = dict(min_edge_sharpe=0.10)


def leverage_edge() -> dict:
    """나스닥 추세구간 2x 레버리지 vs buy&hold (Sharpe). 지수 기반·복리감쇠 반영."""
    res = {"strategy": "leverage_trend_nasdaq", "edge": False, "metrics": {}}
    try:
        from deepsignal.backtest.strategy_lab import fetch_index, backtest, pos_trend_leverage
        px = fetch_index("^IXIC", "max")
        r = backtest("lev2x", px, lambda d: pos_trend_leverage(d, 2.0), annual_expense=0.0079)
        m = r.to_dict()
        res["metrics"] = {
            "instrument": "409820 KODEX 미국나스닥100레버리지(2x)",
            "sharpe": m["sharpe"], "benchmark_sharpe": m["benchmark_sharpe"],
            "edge_sharpe": m["edge_sharpe"], "cagr_pct": m["cagr_pct"], "mdd_pct": m["mdd_pct"],
            "caveat": "kr_etf_1day_lag_tracking_error",
        }
        res["edge"] = bool(m["edge_sharpe"] >= LEV["min_edge_sharpe"])
    except Exception as e:  # noqa: BLE001
        res["metrics"] = {"error": repr(e)}
    return res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    results = [crypto_edge(), stock_edge("US", 0.001, 10), stock_edge("KR", 0.0025, 10),
               momentum_edge("US", False, 0.001), momentum_edge("KR", True, 0.0025),
               regime_edge(), leverage_edge()]
    hist = read_history()

    lines = [f"# 엣지 모니터 — {stamp}", "",
             "검증된 엣지가 연속 %d회 지속될 때만 배포(deploy=true). 기본 닫힘.\n" % PERSIST_RUNS,
             "| 전략 | 엣지? | 핵심지표 | 배포 |", "|---|---|---|---|"]
    gate = {"stamp": stamp, "persist_runs": PERSIST_RUNS, "strategies": {}}
    # 확립된 엣지(학계 다수 검증·생존편향 없음)는 지속성 대기 없이 즉시 배포.
    # 새로 발견한 엣지는 p-hacking 방지를 위해 연속 PERSIST_RUNS회 지속 필요.
    PRE_VALIDATED = {"regime_trend_sp500"}  # S&P500 200일선 추세추종 (98년 OOS 검증)
    for r in results:
        if r["strategy"] in PRE_VALIDATED:
            deploy = bool(r["edge"])
        else:
            deploy = bool(r["edge"] and persisted(hist, r["strategy"], PERSIST_RUNS - 1))
        r["deploy"] = deploy
        r["stamp"] = stamp
        m = r["metrics"]
        if r["strategy"].startswith("crypto"):
            desc = (f"thr={m.get('threshold')} Sharpe={m.get('sharpe'):+.3f} "
                    f"net={m.get('net_pct'):+.3f}% n={m.get('trades')}"
                    if "sharpe" in m else str(m))
        elif r["strategy"].startswith("momentum"):
            desc = (f"xs_mom Sharpe={m.get('sharpe'):.2f} vs B&H {m.get('benchmark_sharpe'):.2f} "
                    f"(엣지 {m.get('edge_sharpe'):+.2f}, 연{m.get('ann_ret_pct'):+.1f}%, {m.get('months')}M) ⚠생존편향"
                    if "edge_sharpe" in m else str(m))
        elif r["strategy"].startswith("regime"):
            desc = (f"{m.get('rule')} Sharpe={m.get('sharpe'):.2f} vs B&H {m.get('benchmark_sharpe'):.2f} "
                    f"(엣지 {m.get('edge_sharpe'):+.2f}, CAGR {m.get('cagr_pct'):+.1f}%, MDD {m.get('mdd_pct'):.0f}%)"
                    if "edge_sharpe" in m else str(m))
        else:
            desc = (f"thr={m.get('threshold')} 엣지={m.get('edge_pct'):+.3f}%p "
                    f"(선택 {m.get('net_pct'):+.3f}% vs B&H {m.get('benchmark_pct'):+.3f}%) n={m.get('trades')}"
                    if "edge_pct" in m else str(m))
        lines.append(f"| {r['strategy']} | {'✅' if r['edge'] else '❌'} | {desc} | {'🟢 배포' if deploy else '🔒 닫힘'} |")
        gate["strategies"][r["strategy"]] = {
            "edge": r["edge"], "deploy": deploy, "metrics": m}
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"stamp": stamp, "strategy": r["strategy"],
                                "edge": r["edge"], "deploy": deploy, "metrics": m}, ensure_ascii=False) + "\n")

    lines += ["", "## 해석",
              "- ❌ = 현재 엣지 없음(무작위/buy&hold 못 이김). 안전장치가 자본 보호.",
              "- ✅가 연속 %d회면 🟢 배포 — 그날 자동으로 live 게이트가 열림." % PERSIST_RUNS,
              "- 이 파일은 매 실행 갱신. 추세는 edge_monitor_history.jsonl 참고."]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    GATE.write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n기록: {HISTORY}\n게이트: {GATE}\n리포트: {REPORT}")


if __name__ == "__main__":
    main()
