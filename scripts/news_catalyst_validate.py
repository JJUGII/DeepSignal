"""뉴스촉매 IC 검증 — news_score가 *이후* 수익률을 실제로 예측하나.

news_scores(채점) × market_prices(이후 가격)을 조인해 포워드 수익률을 구하고,
news_score와의 상관(IC)을 측정한다. 엣지가 진짜인지 데이터로 판정.

IC > 0 (+유의미) = 촉매점수가 예측력 있음 → 게이트 켤 근거.
IC ~ 0 = 예측력 없음 → 키지 말 것.

데이터가 쌓여야 의미(채점 후 시간이 지나 포워드 수익률이 생겨야 함).
사용: ./.venv/bin/python scripts/news_catalyst_validate.py [forward_hours]
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FWD_HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0


def _price_at(conn, symbol: str, after_iso: str, within_hours: float):
    """after_iso 이후 within_hours 내 첫 종가(close). 없으면 None."""
    row = conn.execute(
        "SELECT close FROM market_prices WHERE symbol=? AND bar_time >= ? "
        "AND bar_time <= datetime(?, ?) AND close > 0 ORDER BY bar_time ASC LIMIT 1",
        (symbol, after_iso, after_iso, f"+{within_hours} hours"),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def main() -> None:
    from deepsignal.config.settings import load_settings
    conn = sqlite3.connect(str(Path(load_settings().db_path).expanduser().resolve()))

    scores = conn.execute(
        "SELECT symbol, scored_at, news_score, polarity, strength, catalyst_type FROM news_scores "
        "ORDER BY scored_at"
    ).fetchall()
    print(f"채점 데이터: {len(scores)}건 · 포워드 {FWD_HOURS}h\n")
    if not scores:
        print("news_scores 비어있음 — 러너를 며칠 돌려 데이터를 쌓으세요.")
        return

    pairs = []  # (news_score, fwd_return_pct, polarity)
    no_price = 0
    for sym, scored_at, ns, pol, strg, cat in scores:
        if ns is None:
            continue
        p0 = _price_at(conn, sym, scored_at, 1.0)          # 채점 직후 가격
        p1 = _price_at(conn, sym, scored_at, FWD_HOURS)    # 포워드 가격은 within window 마지막에 가까운 것
        # p1: 포워드 윈도우 끝 가격
        row1 = conn.execute(
            "SELECT close FROM market_prices WHERE symbol=? AND bar_time >= datetime(?, ?) "
            "AND close > 0 ORDER BY bar_time ASC LIMIT 1",
            (sym, scored_at, f"+{FWD_HOURS} hours"),
        ).fetchone()
        p1 = float(row1[0]) if row1 and row1[0] else None
        if not p0 or not p1:
            no_price += 1
            continue
        ret = (p1 - p0) / p0 * 100.0
        pairs.append((float(ns), ret, float(pol or 0)))

    print(f"포워드 수익률 산출: {len(pairs)}건 (가격없음 {no_price}건)")
    if len(pairs) < 10:
        print("\n⚠️ 표본 부족(<10) — 아직 판정 불가. 러너를 며칠 돌려 채점×포워드가 쌓이면 IC가 의미.")
        print("   (방금 만든 파이프라인이라 정상. launchd로 주기 수집·채점하면 자동 누적)")
        return

    import statistics
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / len(xs)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    ic = cov / (sx * sy) if sx > 0 and sy > 0 else 0.0
    # 호재(>55)/악재(<45) 그룹 평균 수익률
    hi = [r for n, r, _ in pairs if n >= 55]
    lo = [r for n, r, _ in pairs if n <= 45]
    print(f"\n=== IC (news_score vs 포워드수익률) = {ic:+.3f} ===")
    print(f"  호재(score≥55) {len(hi)}건 평균수익 {statistics.mean(hi):+.2f}%" if hi else "  호재 표본 없음")
    print(f"  악재(score≤45) {len(lo)}건 평균수익 {statistics.mean(lo):+.2f}%" if lo else "  악재 표본 없음")
    print("\n판정: IC>+0.05 이고 호재>악재 면 촉매 예측력 有 → NEWS_CATALYST_GATE_ENABLED 검토.")
    print("      IC~0 이면 예측력 없음 → 게이트 켜지 말 것.")


if __name__ == "__main__":
    main()
