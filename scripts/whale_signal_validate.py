"""고래신호 IC 검증 — whale_trade_ratio가 *이후* 수익을 실제로 예측하나.

WHALE_SIGNAL_LOG.jsonl(결정시점 채점)을 market_prices(이후 가격)와 조인해
포워드 수익률을 구하고, whale_ratio와의 상관(IC)을 측정한다. 동시에 완결
실거래 DB(outputs/crypto_trades.db)에 대한 동일 검증도 수행해 둘을 함께 보고.

판정 기준
  IC > +0.05 이고 고래군 평균수익 > 비고래군 → +3pt 휴리스틱 유지/상향 근거.
  IC ~ 0 또는 고래군 승률·수익 열위 → 가중치 하향/제거 검토.

사용: ./.venv/bin/python scripts/whale_signal_validate.py [forward_hours]
   (forward_hours 기본 24 — JSONL 로그 검증에만 사용. DB 검증은 actual_return 직접 사용)
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FWD_HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
_LOG_PATH = Path(__file__).resolve().parents[1] / "output" / "crypto_stream" / "WHALE_SIGNAL_LOG.jsonl"
_TRADES_DB = Path(__file__).resolve().parents[1] / "outputs" / "crypto_trades.db"
_WHALE_HI = 20.0


# ── 통계 헬퍼 ────────────────────────────────────────────────────────
def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _rank(a):
    order = sorted(range(len(a)), key=lambda i: a[i])
    r = [0.0] * len(a)
    i = 0
    while i < len(a):
        j = i
        while j < len(a) and a[order[j]] == a[order[i]]:
            j += 1
        avg = (i + j - 1) / 2
        for k in range(i, j):
            r[order[k]] = avg
        i = j
    return r


def _spearman(xs, ys):
    return _pearson(_rank(xs), _rank(ys))


def _winrate(rs):
    return sum(1 for r in rs if r > 0) / len(rs) if rs else float("nan")


def _report(pairs, label):
    """pairs: [(whale_ratio, forward_return)] → IC + 고래/비고래 비교 출력."""
    if len(pairs) < 10:
        print(f"  ⚠️ 표본 부족(n={len(pairs)} <10) — 판정 불가. 며칠 더 수집 필요.")
        return
    X = [p[0] for p in pairs]
    Y = [p[1] for p in pairs]
    print(f"  표본 n={len(pairs)} (whale!=0: {sum(1 for w in X if w != 0)})")
    print(f"  Pearson IC = {_pearson(X, Y):+.4f}   Spearman IC = {_spearman(X, Y):+.4f}")
    wh = [r for w, r in pairs if w >= _WHALE_HI]
    nw = [r for w, r in pairs if w < _WHALE_HI]
    if wh:
        print(f"  고래군(≥20x) n={len(wh)}  mean={st.mean(wh):+.4f}  "
              f"median={st.median(wh):+.4f}  승률={100*_winrate(wh):.1f}%")
    else:
        print("  고래군(≥20x) 없음")
    if nw:
        print(f"  비고래(<20x) n={len(nw)}  mean={st.mean(nw):+.4f}  "
              f"median={st.median(nw):+.4f}  승률={100*_winrate(nw):.1f}%")


# ── ① 라이브 JSONL 로그 검증 ─────────────────────────────────────────
def _price_after(conn, symbol, after_iso, within_hours):
    row = conn.execute(
        "SELECT close FROM market_prices WHERE symbol=? AND bar_time >= datetime(?, ?) "
        "AND close > 0 ORDER BY bar_time ASC LIMIT 1",
        (symbol, after_iso, f"+{within_hours} hours"),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def validate_log() -> None:
    print(f"=== ① 라이브 결정로그 검증 (forward {FWD_HOURS}h) ===")
    if not _LOG_PATH.exists():
        print(f"  로그 없음: {_LOG_PATH}")
        print("  → whale_signal_log.log_whale_decision()을 라이브 결정루프에 배선 후 수집 필요.")
        return
    recs = []
    for line in _LOG_PATH.open():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    print(f"  로그 {len(recs)}건")
    if not recs:
        print("  → 빈 로그. 라이브 수집 필요.")
        return

    from deepsignal.config.settings import load_settings
    conn = sqlite3.connect(str(Path(load_settings().db_path).expanduser().resolve()))
    pairs = []
    no_price = 0
    for r in recs:
        w = r.get("whale_ratio")
        if w is None:
            continue
        sym, ts = r.get("symbol"), r.get("ts")
        p0 = r.get("ref_price") or _price_after(conn, sym, ts, 1.0)
        p1 = _price_after(conn, sym, ts, FWD_HOURS)
        if not p0 or not p1:
            no_price += 1
            continue
        pairs.append((float(w), (p1 - p0) / p0))
    print(f"  포워드 수익 산출 {len(pairs)}건 (가격없음 {no_price}건)")
    _report(pairs, "live-log")


# ── ② 완결 실거래 DB 검증(현재 가용 데이터) ──────────────────────────
def validate_trades_db() -> None:
    print("\n=== ② 완결 실거래 DB 검증 (crypto_trades.db, actual_return 직접) ===")
    if not _TRADES_DB.exists():
        print(f"  DB 없음: {_TRADES_DB}")
        return
    from deepsignal.market_data.feature_engine.spec import FEATURE_INDEX
    wi = FEATURE_INDEX["whale_trade_ratio"]
    conn = sqlite3.connect(str(_TRADES_DB))
    rows = conn.execute(
        "SELECT features_snapshot, actual_return FROM crypto_trades "
        "WHERE exit_time IS NOT NULL AND paper=0"
    ).fetchall()
    pairs = []
    skipped = 0
    for fs, ar in rows:
        if not fs or ar is None:
            skipped += 1
            continue
        try:
            v = json.loads(fs)
        except Exception:
            skipped += 1
            continue
        if len(v) <= wi:  # 구 50차원 스냅샷엔 whale 없음
            skipped += 1
            continue
        pairs.append((float(v[wi]), float(ar)))
    print(f"  완결거래 {len(rows)}건 중 유효 62차원 {len(pairs)}건 (제외 {skipped})")
    _report(pairs, "trades-db")


def main() -> None:
    validate_log()
    validate_trades_db()
    print("\n판정 가이드: IC>+0.05 & 고래군 우위 → +3pt 유지/상향. "
          "IC~0 또는 고래군 승률 열위 → 하향/제거 검토.")


if __name__ == "__main__":
    main()
