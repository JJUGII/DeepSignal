"""Derive min_final_score thresholds from validation backtest signals."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

THRESHOLD_SUMMARY_FILENAME = "AI_VALIDATION_THRESHOLD_SUMMARY.json"
DEFAULT_CANDIDATE_THRESHOLDS = DEFAULT_ANALYSIS_CONDITIONS.score.validation_candidate_scores


@dataclass
class ThresholdGroupResult:
    group: str
    min_final_score: float
    sample_count: int
    win_rate: float
    avg_forward_return_pct: float
    chosen_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationThresholdSummary:
    generated_at: str = ""
    source_validation_json: str | None = None
    forward_days: int = 5
    default_min_final_score: float = DEFAULT_ANALYSIS_CONDITIONS.score.min_final_score_default
    global_threshold: float = DEFAULT_ANALYSIS_CONDITIONS.score.min_final_score_default
    by_symbol: dict[str, float] = field(default_factory=dict)
    by_price_bucket: dict[str, float] = field(default_factory=dict)
    groups: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "source_validation_json": self.source_validation_json,
            "forward_days": self.forward_days,
            "default_min_final_score": self.default_min_final_score,
            "global_threshold": self.global_threshold,
            "by_symbol": dict(self.by_symbol),
            "by_price_bucket": dict(self.by_price_bucket),
            "groups": list(self.groups),
            "warnings": list(self.warnings),
        }

    def resolve_for_symbol(self, symbol: str, *, price_hint: float | None = None) -> float:
        sym = symbol.strip().upper()
        if sym in self.by_symbol:
            return float(self.by_symbol[sym])
        if price_hint is not None:
            bucket = price_bucket(price_hint)
            if bucket in self.by_price_bucket:
                return float(self.by_price_bucket[bucket])
        return float(self.global_threshold)


def price_bucket(price: float) -> str:
    if price >= 50_000:
        return "large"
    if price >= 10_000:
        return "mid"
    return "small"


def _forward_return_pct(
    prices_by_day: dict[str, dict[str, float]],
    *,
    day: str,
    symbol: str,
    forward_days: int,
) -> float | None:
    days = sorted(prices_by_day)
    if day not in days:
        return None
    idx = days.index(day)
    end_idx = idx + int(forward_days)
    if end_idx >= len(days):
        return None
    p0 = prices_by_day[day].get(symbol)
    p1 = prices_by_day[days[end_idx]].get(symbol)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return (float(p1) - float(p0)) / float(p0) * 100.0


def _pick_threshold(
    samples: list[tuple[float, float]],
    *,
    candidate_thresholds: tuple[float, ...],
    min_samples: int,
    target_win_rate: float,
    min_avg_return: float = 0.0,
    default: float = 60.0,
) -> ThresholdGroupResult:
    if len(samples) < min_samples:
        return ThresholdGroupResult(
            group="",
            min_final_score=default,
            sample_count=len(samples),
            win_rate=0.0,
            avg_forward_return_pct=0.0,
            chosen_reason=f"insufficient_samples<{min_samples}",
        )
    chosen = default
    reason = "fallback_default"
    best_win = -1.0
    for thr in sorted(candidate_thresholds):
        subset = [ret for score, ret in samples if score >= thr]
        if len(subset) < min_samples:
            continue
        wins = sum(1 for r in subset if r > 0)
        win_rate = wins / len(subset)
        avg_ret = sum(subset) / len(subset)
        if win_rate >= target_win_rate and avg_ret > min_avg_return:
            if win_rate > best_win or (math.isclose(win_rate, best_win) and thr < chosen):
                chosen = thr
                best_win = win_rate
                reason = f"win_rate>={target_win_rate:.0%},avg_ret>{min_avg_return}"
    if reason == "fallback_default":
        for thr in sorted(candidate_thresholds, reverse=True):
            subset = [ret for score, ret in samples if score >= thr]
            if len(subset) >= max(3, min_samples // 2):
                chosen = thr
                wins = sum(1 for r in subset if r > 0)
                reason = f"relaxed_best_win_rate={wins/len(subset):.0%}"
                break
    subset_final = [ret for score, ret in samples if score >= chosen]
    win_rate = (sum(1 for r in subset_final if r > 0) / len(subset_final)) if subset_final else 0.0
    avg_ret = (sum(subset_final) / len(subset_final)) if subset_final else 0.0
    return ThresholdGroupResult(
        group="",
        min_final_score=float(chosen),
        sample_count=len(subset_final),
        win_rate=float(win_rate),
        avg_forward_return_pct=float(avg_ret),
        chosen_reason=reason,
    )


def compute_threshold_tuning(
    *,
    prices_by_day: dict[str, dict[str, float]],
    signals: dict[tuple[str, str], dict[str, Any]],
    forward_days: int = 5,
    min_samples: int = 8,
    min_samples_symbol: int = 5,
    target_win_rate: float = 0.45,
    default_threshold: float = 60.0,
    candidate_thresholds: tuple[float, ...] = DEFAULT_CANDIDATE_THRESHOLDS,
) -> ValidationThresholdSummary:
    global_samples: list[tuple[float, float]] = []
    by_symbol: dict[str, list[tuple[float, float]]] = defaultdict(list)
    by_bucket: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for (day, symbol), sig in signals.items():
        score = sig.get("final_score")
        if score is None:
            continue
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            continue
        fwd = _forward_return_pct(prices_by_day, day=day, symbol=symbol, forward_days=forward_days)
        if fwd is None:
            continue
        px = prices_by_day.get(day, {}).get(symbol)
        bucket = price_bucket(float(px)) if px else "small"
        pair = (score_f, fwd)
        global_samples.append(pair)
        by_symbol[symbol].append(pair)
        by_bucket[bucket].append(pair)

    groups: list[dict[str, Any]] = []
    global_res = _pick_threshold(
        global_samples,
        candidate_thresholds=candidate_thresholds,
        min_samples=min_samples,
        target_win_rate=target_win_rate,
        default=default_threshold,
    )
    global_res.group = "global"
    groups.append(global_res.to_dict())

    summary = ValidationThresholdSummary(
        forward_days=forward_days,
        default_min_final_score=default_threshold,
        global_threshold=global_res.min_final_score,
    )

    for sym, samples in sorted(by_symbol.items()):
        res = _pick_threshold(
            samples,
            candidate_thresholds=candidate_thresholds,
            min_samples=min_samples_symbol,
            target_win_rate=target_win_rate,
            default=summary.global_threshold,
        )
        res.group = f"symbol:{sym}"
        summary.by_symbol[sym] = res.min_final_score
        groups.append(res.to_dict())

    for bucket, samples in sorted(by_bucket.items()):
        res = _pick_threshold(
            samples,
            candidate_thresholds=candidate_thresholds,
            min_samples=min_samples,
            target_win_rate=target_win_rate,
            default=summary.global_threshold,
        )
        res.group = f"bucket:{bucket}"
        summary.by_price_bucket[bucket] = res.min_final_score
        groups.append(res.to_dict())

    summary.groups = groups
    if len(global_samples) < min_samples:
        summary.warnings.append(f"global_samples={len(global_samples)} < {min_samples}; using defaults")
    return summary


def write_threshold_summary(
    summary: ValidationThresholdSummary,
    output_dir: str | Path,
    *,
    source_validation_json: str | None = None,
) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if source_validation_json:
        summary.source_validation_json = source_validation_json
    path = root / THRESHOLD_SUMMARY_FILENAME
    path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def summary_from_dict(data: dict[str, Any]) -> ValidationThresholdSummary:
    return ValidationThresholdSummary(
        generated_at=str(data.get("generated_at") or ""),
        source_validation_json=data.get("source_validation_json"),
        forward_days=int(data.get("forward_days") or 5),
        default_min_final_score=float(data.get("default_min_final_score") or 60.0),
        global_threshold=float(data.get("global_threshold") or data.get("default_min_final_score") or 60.0),
        by_symbol={str(k).upper(): float(v) for k, v in (data.get("by_symbol") or {}).items()},
        by_price_bucket={str(k): float(v) for k, v in (data.get("by_price_bucket") or {}).items()},
        groups=list(data.get("groups") or []),
        warnings=list(data.get("warnings") or []),
    )


def load_threshold_summary(
    output_dir: str | Path,
    *,
    path: str | Path | None = None,
) -> ValidationThresholdSummary | None:
    root = Path(output_dir)
    target = Path(path) if path else root / THRESHOLD_SUMMARY_FILENAME
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return summary_from_dict(data)


def resolve_min_final_score(
    symbol: str,
    *,
    config: Any,
    summary: ValidationThresholdSummary | None,
    price_hint: float | None = None,
) -> tuple[float, str]:
    """Return (threshold, source_label)."""
    base = float(getattr(config, "min_final_score", 60.0) or 60.0)
    if not bool(getattr(config, "use_validation_tuned_min_score", True)):
        return base, "config"
    if summary is None:
        fallback = float(getattr(config, "validation_tune_fallback_score", base) or base)
        return fallback, "fallback_no_summary"
    override = getattr(config, "min_final_score_by_symbol", None) or {}
    sym = symbol.strip().upper()
    if sym in override:
        return float(override[sym]), "config_override"
    return summary.resolve_for_symbol(sym, price_hint=price_hint), "validation_tuned"
