"""신규 공격형 전략(인버스·레버리지·추세) 백테스트 검증 틀 (P0).

원칙:
- 일별 수익률을 '복리'로 적용 → 레버리지 ETF의 변동성 복리감쇠(decay)가 자연 반영된다.
- 거래비용(전환 시 fee)·레버리지 ETF 보수(연 expense)를 차감.
- 지수(^GSPC 등) 기반이라 생존편향 없음.
- 결과는 Sharpe·CAGR·MDD·전환횟수 + 벤치마크(buy&hold) 대비 edge_sharpe.

전략은 '일별 목표 노출(position)' 시계열로 표현:
  +1.0 = 지수 1배 롱, +2.0 = 2배 레버리지, -1.0 = 인버스(숏), 0 = 현금.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    name: str
    sharpe: float
    cagr_pct: float
    mdd_pct: float
    total_return_pct: float
    trades: int
    days: int
    benchmark_sharpe: float
    edge_sharpe: float
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "sharpe": round(self.sharpe, 3),
            "cagr_pct": round(self.cagr_pct, 2), "mdd_pct": round(self.mdd_pct, 1),
            "total_return_pct": round(self.total_return_pct, 1), "trades": self.trades,
            "days": self.days, "benchmark_sharpe": round(self.benchmark_sharpe, 3),
            "edge_sharpe": round(self.edge_sharpe, 3), **self.extra,
        }


def fetch_index(symbol: str = "^GSPC", period: str = "max") -> pd.Series:
    """yfinance 일별 종가 시계열. (지수: ^GSPC=S&P500, ^IXIC=나스닥)"""
    import yfinance as yf
    h = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    s = h["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s[~s.index.duplicated(keep="last")]


def _perf(daily_ret: pd.Series) -> dict | None:
    r = daily_ret.dropna()
    if len(r) < 252:
        return None
    eq = (1 + r).cumprod()
    return {
        "sharpe": float(r.mean() / (r.std() + 1e-12) * np.sqrt(252)),
        "cagr": float(eq.iloc[-1] ** (252 / len(r)) - 1),
        "mdd": float((eq / eq.cummax() - 1).min()),
        "total": float(eq.iloc[-1] - 1),
    }


def backtest(
    name: str,
    index_close: pd.Series,
    position_fn: Callable[[pd.DataFrame], pd.Series],
    *,
    switch_cost: float = 0.001,     # 전환 1회당 비용(0.1% = 수수료+세금+슬리피지)
    annual_expense: float = 0.0,    # 레버리지/인버스 ETF 연 보수(예: 0.0079)
    cash_yield_annual: float = 0.02,
) -> BacktestResult:
    """position_fn(df) → 일별 목표 노출 시계열로 백테스트.

    df 컬럼: close, ret, sma200, sma50, ret_20d, vol_20d.
    레버리지 L배는 일별 index 수익률 × L 로 복리 적용(decay 자연 반영).
    """
    df = pd.DataFrame({"close": index_close}).sort_index()
    df["ret"] = df["close"].pct_change()
    df["sma200"] = df["close"].rolling(200).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["ret_20d"] = df["close"].pct_change(20)
    df["vol_20d"] = df["ret"].rolling(20).std()

    pos = position_fn(df).reindex(df.index).fillna(0.0)
    pos = pos.shift(1).fillna(0.0)  # look-ahead 방지: 전일 신호로 당일 포지션

    daily_fee = annual_expense / 252.0
    cash_daily = cash_yield_annual / 252.0
    lev_abs = pos.abs()

    # 노출 부분: pos × 지수수익률 − 레버리지보수. 현금 부분: (1-|pos|) × 현금이자
    strat_ret = pos * df["ret"] - lev_abs * daily_fee + (1 - lev_abs).clip(lower=0) * cash_daily
    # 전환 비용: 목표 노출이 바뀐 날 |Δpos| × switch_cost
    turn = pos.diff().abs().fillna(0.0)
    strat_ret = strat_ret - turn * switch_cost

    trades = int((turn > 1e-9).sum())
    ps = _perf(strat_ret)
    pb = _perf(df["ret"])
    if not ps or not pb:
        return BacktestResult(name, 0, 0, 0, 0, trades, len(df), 0, 0, {"note": "표본 부족"})
    return BacktestResult(
        name=name, sharpe=ps["sharpe"], cagr_pct=ps["cagr"] * 100, mdd_pct=ps["mdd"] * 100,
        total_return_pct=ps["total"] * 100, trades=trades, days=len(strat_ret.dropna()),
        benchmark_sharpe=pb["sharpe"], edge_sharpe=ps["sharpe"] - pb["sharpe"],
    )


# ── 전략 빌더들 (position_fn) ─────────────────────────────────────────
def pos_buy_hold(df: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=df.index)


def pos_trend(df: pd.DataFrame) -> pd.Series:
    """200일선 위=롱, 아래=현금 (현 추세추종)."""
    return (df["close"] > df["sma200"]).astype(float)


def pos_trend_inverse(df: pd.DataFrame, inv_mult: float = 1.0) -> pd.Series:
    """200일선 위=롱, 아래=인버스(숏). inv_mult로 인버스 배율."""
    up = df["close"] > df["sma200"]
    return pd.Series(np.where(up, 1.0, -inv_mult), index=df.index)


def pos_trend_leverage(df: pd.DataFrame, up_mult: float = 2.0) -> pd.Series:
    """강한 상승(200·50선 위 & 20일 +)=레버리지, 약한 상승=1배, 하락=현금."""
    strong = (df["close"] > df["sma200"]) & (df["close"] > df["sma50"]) & (df["ret_20d"] > 0)
    mild = (df["close"] > df["sma200"]) & ~strong
    return pd.Series(np.where(strong, up_mult, np.where(mild, 1.0, 0.0)), index=df.index)


def pos_trend_full(df: pd.DataFrame, up_mult: float = 2.0, inv_mult: float = 1.0) -> pd.Series:
    """공격형: 강세=레버리지, 약한상승=1배, 하락=인버스."""
    strong = (df["close"] > df["sma200"]) & (df["close"] > df["sma50"]) & (df["ret_20d"] > 0)
    mild = (df["close"] > df["sma200"]) & ~strong
    down = df["close"] <= df["sma200"]
    return pd.Series(np.where(strong, up_mult, np.where(mild, 1.0, np.where(down, -inv_mult, 0.0))),
                     index=df.index)


def run_suite(symbol: str = "^GSPC", period: str = "max") -> list[dict]:
    """기본 전략 묶음을 한 번에 백테스트해 비교표 반환."""
    px = fetch_index(symbol, period)
    runs = [
        backtest("buy_hold(1x)", px, pos_buy_hold),
        backtest("trend(현재)", px, pos_trend),
        backtest("trend+inverse(-1x)", px, lambda d: pos_trend_inverse(d, 1.0), annual_expense=0.0079),
        backtest("trend+inverse(-2x)", px, lambda d: pos_trend_inverse(d, 2.0), annual_expense=0.0099),
        backtest("trend+leverage(2x)", px, lambda d: pos_trend_leverage(d, 2.0), annual_expense=0.0079),
        backtest("trend+leverage(3x)", px, lambda d: pos_trend_leverage(d, 3.0), annual_expense=0.0095),
        backtest("full(2x/-1x)", px, lambda d: pos_trend_full(d, 2.0, 1.0), annual_expense=0.0085),
        backtest("full(3x/-2x)", px, lambda d: pos_trend_full(d, 3.0, 2.0), annual_expense=0.0099),
    ]
    return [r.to_dict() for r in runs]


if __name__ == "__main__":
    import json
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "^GSPC"
    rows = run_suite(sym)
    print(f"\n=== 백테스트 결과: {sym} ===")
    hdr = f"{'전략':<22}{'Sharpe':>8}{'CAGR%':>8}{'MDD%':>8}{'총수익%':>10}{'전환':>7}{'엣지':>8}"
    print(hdr); print("-" * len(hdr))
    for r in rows:
        print(f"{r['name']:<22}{r['sharpe']:>8.2f}{r['cagr_pct']:>8.1f}{r['mdd_pct']:>8.1f}"
              f"{r['total_return_pct']:>10.0f}{r['trades']:>7}{r['edge_sharpe']:>8.2f}")
    print()
