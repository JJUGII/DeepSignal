#!/usr/bin/env python3
"""
IC analysis of crypto recommendation features vs forward return.

KEY DATA REALITY (verified before writing this script):
  - Table crypto_recommendation_outcomes has 8,517 rows.
  - 6,714 feature-bearing rows are ALL side='buy' recommendations and have
    realized_pnl_pct = NULL (no recorded outcome).
  - The 141 realized_pnl_pct rows are ALL side='sell' exit events with NO features.
    => feature set and realized-outcome set are DISJOINT. Cannot join 1:1
       (scalping fills average positions; sell P&L is position-level, not entry-level).
  - pnl_pct is the unrealized snapshot at record time (~always 0 for buys). Using it
    as an "outcome" would be garbage (corr against zeros).

APPROACH: reconstruct a FORWARD RETURN for each buy recommendation directly from the
price series the table already records (market, created_at, current_price). For each
buy at time t with price p0, find the SAME market's recorded price at the first record
in [t+H-tol, t+H+tol]; forward return = p_future/p0 - 1. This is self-contained
(no network), honest, and avoids look-ahead leakage. Horizons: 30m and 60m.

Outputs IC (Spearman + Pearson), quintile monotonicity, order-flow focus,
executed-vs-all comparison, final_score verdict.
"""
import sqlite3, json, math, datetime as dt
from collections import defaultdict

DB = "outputs/crypto_recommendation_outcomes.db"
FEATURE_KEYS = ["ret_1m","ret_3m","ret_5m","ret_15m","ret_1h","alpha_vs_btc_1m",
 "alpha_vs_btc_15m","trend_align_1m_3m_15m","volume_ratio_20","taker_buy_ratio",
 "quote_vol_spike_5m","ob_imbalance","ob_spread_frac","ob_depth_1pct",
 "ob_bid_wall_dist_bps","ob_ask_wall_dist_bps","atr_14_1m_pct","realized_vol_20tick",
 "bb_position_20","btc_trend_1h","alt_quote_vol_sum_log","fear_greed_norm"]
SCORE_KEYS = ["final_score","model_probability","technical_score","macro_score",
              "rsi_14","signed_change_rate"]
ORDERFLOW = ["taker_buy_ratio","ob_imbalance","quote_vol_spike_5m","volume_ratio_20"]

def parse(ts):
    return dt.datetime.fromisoformat(ts)

# ---------- stats helpers (no scipy dependency) ----------
def rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0]*len(xs)
    i = 0
    while i < len(xs):
        j = i
        while j+1 < len(xs) and xs[order[j+1]] == xs[order[i]]:
            j += 1
        avg = (i+j)/2.0 + 1
        for k in range(i, j+1):
            r[order[k]] = avg
        i = j+1
    return r

def pearson(x, y):
    n=len(x)
    if n<3: return None
    mx=sum(x)/n; my=sum(y)/n
    sxy=sum((a-mx)*(b-my) for a,b in zip(x,y))
    sxx=sum((a-mx)**2 for a in x); syy=sum((b-my)**2 for b in y)
    if sxx==0 or syy==0: return None
    return sxy/math.sqrt(sxx*syy)

def spearman(x, y):
    if len(x)<3: return None
    return pearson(rank(x), rank(y))

