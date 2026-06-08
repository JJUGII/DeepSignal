"""
feedback_tuner.py — 실전 피드백 루프 (Phase 4).

체결 완료된 매매 결과(outcome DB)를 분석하여
패스트레인 임계값(min_gsqs, min_pwin)을 자동 조율한다.

알고리즘:
  1. 최근 N일 동안 closed_at IS NOT NULL AND executed=1 레코드 조회
  2. GSQS 구간별·pwin 구간별 승률(realized_pnl_pct > 0) 계산
  3. 목표 승률(기본 52%)을 달성하는 최저 GSQS / pwin floor 탐색
  4. 조율 범위를 원래 ENV 값의 [×0.80, ×1.30]로 제한
  5. 결과를 CRYPTO_FEEDBACK_THRESHOLDS.json에 저장

fastlane.should_fastlane() 이 이 파일을 읽어 동적 임계값을 사용한다.

ENV 플래그:
  CRYPTO_FEEDBACK_TUNING_ENABLED   (기본: false)
  CRYPTO_FEEDBACK_LOOKBACK_DAYS    (기본: 30)
  CRYPTO_FEEDBACK_MIN_SAMPLES      (기본: 10)   — 최소 분석 샘플 수
  CRYPTO_FEEDBACK_TARGET_WIN_RATE  (기본: 0.52) — 목표 승률
  CRYPTO_FEEDBACK_GSQS_STEP        (기본: 2.0)  — GSQS 탐색 단계
  CRYPTO_FEEDBACK_PWIN_STEP        (기본: 0.02) — pwin 탐색 단계
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FEEDBACK_JSON = "CRYPTO_FEEDBACK_THRESHOLDS.json"


# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

def _env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, "")
    return v.strip().lower() in ("1", "true", "yes", "on") if v else default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class FeedbackTunerConfig:
    enabled: bool = False
    lookback_days: int = 30
    min_samples: int = 10
    target_win_rate: float = 0.52
    gsqs_step: float = 2.0
    pwin_step: float = 0.02


def load_feedback_tuner_config() -> FeedbackTunerConfig:
    return FeedbackTunerConfig(
        enabled=_env_bool("CRYPTO_FEEDBACK_TUNING_ENABLED"),
        lookback_days=_env_int("CRYPTO_FEEDBACK_LOOKBACK_DAYS", 30),
        min_samples=_env_int("CRYPTO_FEEDBACK_MIN_SAMPLES", 10),
        target_win_rate=_env_float("CRYPTO_FEEDBACK_TARGET_WIN_RATE", 0.52),
        gsqs_step=_env_float("CRYPTO_FEEDBACK_GSQS_STEP", 2.0),
        pwin_step=_env_float("CRYPTO_FEEDBACK_PWIN_STEP", 0.02),
    )


# ──────────────────────────────────────────────
# 결과 데이터 로드
# ──────────────────────────────────────────────

@dataclass
class OutcomeSample:
    market: str
    final_score: float | None
    model_probability: float | None
    realized_pnl_pct: float | None
    macro_regime: str


def load_closed_buy_samples(
    outcomes_db: str | Path,
    lookback_days: int = 30,
) -> list[OutcomeSample]:
    """
    outcomes DB에서 체결 완료(executed=1, closed_at IS NOT NULL)된
    매수 결과를 로드한다.
    """
    path = Path(outcomes_db)
    if not path.is_file():
        logger.info("feedback_tuner: outcomes DB 없음 (%s)", path)
        return []

    since = (date.today() - timedelta(days=max(1, lookback_days))).isoformat()
    samples: list[OutcomeSample] = []

    try:
        with sqlite3.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT market, final_score, model_probability,
                       realized_pnl_pct, macro_regime
                FROM crypto_recommendation_outcomes
                WHERE side = 'buy'
                  AND executed = 1
                  AND closed_at IS NOT NULL
                  AND substr(created_at, 1, 10) >= ?
                """,
                (since,),
            ).fetchall()

        for row in rows:
            pnl = _safe_float(row["realized_pnl_pct"])
            if pnl is None:
                continue
            samples.append(OutcomeSample(
                market=str(row["market"] or ""),
                final_score=_safe_float(row["final_score"]),
                model_probability=_safe_float(row["model_probability"]),
                realized_pnl_pct=pnl,
                macro_regime=str(row["macro_regime"] or "neutral"),
            ))
    except Exception as exc:
        logger.warning("feedback_tuner: DB 조회 실패: %s", exc)

    return samples


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────
# 최적 floor 탐색
# ──────────────────────────────────────────────

