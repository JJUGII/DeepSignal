"""Suggest .env ML settings from CRYPTO_ML_THRESHOLD_REPORT.md (no auto-apply)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ThresholdSuggestion:
    horizon_minutes: int
    prob_threshold: float
    sharpe: float
    n_trades: int
    ev_pct: float
    source_path: str

    def env_lines(self) -> list[str]:
        return [
            f"CRYPTO_ML_BUY_THRESHOLD={self.prob_threshold:.2f}",
            f"CRYPTO_ML_BUY_GATE=true",
            f"CRYPTO_LABEL_N_MIN={self.horizon_minutes}",
            "# train: python main.py crypto-train-lgbm --horizon {0}".format(self.horizon_minutes),
        ]


def parse_threshold_report(path: str | Path) -> ThresholdSuggestion | None:
    p = Path(path)
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")

    rec_block = ""
    if "## Recommendation" in text:
        rec_block = text.split("## Recommendation", 1)[1]

    m_p = re.search(r"`P=([\d.]+)`", rec_block)
    m_n = re.search(r"`N=(\d+)m`", rec_block)
    m_sh = re.search(r"Sharpe\s+\*\*([\d.]+)\*\*", rec_block)
    m_tr = re.search(r"trades=(\d+)", rec_block)
    m_ev = re.search(r"EV=([-\d.]+)%", rec_block)
    if m_p and m_n:
        return ThresholdSuggestion(
            horizon_minutes=int(m_n.group(1)),
            prob_threshold=float(m_p.group(1)),
            sharpe=float(m_sh.group(1)) if m_sh else 0.0,
            n_trades=int(m_tr.group(1)) if m_tr else 0,
            ev_pct=float(m_ev.group(1)) if m_ev else 0.0,
            source_path=p.as_posix(),
        )

    best: ThresholdSuggestion | None = None
    for line in text.splitlines():
        if not line.startswith("|") or "N (min)" in line or "---" in line:
            continue
        cols = [c.strip() for c in line.split("|") if c.strip()]
        if len(cols) < 6:
            continue
        try:
            n_min = int(cols[0])
            prob = float(cols[1])
            sharpe = float(cols[5])
            trades = int(cols[2])
            ev = float(cols[4])
        except ValueError:
            continue
        row = ThresholdSuggestion(
            horizon_minutes=n_min,
            prob_threshold=prob,
            sharpe=sharpe,
            n_trades=trades,
            ev_pct=ev,
            source_path=p.as_posix(),
        )
        if best is None or row.sharpe > best.sharpe:
            best = row
    return best


def format_suggestion_report(
    suggestion: ThresholdSuggestion | None,
    *,
    validation_path: str | Path | None = None,
) -> str:
    lines = [
        "# CRYPTO ML — suggested .env values",
        "",
        "> 자동 적용하지 않습니다. Phase 2 val_sharpe 확인 후 수동으로 .env를 수정하세요.",
        "",
    ]
    if validation_path:
        lines.append(f"Validation report: `{Path(validation_path).as_posix()}`")
        lines.append("")

    if suggestion is None:
        lines.extend(
            [
                "⚠️ `CRYPTO_ML_THRESHOLD_REPORT.md`를 찾을 수 없거나 파싱 실패.",
                "",
                "먼저 실행:",
                "```bash",
                "python main.py crypto-validate-ml --symbols BTC,ETH --days 60",
                "```",
            ]
        )
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            f"Source: `{suggestion.source_path}`",
            "",
            "## 권장 (Sharpe 최대 조합)",
            "",
            f"- **P (CRYPTO_ML_BUY_THRESHOLD)**: `{suggestion.prob_threshold:.2f}`",
            f"- **N (CRYPTO_LABEL_N_MIN)**: `{suggestion.horizon_minutes}`",
            f"- OOF Sharpe: **{suggestion.sharpe:.2f}**, trades: {suggestion.n_trades}, EV: {suggestion.ev_pct:.3f}%",
            "",
            "## .env에 붙여넣기",
            "",
            "```env",
        ]
    )
    lines.extend(suggestion.env_lines())
    lines.extend(
        [
            "```",
            "",
            "## ml_only 사용 전 확인",
            "",
            "`CRYPTO_ML_VALIDATION_REPORT.md`에서 모든 fold **val_sharpe ≥ 0.5** 인지 확인 후:",
            "```env",
            "CRYPTO_GATE_MODE=ml_primary",
            "# 실험: CRYPTO_GATE_MODE=ml_only",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"
