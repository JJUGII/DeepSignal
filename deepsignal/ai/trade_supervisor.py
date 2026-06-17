"""AI 매매 감독관 — 최근 코인 거래를 주기적으로 분석해 이상·튜닝 포인트 감지.

실시간 틱 제어가 아니라(불가) '슬로우 감독': 최근 완결 거래의 churn·승률·수수료잠식·
트레일링 본전청산·반복손실 종목을 규칙으로 진단하고, 안전범위 권고를 낸다. 선택적으로
LLM(OpenAI)이 한 줄 코멘트. 자동 적용은 하지 않고(기본) 권고+알림만.

env: NEWS_LLM_ENABLED/OPENAI_API_KEY (LLM 코멘트, 선택)
"""
from __future__ import annotations

import os
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

_FEE_ROUNDTRIP_PCT = 0.10  # 업비트 ~0.05% × 2레그


def _hold_minutes(entry: str, exit_: str) -> float | None:
    try:
        from datetime import datetime as _dt
        e = _dt.fromisoformat(str(entry).replace("Z", "+00:00"))
        x = _dt.fromisoformat(str(exit_).replace("Z", "+00:00"))
        return (x - e).total_seconds() / 60.0
    except Exception:
        return None


def analyze_crypto_health(*, crypto_db: str = "outputs/crypto_trades.db", lookback: int = 60) -> dict[str, Any]:
    """최근 완결 코인거래 분석 → 지표·진단·권고 dict."""
    p = Path(crypto_db)
    if not p.is_file():
        return {"error": "crypto_trades.db 없음"}
    c = sqlite3.connect(str(p.resolve()))
    rows = c.execute(
        "SELECT symbol, entry_time, exit_time, actual_return, exit_reason, position_size, "
        "entry_price, exit_price FROM crypto_trades "
        "WHERE exit_time IS NOT NULL AND paper=0 ORDER BY exit_time DESC LIMIT ?",
        (lookback,),
    ).fetchall()
    c.close()
    if not rows:
        return {"error": "완결 거래 없음", "n": 0}

    rets, holds, churn, by_reason = [], [], 0, {}
    loser_counts: dict[str, int] = {}
    realized_krw = 0.0   # 실제 원화 손익
    volume_krw = 0.0     # 거래대금(매수액 합)
    for sym, et, xt, ret, reason, qty, ep, xp in rows:
        r = float(ret or 0) * 100.0
        rets.append(r)
        try:
            q, e, x = float(qty or 0), float(ep or 0), float(xp or 0)
            realized_krw += q * (x - e)
            volume_krw += q * e
        except (TypeError, ValueError):
            pass
        hm = _hold_minutes(et, xt)
        if hm is not None:
            holds.append(hm)
            if hm < 5 and r < 0:
                churn += 1
        by_reason.setdefault(reason or "?", []).append(r)
        if r < 0:
            loser_counts[sym] = loser_counts.get(sym, 0) + 1

    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    n = len(rets)
    gross = sum(rets)
    fee_drag = n * _FEE_ROUNDTRIP_PCT
    net = gross - fee_drag
    fee_krw = volume_krw * 0.0005 * 2          # 양레그 0.05%
    net_krw = realized_krw - fee_krw           # 실제 순손익(원화)
    reason_stats = {k: {"n": len(v), "avg": round(statistics.mean(v), 2)} for k, v in by_reason.items()}
    repeat_losers = sorted([(s, cnt) for s, cnt in loser_counts.items() if cnt >= 3], key=lambda x: -x[1])[:5]

    metrics = {
        "n": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "avg_win": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.mean(losses), 2) if losses else 0.0,
        "avg_trade_pct": round(gross / n, 2),                 # 종목당 평균 수익률(의미있는 %)
        "sum_return_pct": round(gross, 2),                    # %p 단순합 (자산 대비 아님!)
        "fee_drag_pct": round(fee_drag, 2),
        "net_return_pct": round(net, 2),
        "realized_krw": round(realized_krw),                  # 실제 원화 손익
        "fee_krw": round(fee_krw),
        "net_krw": round(net_krw),                            # 실제 순손익(원화) ← 진짜 금액
        "volume_krw": round(volume_krw),
        "median_hold_min": round(statistics.median(holds), 1) if holds else None,
        "churn_count": churn,
        "churn_rate": round(churn / n * 100, 1),
        "exit_reasons": reason_stats,
        "repeat_losers": repeat_losers,
    }

    # ── 규칙 진단·권고 ─────────────────────────────────────
    findings: list[str] = []
    recs: list[str] = []
    # (최우선) 순손실 + 비대칭(승자짧게/패자길게)
    if metrics["net_krw"] < 0:
        findings.append(
            f"⚠️ 최근 {n}건 순손실 {metrics['net_krw']:+,}원 (거래대금 {metrics['volume_krw']:,}원 굴려 "
            f"수수료 {metrics['fee_krw']:,}원 차감 후) — 종목당 평균 {metrics['avg_trade_pct']}%. "
            f"※ '%p 합 {metrics['sum_return_pct']}'은 자산의 그만큼이 아니라 거래수익률 단순합임"
        )
    aw, al = metrics["avg_win"], abs(metrics["avg_loss"])
    if aw > 0 and al > aw * 1.3 and metrics["win_rate"] >= 45:
        findings.append(f"승률 {metrics['win_rate']}%로 절반 이상 이기는데 패자(-{al}%)가 승자(+{aw}%)보다 큼 "
                        f"— '승자 짧게·패자 길게' 비대칭이 순익을 깎음")
        recs.append(f"익절폭↑(+{aw}%→더 끌기) 또는 손절폭↓(패자 더 짧게) — RR>1 만들기")
    if metrics["churn_rate"] >= 15:
        findings.append(f"churn {metrics['churn_rate']}%(5분내 손실청산 {churn}건) — 사자마자 손절 의심")
        recs.append("min_hold↑(CRYPTO_MIN_HOLD_MINUTES) 또는 손절폭↑(stop-loss-pct -1.5→-3)")
    tr = reason_stats.get("trailing")
    if tr and tr["n"] >= 8 and -0.5 < tr["avg"] < 0.5:
        findings.append(f"트레일링 {tr['n']}건이 평균 {tr['avg']}%(본전) 청산 — winners 너무 빨리 끊음")
        recs.append("트레일링 폭↑/익절폭↑로 수익 더 끌기")
    if fee_drag >= abs(net) or (net < 0 and gross > 0):
        findings.append(f"수수료 {fee_drag:.1f}%p가 총수익 {gross:.1f}%p 잠식 → 순익 {net:.1f}%p")
        recs.append("거래 빈도↓(진입 기준 강화) — 수수료 대비 엣지 부족")
    if metrics["win_rate"] < 35:
        findings.append(f"승률 {metrics['win_rate']}% 낮음")
        recs.append("진입 신호 강화 또는 일시 축소 검토")
    if repeat_losers:
        rl = ", ".join(f"{s}({c})" for s, c in repeat_losers)
        findings.append(f"반복 손실 종목: {rl}")
        recs.append("반복손실 종목 일시 제외(exclude_markets) 검토")
    if not findings:
        findings.append("특이 이상 없음 — 정상 범위")

    return {"metrics": metrics, "findings": findings, "recommendations": recs,
            "analyzed_at": datetime.now().isoformat(timespec="seconds")}


def llm_comment(report: dict[str, Any]) -> str | None:
    """선택: LLM이 지표를 읽고 한 줄 총평. 무키/비활성이면 None."""
    if os.environ.get("NEWS_LLM_ENABLED", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key or report.get("error"):
        return None
    import json as _json

    import requests
    m = report.get("metrics", {})
    prompt = (
        "다음은 코인 자동매매 최근 거래 지표다. 한국어로 2문장 이내 냉정한 총평 + 가장 중요한 "
        f"조치 1개만. JSON {{\"comment\":\"...\"}}.\n{_json.dumps({'metrics': m, 'findings': report.get('findings')}, ensure_ascii=False)}"
    )
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": os.environ.get("NEWS_LLM_MODEL", "gpt-4o-mini"),
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "response_format": {"type": "json_object"}, "max_tokens": 200},
            timeout=20)
        if r.status_code == 200:
            return str(_json.loads(r.json()["choices"][0]["message"]["content"]).get("comment") or "")[:300]
    except Exception:
        pass
    return None