def _find_optimal_floor(
    samples: list[OutcomeSample],
    *,
    key: str,           # "final_score" | "model_probability"
    start: float,
    stop: float,
    step: float,
    target_win_rate: float,
    min_samples: int,
) -> tuple[float | None, dict[str, Any]]:
    """
    start ~ stop 범위를 step씩 올리며,
    해당 floor 이상인 샘플의 승률 ≥ target_win_rate 인
    최저 floor를 반환. 없으면 None.

    Returns: (optimal_floor, diagnostics_dict)
    """
    diag: dict[str, Any] = {}
    optimal: float | None = None

    floor = start
    while floor <= stop + 1e-9:
        subset = [
            s for s in samples
            if (getattr(s, key, None) or 0) >= floor
        ]
        n = len(subset)
        if n >= min_samples:
            wins = sum(1 for s in subset if (s.realized_pnl_pct or 0) > 0)
            wr = wins / n
            diag[f"{floor:.1f}"] = {"n": n, "win_rate": round(wr, 4)}
            if wr >= target_win_rate and optimal is None:
                optimal = floor
        floor = round(floor + step, 6)

    return optimal, diag


# ──────────────────────────────────────────────
# 결과 데이터클래스
# ──────────────────────────────────────────────

@dataclass
class FeedbackThresholds:
    tuned_min_gsqs: float | None = None
    tuned_min_pwin: float | None = None
    overall_win_rate: float | None = None
    total_samples: int = 0
    lookback_days: int = 30
    generated_at: str = ""
    gsqs_diag: dict[str, Any] = field(default_factory=dict)
    pwin_diag: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────
# 핵심 튜닝 함수
# ──────────────────────────────────────────────

def run_feedback_tuning(
    outcomes_db: str | Path,
    output_dir: str | Path,
    cfg: FeedbackTunerConfig | None = None,
    *,
    base_min_gsqs: float = 65.0,
    base_min_pwin: float = 0.52,
) -> FeedbackThresholds:
    """
    outcome DB를 분석하여 최적 임계값을 산출하고
    output_dir/CRYPTO_FEEDBACK_THRESHOLDS.json에 저장한다.

    Args:
        base_min_gsqs: 현재 ENV의 min_gsqs (조율 범위 계산 기준)
        base_min_pwin: 현재 ENV의 min_pwin (조율 범위 계산 기준)
    """
    from deepsignal.live_trading.time_utils import now_kst_iso

    c = cfg or load_feedback_tuner_config()
    result = FeedbackThresholds(
        lookback_days=c.lookback_days,
        generated_at=now_kst_iso(),
    )

    samples = load_closed_buy_samples(outcomes_db, lookback_days=c.lookback_days)
    result.total_samples = len(samples)

    if len(samples) < c.min_samples:
        result.warnings.append(
            f"샘플 부족: {len(samples)}건 < 최소 {c.min_samples}건 — 튜닝 건너뜀"
        )
        logger.info("feedback_tuner: %s", result.warnings[-1])
        _save(result, output_dir)
        return result

    # 전체 승률
    wins = sum(1 for s in samples if (s.realized_pnl_pct or 0) > 0)
    result.overall_win_rate = round(wins / len(samples), 4)
    logger.info(
        "feedback_tuner: %d건 분석, 전체 승률=%.1f%%",
        len(samples), result.overall_win_rate * 100,
    )

    # ── GSQS 최적 floor 탐색 ──────────────────────
    gsqs_start = max(40.0, base_min_gsqs * 0.80)
    gsqs_stop  = min(90.0, base_min_gsqs * 1.30)
    opt_gsqs, gsqs_diag = _find_optimal_floor(
        samples,
        key="final_score",
        start=gsqs_start,
        stop=gsqs_stop,
        step=c.gsqs_step,
        target_win_rate=c.target_win_rate,
        min_samples=c.min_samples,
    )
    result.gsqs_diag = gsqs_diag

    if opt_gsqs is not None:
        # 안전 범위 클램프
        result.tuned_min_gsqs = round(
            max(base_min_gsqs * 0.80, min(base_min_gsqs * 1.30, opt_gsqs)), 2
        )
        logger.info(
            "feedback_tuner: GSQS floor tuned %.1f → %.2f (base=%.1f)",
            base_min_gsqs, result.tuned_min_gsqs, base_min_gsqs,
        )
    else:
        result.warnings.append(
            f"GSQS 튜닝: 목표 승률 {c.target_win_rate*100:.0f}% 달성 구간 없음"
        )

    # ── P(win) 최적 floor 탐색 ───────────────────
    pwin_samples = [s for s in samples if s.model_probability is not None]
    if len(pwin_samples) >= c.min_samples:
        pwin_start = max(0.40, base_min_pwin * 0.80)
        pwin_stop  = min(0.85, base_min_pwin * 1.30)
        opt_pwin, pwin_diag = _find_optimal_floor(
            pwin_samples,
            key="model_probability",
            start=pwin_start,
            stop=pwin_stop,
            step=c.pwin_step,
            target_win_rate=c.target_win_rate,
            min_samples=c.min_samples,
        )
        result.pwin_diag = pwin_diag

        if opt_pwin is not None:
            result.tuned_min_pwin = round(
                max(base_min_pwin * 0.80, min(base_min_pwin * 1.30, opt_pwin)), 4
            )
            logger.info(
                "feedback_tuner: pwin floor tuned %.2f → %.4f (base=%.2f)",
                base_min_pwin, result.tuned_min_pwin, base_min_pwin,
            )
        else:
            result.warnings.append(
                f"pwin 튜닝: 목표 승률 {c.target_win_rate*100:.0f}% 달성 구간 없음"
            )
    else:
        result.warnings.append(
            f"pwin 튜닝: model_probability 샘플 부족 ({len(pwin_samples)}건)"
        )

    _save(result, output_dir)
    return result