def quintile_table(x, y, nq=5):
    pairs=sorted(zip(x,y))
    n=len(pairs)
    if n<nq*3: nq=max(2, n//5) or 2
    out=[]
    for q in range(nq):
        lo=q*n//nq; hi=(q+1)*n//nq
        seg=pairs[lo:hi]
        if not seg: continue
        ys=[b for _,b in seg]
        avg=sum(ys)/len(ys)
        win=sum(1 for v in ys if v>0)/len(ys)
        out.append((q+1, len(seg), avg, win, seg[0][0], seg[-1][0]))
    return out

def monotonic(qt):
    # check if mean return is (weakly) monotonic increasing across quintiles
    means=[m for _,_,m,_,_,_ in qt]
    if len(means)<3: return "?"
    inc=all(means[i]<=means[i+1]+1e-9 for i in range(len(means)-1))
    dec=all(means[i]>=means[i+1]-1e-9 for i in range(len(means)-1))
    if inc: return "O+"   # higher signal -> higher return
    if dec: return "O-"   # higher signal -> lower return (inverse/anti-predictive)
    # spread sign as weak monotonicity proxy
    return "X"

# ---------- load ----------
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row; cur=con.cursor()
cur.execute("""SELECT id,market,created_at,current_price,executed,final_score,
  model_probability,technical_score,macro_score,rsi_14,signed_change_rate,
  features_snapshot_json
  FROM crypto_recommendation_outcomes
  WHERE side='buy' AND current_price>0 AND created_at IS NOT NULL
  ORDER BY market, created_at""")
rows=[dict(r) for r in cur.fetchall()]
con.close()

# build per-market price series (use ALL buy rows' current_price as price ticks)
series=defaultdict(list)
for r in rows:
    series[r['market']].append((parse(r['created_at']), r['current_price']))
for m in series: series[m].sort()

def fwd_return(market, t0, p0, H_min, tol_min):
    arr=series[market]
    target=t0+dt.timedelta(minutes=H_min)
    lo=target-dt.timedelta(minutes=tol_min); hi=target+dt.timedelta(minutes=tol_min)
    best=None; bestdt=None
    for (t,p) in arr:
        if t<=t0: continue
        if lo<=t<=hi:
            d=abs((t-target).total_seconds())
            if bestdt is None or d<bestdt:
                bestdt=d; best=p
        if t>hi: break
    if best is None or p0<=0: return None
    return (best/p0-1.0)*100.0  # percent

# ---------- compute IC for a given horizon ----------
def run_horizon(H, tol, label):
    print(f"\n{'='*70}\nHORIZON {label} (target +{H}m, tol +-{tol}m)\n{'='*70}")
    # attach forward returns
    fr={}
    for r in rows:
        ret=fwd_return(r['market'], parse(r['created_at']), r['current_price'], H, tol)
        fr[r['id']]=ret
    valid=[r for r in rows if fr[r['id']] is not None]
    print(f"rows with computable forward return: {len(valid)} / {len(rows)}")
    if not valid:
        return None
    rets_all=[fr[r['id']] for r in valid]
    print(f"forward return all: mean {sum(rets_all)/len(rets_all):+.4f}%  "
          f"win {sum(1 for v in rets_all if v>0)/len(rets_all)*100:.1f}%")

    results=[]
    # feature ICs
    for key in FEATURE_KEYS:
        xs=[]; ys=[]
        for r in valid:
            try: d=json.loads(r['features_snapshot_json']) if r['features_snapshot_json'] else {}
            except: d={}
            v=d.get(key)
            if v is None or not isinstance(v,(int,float)): continue
            xs.append(float(v)); ys.append(fr[r['id']])
        # drop degenerate (all same value)
        if len(xs)<30 or len(set(xs))<5:
            results.append((key, None, None, len(xs), "deg")); continue
        sp=spearman(xs,ys); pe=pearson(xs,ys)
        qt=quintile_table(xs,ys)
        mono=monotonic(qt)
        results.append((key, sp, pe, len(xs), mono, qt))
    # score ICs
    for key in SCORE_KEYS:
        xs=[]; ys=[]
        for r in valid:
            v=r.get(key)
            if v is None: continue
            xs.append(float(v)); ys.append(fr[r['id']])
        if len(xs)<30 or len(set(xs))<5:
            results.append((key, None, None, len(xs), "deg")); continue
        sp=spearman(xs,ys); pe=pearson(xs,ys); qt=quintile_table(xs,ys)
        results.append((key, sp, pe, len(xs), monotonic(qt), qt))

    # sort by |spearman|
    rsort=sorted([r for r in results if r[1] is not None],
                 key=lambda r: abs(r[1]), reverse=True)
    print(f"\n{'feature':<26}{'SpearmanIC':>12}{'PearsonIC':>12}{'N':>7}  mono")
    for r in rsort:
        print(f"{r[0]:<26}{r[1]:>+12.4f}{r[2]:>+12.4f}{r[3]:>7}  {r[4]}")
    deg=[r for r in results if r[1] is None]
    if deg:
        print("degenerate/insufficient:", ", ".join(f"{r[0]}(N={r[3]})" for r in deg))

    # order-flow detail with quintiles
    print(f"\n--- ORDER-FLOW DETAIL ({label}) ---")
    rmap={r[0]:r for r in results}
    for key in ORDERFLOW:
        r=rmap.get(key)
        if not r or r[1] is None:
            print(f"{key}: insufficient/degenerate (N={r[3] if r else 0})"); continue
        print(f"{key}: SpearmanIC={r[1]:+.4f} PearsonIC={r[2]:+.4f} N={r[3]} mono={r[4]}")
        for q,n,avg,win,lo,hi in r[5]:
            print(f"   Q{q} n={n:<5} ret={avg:+.3f}% win={win*100:4.1f}%  [{lo:.4g}..{hi:.4g}]")

    # final_score quintiles
    print(f"\n--- final_score quintiles ({label}) ---")
    r=rmap.get('final_score')
    if r and r[1] is not None:
        for q,n,avg,win,lo,hi in r[5]:
            print(f"   Q{q} n={n:<5} ret={avg:+.3f}% win={win*100:4.1f}%  score[{lo:.1f}..{hi:.1f}]")

    return fr, valid

# ---------- sample status ----------
print("="*70); print("SAMPLE STATUS"); print("="*70)
con=sqlite3.connect(DB); cur=con.cursor()
cur.execute("SELECT MIN(created_at),MAX(created_at) FROM crypto_recommendation_outcomes WHERE side='buy'")
mn,mx=cur.fetchone()
print(f"buy recs date range: {mn} .. {mx}")
print(f"feature buy rows loaded: {len(rows)}")
cur.execute("SELECT COUNT(*) FROM crypto_recommendation_outcomes WHERE side='buy' AND executed=1")
print(f"executed buys: {cur.fetchone()[0]}")
con.close()

res30=run_horizon(30, 15, "30m")
res60=run_horizon(60, 20, "60m")

# ---------- executed vs all (using 30m forward return) ----------
if res30:
    fr, valid=res30
    print(f"\n{'='*70}\nEXECUTED GATE EFFECT (30m forward return)\n{'='*70}")
    ex=[fr[r['id']] for r in valid if r['executed']==1]
    al=[fr[r['id']] for r in valid]
    nex=[fr[r['id']] for r in valid if r['executed']!=1]
    def stat(name,a):
        if not a: print(f"{name}: n=0"); return
        print(f"{name}: n={len(a):<5} mean={sum(a)/len(a):+.4f}%  "
              f"win={sum(1 for v in a if v>0)/len(a)*100:.1f}%  median={sorted(a)[len(a)//2]:+.4f}%")
    stat("ALL buys      ", al)
    stat("executed=1    ", ex)
    stat("not executed  ", nex)
