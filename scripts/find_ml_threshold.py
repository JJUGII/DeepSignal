"""ML 매수 게이트 최적 임계값 자동 탐색 (OOS Sharpe 기준).

- 대표 유동 종목으로 데이터셋 빌드(피처+라벨), 봉에서 5분 선행수익률 계산
- TimeSeriesSplit으로 out-of-sample 예측 수집(누수 없음)
- 임계값 스윕: 각 th에서 선택된 매수의 순수익(수수료 차감) Sharpe·승률·커버리지
- Sharpe 최대(최소 거래수 충족) 임계값을 추천

사용: ./.venv/bin/python scripts/find_ml_threshold.py
"""

from __future__ import annotations

import sys

import numpy as np

from deepsignal.ml.crypto_scalp_dataset import load_bars_jsonl, load_dataset_from_bars_dir
from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig
from pathlib import Path

# 사용: find_ml_threshold.py [horizon] [cost_pct] [non_overlap(0/1)]
HORIZON = int(sys.argv[1]) if len(sys.argv) > 1 else 5
COST_PCT = float(sys.argv[2]) if len(sys.argv) > 2 else 0.2
NON_OVERLAP = (len(sys.argv) > 3 and sys.argv[3] in ("1", "true"))
ROUND_TRIP_FEE = 0.001  # 왕복 수수료+슬리피지 근사 (0.1%)
CAP = 6000
MIN_TRADES = 80
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
        "SUIUSDT", "NEARUSDT", "TRXUSDT", "LINKUSDT", "ADAUSDT", "XLMUSDT"]
BARS_DIR = "outputs/binance_stream/bars"


def forward_returns_by_symbol(syms, cap, horizon):
    """심볼별 {open_ts_ms: 5분 선행수익률} 맵."""
    out = {}
    for s in syms:
        bars = load_bars_jsonl(Path(BARS_DIR) / f"{s}_1m.jsonl")
        if cap and len(bars) > cap:
            bars = bars[-cap:]
        closes = [b.close for b in bars]
        m = {}
        for i in range(len(bars) - horizon):
            c0 = closes[i]
            cj = closes[i + horizon]
            if c0 and c0 > 0:
                m[int(bars[i].open_ts_ms)] = cj / c0 - 1.0
        out[s.upper()] = m
    return out


def main():
    print(f"빌드: {len(SYMS)}종목 × 최근 {CAP}봉 ...")
    ds = load_dataset_from_bars_dir(
        BARS_DIR, symbols=SYMS,
        label_cfg=ScalpLabelConfig(horizon_minutes=HORIZON, cost_pct=COST_PCT),
        max_bars_per_symbol=CAP,
    )
    X, y = ds.X, np.asarray(ds.y)
    ts = np.asarray(ds.timestamps_ms)
    syms = np.asarray(ds.symbols)
    print(f"샘플 {len(y)}, 양성비율 {y.mean():.3f}")

    # 선행수익률 매핑
    retmap = forward_returns_by_symbol(SYMS, CAP, HORIZON)
    rets = np.array([retmap.get(str(syms[i]).upper(), {}).get(int(ts[i]), np.nan) for i in range(len(y))])

    # 시간순 정렬(글로벌) — TimeSeriesSplit가 시간 기반이 되도록
    order = np.argsort(ts)
    X, y, rets = X[order], y[order], rets[order]
    valid = ~np.isnan(rets)
    X, y, rets = X[valid], y[valid], rets[valid]
    print(f"수익률 매칭 후 샘플 {len(y)}")

    import lightgbm as lgb
    from sklearn.model_selection import TimeSeriesSplit

    tscv = TimeSeriesSplit(n_splits=5, gap=HORIZON)
    oos_p = np.full(len(y), np.nan)
    params = dict(objective="binary", learning_rate=0.05, num_leaves=31,
                  min_child_samples=50, subsample=0.8, colsample_bytree=0.8,
                  n_estimators=300, verbosity=-1)
    for tr, va in tscv.split(X):
        m = lgb.LGBMClassifier(**params)
        m.fit(X[tr], y[tr])
        oos_p[va] = m.predict_proba(X[va])[:, 1]

    mask = ~np.isnan(oos_p)
    p, yy, rr = oos_p[mask], y[mask], rets[mask]
    net = rr - ROUND_TRIP_FEE  # 매수 후 5분 보유 순수익 근사
    print(f"OOS 예측 샘플 {len(p)} (전체 base 수익률 평균 {net.mean()*100:.3f}%)\n")

    print("임계값  통과율   거래수   승률    평균순익%   Sharpe")
    best = None
    for th in np.arange(0.10, 0.601, 0.025):
        sel = p >= th
        n = int(sel.sum())
        if n == 0:
            continue
        s_net = net[sel]
        cov = sel.mean()
        win = yy[sel].mean()
        sharpe = s_net.mean() / (s_net.std() + 1e-9)
        flag = ""
        if n >= MIN_TRADES and (best is None or sharpe > best[1]):
            best = (round(float(th), 3), float(sharpe), n, float(win), float(s_net.mean()))
            flag = ""
        mark = " *" if n >= MIN_TRADES else "  (적음)"
        print(f" {th:.3f}  {cov*100:5.1f}%  {n:6d}  {win:.3f}  {s_net.mean()*100:+7.3f}   {sharpe:+.4f}{mark}")

    print()
    if best:
        print(f"==> 추천 임계값(OOS Sharpe 최대, 거래≥{MIN_TRADES}): "
              f"CRYPTO_ML_BUY_THRESHOLD={best[0]}  "
              f"(Sharpe={best[1]:.4f}, 거래수={best[2]}, 승률={best[3]:.3f}, 평균순익={best[4]*100:+.3f}%)")
    else:
        print("충분한 거래수의 임계값 없음 — MIN_TRADES 완화 또는 데이터 보강 필요")


if __name__ == "__main__":
    main()
