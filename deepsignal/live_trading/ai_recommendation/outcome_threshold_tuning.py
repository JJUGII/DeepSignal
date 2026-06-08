"""Tune min_final_score from live recommendation_outcomes.db."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from deepsignal.live_trading.ai_recommendation.recommendation_outcomes import init_outcomes_db
from deepsignal.live_trading.ai_recommendation.validation_threshold_tuning import (
    DEFAULT_CANDIDATE_THRESHOLDS,
    THRESHOLD_SUMMARY_FILENAME,
    ValidationThresholdSummary,
    _pick_threshold,
    load_threshold_summary,
    write_threshold_summary,
)

OUTCOME_TUNING_MD = "OUTCOME_THRESHOLD_TUNING.md"


@dataclass
class OutcomeSample:
    symbol: str
    final_score: float
    return_pct: float
    return_source: str
    closed: bool
    max_profit_pct: float | None
    max_loss_pct: float | None


@dataclass
class OutcomeThresholdTuningResult:
    generated_at: str
    source: str = "outcomes"
    lookback_days: int = 60
    min_samples: int = 10
    target_win_rate: float = 0.45
    min_avg_return: float = 0.0
    blend_with_validation: float = 0.5
    global_block: dict[str, Any] = field(default_factory=dict)
    score_buckets: list[dict[str, Any]] = field(default_factory=list)
    outcome_summary: ValidationThresholdSummary = field(default_factory=ValidationThresholdSummary)
    merged_summary: ValidationThresholdSummary = field(default_factory=ValidationThresholdSummary)
    warnings: list[str] = field(default_factory=list)
    output_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "generated_at": self.generated_at,
            "lookback_days": self.lookback_days,
            "min_samples": self.min_samples,
            "target_win_rate": self.target_win_rate,
            "min_avg_return": self.min_avg_return,
            "blend_with_validation": self.blend_with_validation,
            "global": dict(self.global_block),
            "score_buckets": list(self.score_buckets),
            "outcome_summary": self.outcome_summary.to_dict(),
            "merged_summary": self.merged_summary.to_dict(),
            "warnings": list(self.warnings),
            "output_paths": dict(self.output_paths),
        }


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _score_bucket_label(score: float) -> str:
    if score < 55:
        return "<55"
    if score < 65:
        return "55-64"
    if score < 75:
        return "65-74"
    return "75+"


def _outcome_return(row: sqlite3.Row) -> tuple[float | None, str]:
    realized = _float(row["realized_pnl_pct"])
    if row["closed_at"] and realized is not None:
        return realized, "closed"
    if int(row["executed"] or 0) and _float(row["max_profit_pct"]) is not None:
        return _float(row["max_profit_pct"]), "holding"
    return None, ""


def load_outcome_samples(
    outcomes_db: str | Path,
    *,
    lookback_days: int,
) -> list[OutcomeSample]:
    path = init_outcomes_db(outcomes_db)
    since = (date.today() - timedelta(days=max(1, int(lookback_days)))).isoformat()
    sql = (
        "SELECT symbol, final_score, executed, entry_price, closed_at, realized_pnl_pct, "
        "max_profit_pct, max_loss_pct FROM recommendation_outcomes "
        "WHERE substr(created_at, 1, 10) >= ? AND allowed_for_plan = 1 AND final_score IS NOT NULL "
        "AND (closed_at IS NOT NULL OR (executed = 1 AND entry_price IS NOT NULL))"
    )
    out: list[OutcomeSample] = []
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, (since,)).fetchall()
    for row in rows:
        score = _float(row["final_score"])
        if score is None:
            continue
        ret, src = _outcome_return(row)
        if ret is None:
            continue
        out.append(
            OutcomeSample(
                symbol=str(row["symbol"]).upper(),
                final_score=float(score),
                return_pct=float(ret),
                return_source=src,
                closed=bool(row["closed_at"]),
                max_profit_pct=_float(row["max_profit_pct"]),
                max_loss_pct=_float(row["max_loss_pct"]),
            )
        )
    return out


def analyze_score_buckets(samples: list[OutcomeSample]) -> list[dict[str, Any]]:
    buckets: dict[str, list[OutcomeSample]] = {}
    for s in samples:
        buckets.setdefault(_score_bucket_label(s.final_score), []).append(s)
    rows: list[dict[str, Any]] = []
    for label in ("<55", "55-64", "65-74", "75+"):
        group = buckets.get(label, [])
        if not group:
            rows.append(
                {
                    "bucket": label,
                    "sample_count": 0,
                    "win_rate": None,
                    "avg_realized_pnl_pct": None,
                    "avg_max_profit_pct": None,
                    "avg_max_loss_pct": None,
                }
            )
            continue
        wins = sum(1 for g in group if g.return_pct > 0)
        rows.append(
            {
                "bucket": label,
                "sample_count": len(group),
                "win_rate": wins / len(group),
                "avg_realized_pnl_pct": sum(g.return_pct for g in group) / len(group),
                "avg_max_profit_pct": _avg_optional([g.max_profit_pct for g in group]),
                "avg_max_loss_pct": _avg_optional([g.max_loss_pct for g in group]),
            }
        )
    return rows


def _avg_optional(values: list[float | None]) -> float | None:
    usable = [v for v in values if v is not None]
    return (sum(usable) / len(usable)) if usable else None


def _blend_value(outcome: float, validation: float, weight_outcome: float) -> float:
    w = max(0.0, min(1.0, float(weight_outcome)))
    return round(outcome * w + validation * (1.0 - w), 1)


def blend_threshold_summaries(
    outcome: ValidationThresholdSummary,
    validation: ValidationThresholdSummary | None,
    *,
    weight_outcome: float,
    generated_at: str,
) -> ValidationThresholdSummary:
    if validation is None:
        merged = ValidationThresholdSummary(
            generated_at=generated_at,
            default_min_final_score=outcome.default_min_final_score,
            global_threshold=outcome.global_threshold,
            by_symbol=dict(outcome.by_symbol),
            by_price_bucket=dict(outcome.by_price_bucket),
            groups=list(outcome.groups),
            warnings=list(outcome.warnings) + ["validation_summary_missing; outcomes_only"],
        )
        return merged

    w = max(0.0, min(1.0, float(weight_outcome)))
    global_thr = _blend_value(outcome.global_threshold, validation.global_threshold, w)
    by_symbol: dict[str, float] = {}
    for sym in set(outcome.by_symbol) | set(validation.by_symbol):
        o = outcome.by_symbol.get(sym, outcome.global_threshold)
        v = validation.by_symbol.get(sym, validation.global_threshold)
        by_symbol[sym] = _blend_value(o, v, w)
    by_bucket: dict[str, float] = {}
    for bucket in set(outcome.by_price_bucket) | set(validation.by_price_bucket):
        o = outcome.by_price_bucket.get(bucket, outcome.global_threshold)
        v = validation.by_price_bucket.get(bucket, validation.global_threshold)
        by_bucket[bucket] = _blend_value(o, v, w)

    warnings = list(outcome.warnings) + list(validation.warnings)
    warnings.append(f"blended outcomes={w:.0%} validation={1-w:.0%}")
    return ValidationThresholdSummary(
        generated_at=generated_at,
        source_validation_json=validation.source_validation_json,
        forward_days=validation.forward_days,
        default_min_final_score=global_thr,
        global_threshold=global_thr,
        by_symbol=by_symbol,
        by_price_bucket=by_bucket,
        groups=outcome.groups + validation.groups,
        warnings=warnings,
    )


def compute_outcome_threshold_tuning(
    outcomes_db: str | Path,
    *,
    lookback_days: int = 60,
    min_samples: int = 10,
    target_win_rate: float = 0.45,
    min_avg_return: float = 0.0,
    default_threshold: float = 60.0,
    candidate_thresholds: tuple[float, ...] = DEFAULT_CANDIDATE_THRESHOLDS,
    blend_with_validation: float = 0.5,
    output_dir: str | Path = "outputs",
) -> OutcomeThresholdTuningResult:
    generated_at = datetime.now().isoformat(timespec="seconds")
    samples = load_outcome_samples(outcomes_db, lookback_days=lookback_days)
    warnings: list[str] = []
    if not samples:
        warnings.append("no eligible outcome samples in lookback window")

    holding_count = sum(1 for s in samples if s.return_source == "holding")
    if holding_count:
        warnings.append(f"{holding_count} holding samples use max_profit_pct as return proxy")

    pairs = [(s.final_score, s.return_pct) for s in samples]
    global_pick = _pick_threshold(
        pairs,
        candidate_thresholds=candidate_thresholds,
        min_samples=min_samples,
        target_win_rate=target_win_rate,
        min_avg_return=min_avg_return,
        default=default_threshold,
    )
    global_pick.group = "global"

    by_symbol: dict[str, float] = {}
    symbol_groups: dict[str, list[tuple[float, float]]] = {}
    for s in samples:
        symbol_groups.setdefault(s.symbol, []).append((s.final_score, s.return_pct))
    min_symbol = max(3, min_samples // 2)
    groups: list[dict[str, Any]] = [global_pick.to_dict()]
    for sym, sym_pairs in sorted(symbol_groups.items()):
        res = _pick_threshold(
            sym_pairs,
            candidate_thresholds=candidate_thresholds,
            min_samples=min_symbol,
            target_win_rate=target_win_rate,
            min_avg_return=min_avg_return,
            default=global_pick.min_final_score,
        )
        res.group = f"symbol:{sym}"
        by_symbol[sym] = res.min_final_score
        groups.append(res.to_dict())

    outcome_summary = ValidationThresholdSummary(
        generated_at=generated_at,
        default_min_final_score=default_threshold,
        global_threshold=global_pick.min_final_score,
        by_symbol=by_symbol,
        groups=groups,
        warnings=list(warnings),
    )

    validation_existing = load_threshold_summary(output_dir)
    merged = blend_threshold_summaries(
        outcome_summary,
        validation_existing,
        weight_outcome=blend_with_validation,
        generated_at=generated_at,
    )

    subset_final = [ret for score, ret in pairs if score >= global_pick.min_final_score]
    win_rate = (sum(1 for r in subset_final if r > 0) / len(subset_final)) if subset_final else 0.0
    avg_ret = (sum(subset_final) / len(subset_final)) if subset_final else 0.0

    return OutcomeThresholdTuningResult(
        generated_at=generated_at,
        lookback_days=int(lookback_days),
        min_samples=int(min_samples),
        target_win_rate=float(target_win_rate),
        min_avg_return=float(min_avg_return),
        blend_with_validation=float(blend_with_validation),
        global_block={
            "recommended_min_final_score": global_pick.min_final_score,
            "merged_min_final_score": merged.global_threshold,
            "sample_count": global_pick.sample_count,
            "win_rate": global_pick.win_rate,
            "avg_return_pct": global_pick.avg_forward_return_pct,
            "chosen_reason": global_pick.chosen_reason,
            "eligible_samples": len(samples),
            "win_rate_at_threshold": win_rate,
            "avg_return_at_threshold": avg_ret,
        },
        score_buckets=analyze_score_buckets(samples),
        outcome_summary=outcome_summary,
        merged_summary=merged,
        warnings=warnings,
    )


def write_outcome_threshold_outputs(
    result: OutcomeThresholdTuningResult,
    *,
    output_dir: str | Path,
) -> tuple[Path, Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    dt = datetime.fromisoformat(result.generated_at)
    ts = dt.strftime("%Y%m%d_%H%M%S")
    json_path = root / f"outcome_threshold_tuning_{ts}.json"
    md_path = root / OUTCOME_TUNING_MD
    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    g = result.global_block
    lines = [
        "# DeepSignal — Outcome 기반 Threshold 튜닝",
        "",
        f"- Generated: {result.generated_at}",
        f"- Lookback: {result.lookback_days}일",
        f"- Blend (outcomes): {result.blend_with_validation:.0%}",
        "",
        "## Global",
        "",
        f"- Outcomes 추천 `min_final_score`: **{g.get('recommended_min_final_score')}**",
        f"- 병합 후 live 적용값: **{g.get('merged_min_final_score')}**",
        f"- Samples (eligible / at threshold): {g.get('eligible_samples')} / {g.get('sample_count')}",
        f"- Win rate @ threshold: {float(g.get('win_rate_at_threshold') or 0) * 100:.1f}%",
        f"- Avg return @ threshold: {float(g.get('avg_return_at_threshold') or 0):+.2f}%",
        f"- Reason: {g.get('chosen_reason')}",
        "",
        "## Score buckets",
        "",
        "| 구간 | N | 승률 | 평균수익% | max_profit | max_loss |",
        "|------|---|------|-----------|------------|----------|",
    ]
    for row in result.score_buckets:
        wr = row.get("win_rate")
        wr_s = f"{float(wr) * 100:.1f}%" if wr is not None else "n/a"
        lines.append(
            f"| {row['bucket']} | {row['sample_count']} | {wr_s} | "
            f"{row.get('avg_realized_pnl_pct', 'n/a')} | {row.get('avg_max_profit_pct', 'n/a')} | "
            f"{row.get('avg_max_loss_pct', 'n/a')} |"
        )
    if result.warnings:
        lines.extend(["", "## Warnings", ""])
        for w in result.warnings:
            lines.append(f"- {w}")
    lines.extend(
        [
            "",
            "## 적용",
            "",
            f"- Live: `{root / THRESHOLD_SUMMARY_FILENAME}`",
            "- `daily-ai-trade-plan` quality gate가 `min_final_score`를 읽음",
            "",
            "Note: read-only tuning. 실주문 없음.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    summary_path = write_threshold_summary(result.merged_summary, root)
    result.output_paths = {
        "tuning_json": json_path.name,
        "markdown": md_path.name,
        "threshold_summary": summary_path.name,
    }
    return json_path, md_path, summary_path


def run_tune_threshold_from_outcomes(
    *,
    outcomes_db: str | Path,
    output_dir: str | Path = "outputs",
    lookback_days: int = 60,
    min_samples: int = 10,
    target_win_rate: float = 0.45,
    min_avg_return: float = 0.0,
    blend_with_validation: float = 0.5,
) -> tuple[OutcomeThresholdTuningResult, Path, Path, Path]:
    result = compute_outcome_threshold_tuning(
        outcomes_db,
        lookback_days=lookback_days,
        min_samples=min_samples,
        target_win_rate=target_win_rate,
        min_avg_return=min_avg_return,
        blend_with_validation=blend_with_validation,
        output_dir=output_dir,
    )
    paths = write_outcome_threshold_outputs(result, output_dir=output_dir)
    return result, *paths


def format_outcome_threshold_console(
    result: OutcomeThresholdTuningResult,
    json_path: Path,
    md_path: Path,
    summary_path: Path,
) -> str:
    g = result.global_block
    return "\n".join(
        [
            "DeepSignal outcome threshold tuning",
            f"Eligible samples: {g.get('eligible_samples')}",
            f"Recommended min_final_score (outcomes): {g.get('recommended_min_final_score')}",
            f"Merged min_final_score (live): {g.get('merged_min_final_score')}",
            f"Blend weight (outcomes): {result.blend_with_validation:.0%}",
            f"JSON: {json_path.as_posix()}",
            f"Markdown: {md_path.as_posix()}",
            f"Threshold summary: {summary_path.as_posix()}",
        ]
    )