# ──────────────────────────────────────────────
# 저장 / 로드
# ──────────────────────────────────────────────

def _save(result: FeedbackThresholds, output_dir: str | Path) -> None:
    path = Path(output_dir) / FEEDBACK_JSON
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("feedback_tuner: 저장 → %s", path)
    except Exception as exc:
        logger.warning("feedback_tuner: 저장 실패: %s", exc)


def load_feedback_thresholds(
    output_dir: str | Path,
    *,
    max_age_hours: float = 25.0,
) -> FeedbackThresholds | None:
    """
    저장된 피드백 임계값 로드.
    파일이 없거나 max_age_hours 이상 경과하면 None 반환.
    """
    path = Path(output_dir) / FEEDBACK_JSON
    if not path.is_file():
        return None
    try:
        import time as _time
        age_hours = (_time.time() - path.stat().st_mtime) / 3600
        if age_hours > max_age_hours:
            logger.debug("feedback_tuner: 파일 오래됨 (%.1fh > %.1fh)", age_hours, max_age_hours)
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return FeedbackThresholds(
            tuned_min_gsqs=_safe_float(data.get("tuned_min_gsqs")),
            tuned_min_pwin=_safe_float(data.get("tuned_min_pwin")),
            overall_win_rate=_safe_float(data.get("overall_win_rate")),
            total_samples=int(data.get("total_samples") or 0),
            lookback_days=int(data.get("lookback_days") or 30),
            generated_at=str(data.get("generated_at") or ""),
            gsqs_diag=dict(data.get("gsqs_diag") or {}),
            pwin_diag=dict(data.get("pwin_diag") or {}),
            warnings=list(data.get("warnings") or []),
        )
    except Exception as exc:
        logger.warning("feedback_tuner: 로드 실패: %s", exc)
        return None


# ──────────────────────────────────────────────
# fastlane 연동: 동적 임계값 반환
# ──────────────────────────────────────────────

def get_effective_fastlane_thresholds(
    output_dir: str | Path,
    *,
    base_min_gsqs: float,
    base_min_pwin: float,
) -> tuple[float, float, str]:
    """
    피드백 임계값이 유효하면 그것을, 아니면 base ENV 값을 반환.

    Returns: (effective_min_gsqs, effective_min_pwin, source)
    """
    if not _env_bool("CRYPTO_FEEDBACK_TUNING_ENABLED"):
        return base_min_gsqs, base_min_pwin, "env(feedback_disabled)"

    fb = load_feedback_thresholds(output_dir)
    if fb is None:
        return base_min_gsqs, base_min_pwin, "env(no_feedback_file)"

    eff_gsqs = fb.tuned_min_gsqs if fb.tuned_min_gsqs is not None else base_min_gsqs
    eff_pwin = fb.tuned_min_pwin if fb.tuned_min_pwin is not None else base_min_pwin
    source = f"feedback(samples={fb.total_samples},wr={fb.overall_win_rate})"
    return eff_gsqs, eff_pwin, source
