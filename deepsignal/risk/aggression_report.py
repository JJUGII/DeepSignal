"""공격성 단계 성과 분석기 (읽기 전용).

거래 결과 DB(crypto/stock)를 읽어 '공격성 단계별·추격거래별' 성과를 집계한다.
며칠간 10단계로 실거래한 뒤, 어느 단계/조건이 실제로 돈을 벌었는지 보고
적정 단계를 재책정하기 위한 자료를 만든다.

실주문 경로를 전혀 건드리지 않으며, DB를 SELECT만 한다.
출력: outputs/AGGRESSION_PERFORMANCE.md + .json
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


# ──────────────────────────────────────────────────────────────
# 공격성 변경 이력 → 시각별 단계 매핑
# ──────────────────────────────────────────────────────────────
def _load_aggression_history(output_dir: Path) -> list[tuple[str, int, str]]:
    """aggression_history.jsonl → [(ts_iso, level, band), ...] (시간순)."""
    path = output_dir / "aggression_history.jsonl"
    rows: list[tuple[str, int, str]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            rows.append((str(r.get("ts")), int(r.get("level")), str(r.get("band") or "")))
        except Exception:
            continue
    rows.sort(key=lambda x: x[0])
    return rows


def _level_at(ts: str | None, history: list[tuple[str, int, str]]) -> tuple[int | None, str | None]:
    """주어진 시각에 활성화돼 있던 공격성 단계를 history에서 찾는다."""
    if not ts or not history:
        return None, None
    found: tuple[int | None, str | None] = (None, None)
    for h_ts, lvl, band in history:
        if h_ts <= ts:
            found = (lvl, band)
        else:
            break
    return found


# ──────────────────────────────────────────────────────────────
# 집계 단위
# ──────────────────────────────────────────────────────────────
@dataclass
class Bucket:
    label: str
    n: int = 0
    wins: int = 0
    pnl_sum: float = 0.0
    hold_sum: float = 0.0
    hold_n: int = 0
    pnls: list[float] = field(default_factory=list)

    def add(self, pnl_pct: float, hold_hours: float | None = None) -> None:
        self.n += 1
        self.pnl_sum += pnl_pct
        self.pnls.append(pnl_pct)
        if pnl_pct > 0:
            self.wins += 1
        if hold_hours is not None:
            self.hold_sum += hold_hours
            self.hold_n += 1

    def to_dict(self) -> dict[str, Any]:
        win_rate = (self.wins / self.n * 100.0) if self.n else 0.0
        avg = (self.pnl_sum / self.n) if self.n else 0.0
        avg_hold = (self.hold_sum / self.hold_n) if self.hold_n else None
        best = max(self.pnls) if self.pnls else None
        worst = min(self.pnls) if self.pnls else None
        return {
            "label": self.label,
            "trades": self.n,
            "win_rate_pct": round(win_rate, 1),
            "avg_pnl_pct": round(avg, 3),
            "total_pnl_pct": round(self.pnl_sum, 2),
            "avg_holding_hours": round(avg_hold, 1) if avg_hold is not None else None,
            "best_pct": round(best, 2) if best is not None else None,
            "worst_pct": round(worst, 2) if worst is not None else None,
        }


def _q(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql))
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
# 메인 집계
# ──────────────────────────────────────────────────────────────
def build_aggression_report(output_dir: str | Path = "outputs") -> dict[str, Any]:
    out = Path(output_dir)
    history = _load_aggression_history(out)

    by_level: dict[int, Bucket] = {}
    by_band: dict[str, Bucket] = {}
    crypto_chase = Bucket("코인 추격거래(등락률>8%)")
    crypto_normal = Bucket("코인 일반거래(등락률≤8%)")
    by_asset: dict[str, Bucket] = {}

    def _lv_bucket(lvl: int | None) -> Bucket | None:
        if lvl is None:
            return None
        return by_level.setdefault(lvl, Bucket(f"{lvl}단계"))

    def _band_bucket(band: str | None) -> Bucket | None:
        if not band:
            return None
        return by_band.setdefault(band, Bucket(band))

    # ── 코인 ──────────────────────────────────────────────
    cdb = out / "crypto_recommendation_outcomes.db"
    if cdb.exists():
        conn = sqlite3.connect(str(cdb))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(crypto_recommendation_outcomes)")}
        has_agg = "aggression_level" in cols
        has_chg = "signed_change_rate" in cols
        sel = "created_at, realized_pnl_pct, closed_at, side, executed"
        if has_agg:
            sel += ", aggression_level, aggression_band"
        if has_chg:
            sel += ", signed_change_rate"
        rows = _q(conn, f"SELECT {sel} FROM crypto_recommendation_outcomes "
                        f"WHERE realized_pnl_pct IS NOT NULL")
        ab = by_asset.setdefault("크립토", Bucket("크립토"))
        for r in rows:
            pnl = r["realized_pnl_pct"]
            if pnl is None:
                continue
            pnl = float(pnl)
            lvl = r["aggression_level"] if has_agg and r["aggression_level"] is not None else None
            band = r["aggression_band"] if has_agg and r["aggression_band"] is not None else None
            if lvl is None:
                lvl, band = _level_at(r["created_at"], history)
            ab.add(pnl)
            b = _lv_bucket(lvl)
            if b:
                b.add(pnl)
            bb = _band_bucket(band)
            if bb:
                bb.add(pnl)
            if has_chg and r["signed_change_rate"] is not None:
                (crypto_chase if abs(float(r["signed_change_rate"])) > 0.08 else crypto_normal).add(pnl)
        conn.close()

    # ── 주식(국내+해외) ──────────────────────────────────────
    sdb = out / "recommendation_outcomes.db"
    if sdb.exists():
        conn = sqlite3.connect(str(sdb))
        rows = _q(conn, "SELECT created_at, realized_pnl_pct, holding_hours "
                        "FROM recommendation_outcomes WHERE realized_pnl_pct IS NOT NULL")
        ab = by_asset.setdefault("주식", Bucket("주식"))
        for r in rows:
            pnl = r["realized_pnl_pct"]
            if pnl is None:
                continue
            pnl = float(pnl)
            hold = float(r["holding_hours"]) if r["holding_hours"] is not None else None
            lvl, band = _level_at(r["created_at"], history)
            ab.add(pnl, hold)
            b = _lv_bucket(lvl)
            if b:
                b.add(pnl, hold)
            bb = _band_bucket(band)
            if bb:
                bb.add(pnl, hold)
        conn.close()

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "history_changes": len(history),
        "current_level": history[-1][1] if history else None,
        "by_level": {str(k): by_level[k].to_dict() for k in sorted(by_level)},
        "by_band": {k: v.to_dict() for k, v in by_band.items()},
        "by_asset": {k: v.to_dict() for k, v in by_asset.items()},
        "crypto_chase_vs_normal": {
            "chase": crypto_chase.to_dict(),
            "normal": crypto_normal.to_dict(),
        },
    }
    return report


def _fmt_bucket_line(d: dict[str, Any]) -> str:
    hold = f", 평균보유 {d['avg_holding_hours']}h" if d.get("avg_holding_hours") is not None else ""
    return (f"- **{d['label']}**: {d['trades']}건 · 승률 {d['win_rate_pct']}% · "
            f"평균손익 {d['avg_pnl_pct']:+}% · 누적 {d['total_pnl_pct']:+}%"
            f" (최고 {d['best_pct']:+}% / 최저 {d['worst_pct']:+}%){hold}"
            if d['trades'] else f"- **{d['label']}**: 거래 없음")


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# 공격성 단계 성과 리포트", "",
             f"생성: {report['generated_at']}",
             f"현재 단계: {report.get('current_level')} · 단계변경 기록 {report['history_changes']}건", ""]
    lines.append("## 자산군별")
    for d in report["by_asset"].values():
        lines.append(_fmt_bucket_line(d))
    lines += ["", "## 공격성 단계별"]
    if report["by_level"]:
        for d in report["by_level"].values():
            lines.append(_fmt_bucket_line(d))
    else:
        lines.append("- 아직 마감된 거래 없음")
    lines += ["", "## 위험 밴드별"]
    for d in report["by_band"].values():
        lines.append(_fmt_bucket_line(d))
    lines += ["", "## 코인: 추격거래 vs 일반거래 (10단계 신규 허용분 검증)"]
    lines.append(_fmt_bucket_line(report["crypto_chase_vs_normal"]["chase"]))
    lines.append(_fmt_bucket_line(report["crypto_chase_vs_normal"]["normal"]))
    return "\n".join(lines) + "\n"


def write_report(output_dir: str | Path = "outputs") -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = build_aggression_report(out)
    md = render_markdown(report)
    md_path = out / "AGGRESSION_PERFORMANCE.md"
    json_path = out / "AGGRESSION_PERFORMANCE.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path
