"""GSQS 가중치 자동 최적화 — 신호 로그 데이터 기반 ML 교정.

알고리즘:
    1. signal_log.jsonl에서 완성 레코드 로드
    2. 각 레코드의 sub_scores (trend/volume/orderbook 등) 사용
    3. scipy.optimize.minimize로 가중치 최적화
       - 목적함수: Sharpe Ratio 최대화 (수익/변동성)
       - 제약: 가중치 합 = 1.0, 각 가중치 > 0
    4. 최적 가중치를 outputs/optimized_weights.json에 저장

자동 실행 조건:
    - 완성 신호 ≥ 200건 (MIN_SAMPLES)
    - 마지막 최적화 이후 50건 신규 신호 누적 (UPDATE_INTERVAL)

사용::
    optimizer = WeightOptimizer(Path("outputs"))
    result = optimizer.run()
    print(result)  # {"weights": {...}, "expected_win_rate": 0.62, ...}
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

# 최적화 실행 최소 샘플 수
MIN_SAMPLES = 200
# 마지막 최적화 이후 신규 샘플 수 (이 이상이면 재최적화)
UPDATE_INTERVAL = 50

# 현재 GSQS 기본 가중치
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend":     0.15,
    "volume":    0.15,
    "orderbook": 0.20,
    "tradeflow": 0.20,
    "futures":   0.15,
    "risk":      0.10,
    "market":    0.05,
}

COMPONENT_KEYS = list(DEFAULT_WEIGHTS.keys())


def _safe_float(v: Any) -> float:
    try:
        f = float(v)
        return f if not math.isnan(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


class WeightOptimizer:
    """신호 로그 기반 GSQS 가중치 자동 최적화.

    Args:
        output_dir: outputs/ 경로. signal_log.jsonl과 optimized_weights.json 모두 이 경로 사용.
        horizon_minutes: 승률 계산 기준 시간대 (1/3/5/15)
    """

    def __init__(
        self,
        output_dir: Path | str,
        horizon_minutes: int = 5,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.log_path = self.output_dir / "signal_log.jsonl"
        self.weights_path = self.output_dir / "optimized_weights.json"
        self.horizon = horizon_minutes

    # ── 공개 API ────────────────────────────────────────────────

    def should_run(self) -> bool:
        """최적화 실행이 필요한지 확인."""
        records = self._load_complete()
        if len(records) < MIN_SAMPLES:
            return False

        if not self.weights_path.exists():
            return True

        try:
            saved = json.loads(self.weights_path.read_text(encoding="utf-8"))
            last_n = int(saved.get("n_samples", 0))
            return (len(records) - last_n) >= UPDATE_INTERVAL
        except Exception:
            return True

    def run(self) -> dict[str, Any]:
        """가중치 최적화 실행. 결과 딕셔너리 반환."""
        try:
            from scipy.optimize import minimize  # type: ignore[import]
            import numpy as np
        except ImportError:
            return {"error": "scipy 미설치 — pip install scipy", "weights": DEFAULT_WEIGHTS}

        records = self._load_complete()
        if len(records) < MIN_SAMPLES:
            return {
                "error": f"샘플 부족: {len(records)}/{MIN_SAMPLES}",
                "weights": DEFAULT_WEIGHTS,
            }

        # sub_scores 행렬 구성
        X = np.array([
            [_safe_float(r["sub_scores"].get(k, 50.0)) for k in COMPONENT_KEYS]
            for r in records
        ])  # (N, 7)

        # 결과 벡터: horizon분 후 수익률
        y = np.array([
            _safe_float(r.get(f"ret_{self.horizon}m", 0.0))
            for r in records
        ])  # (N,)

        # 목적함수: 음의 Sharpe Ratio (최소화 = Sharpe 최대화)
        def neg_sharpe(w: "np.ndarray") -> float:
            w = np.abs(w)
            w = w / w.sum()
            scores = X @ w            # 가중 합산 점수 (N,)
            # 점수 임계값 기반 필터링 (상위 50% 신호만)
            threshold = np.percentile(scores, 50)
            mask = scores >= threshold
            if mask.sum() < 10:
                return 1.0            # 페널티
            selected_ret = y[mask]
            mean_ret = selected_ret.mean()
            std_ret = selected_ret.std()
            if std_ret < 1e-8:
                return -mean_ret * 100  # 분산 0이면 평균 수익으로 대체
            return -(mean_ret / std_ret)  # 음의 Sharpe

        # 초기값: 현재 기본 가중치
        w0 = np.array([DEFAULT_WEIGHTS[k] for k in COMPONENT_KEYS])

        # 제약: 합 = 1, 각 가중치 0.02 ~ 0.40
        constraints = {"type": "eq", "fun": lambda w: w.sum() - 1.0}
        bounds = [(0.02, 0.40)] * len(COMPONENT_KEYS)

        result = minimize(
            neg_sharpe,
            w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )

        if not result.success:
            # 최적화 실패 시 기본 가중치 반환
            return {
                "error": f"최적화 실패: {result.message}",
                "weights": DEFAULT_WEIGHTS,
                "n_samples": len(records),
                "applied": False,
            }

        w_opt = np.abs(result.x)
        w_opt = w_opt / w_opt.sum()
        optimized = {k: round(float(w_opt[i]), 4) for i, k in enumerate(COMPONENT_KEYS)}

        # 최적화 성과 측정
        scores_opt = X @ w_opt
        threshold = np.percentile(scores_opt, 50)
        mask = scores_opt >= threshold
        win_rate = float((y[mask] > 0).mean()) if mask.sum() > 0 else 0.5

        # 기본 가중치 성과 비교
        scores_def = X @ w0
        threshold_def = np.percentile(scores_def, 50)
        mask_def = scores_def >= threshold_def
        win_rate_def = float((y[mask_def] > 0).mean()) if mask_def.sum() > 0 else 0.5

        improvement = round(win_rate - win_rate_def, 4)

        # 퇴보 방지: improvement < 0이면 저장만 하고 적용하지 않음
        applied = improvement >= 0
        if applied:
            self._apply_to_scorer(optimized)
        else:
            import logging as _log
            _log.getLogger(__name__).info(
                "퇴보 방지: improvement=%.4f — 기본 가중치 유지", improvement
            )

        output = {
            "weights": optimized,
            "default_weights": DEFAULT_WEIGHTS,
            "n_samples": len(records),
            "horizon_minutes": self.horizon,
            "expected_win_rate": round(win_rate, 4),
            "default_win_rate": round(win_rate_def, 4),
            "improvement": improvement,
            "applied": applied,
            "optimized_at": int(time.time()),
        }

        # 파일 저장 (결과 기록은 항상)
        self.weights_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        return output

    def load_weights(self) -> dict[str, float]:
        """저장된 최적화 가중치 로드. 없으면 기본값 반환."""
        if not self.weights_path.exists():
            return dict(DEFAULT_WEIGHTS)
        try:
            saved = json.loads(self.weights_path.read_text(encoding="utf-8"))
            w = saved.get("weights", {})
            # 모든 키 존재 여부 검증
            if all(k in w for k in COMPONENT_KEYS):
                return {k: float(w[k]) for k in COMPONENT_KEYS}
        except Exception:
            pass
        return dict(DEFAULT_WEIGHTS)

    def status(self) -> dict[str, Any]:
        """현재 최적화 상태 요약."""
        records = self._load_complete()
        n = len(records)
        weights = self.load_weights()

        next_run_at = max(0, MIN_SAMPLES - n) if n < MIN_SAMPLES else 0
        progress_pct = min(100.0, round(n / MIN_SAMPLES * 100, 1))

        # 마지막 최적화 메타
        last_optimized_at: str | None = None
        last_improvement: float | None = None
        last_applied: bool | None = None
        if self.weights_path.exists():
            try:
                saved = json.loads(self.weights_path.read_text(encoding="utf-8"))
                ts = saved.get("optimized_at")
                if ts:
                    last_optimized_at = _kst_iso(int(ts))
                last_improvement = saved.get("improvement")
                last_applied = saved.get("applied")
            except Exception:
                pass

        return {
            "n_complete_signals": n,
            "min_for_optimization": MIN_SAMPLES,
            "ready_to_optimize": n >= MIN_SAMPLES,
            "progress_pct": progress_pct,
            "next_run_at": next_run_at,
            "should_run": self.should_run(),
            "current_weights": weights,
            "weights_file": str(self.weights_path),
            "last_optimized_at": last_optimized_at,
            "last_improvement": last_improvement,
            "last_applied": last_applied,
        }

    # ── 내부 헬퍼 ────────────────────────────────────────────────

    def _load_complete(self) -> list[dict[str, Any]]:
        """완성 레코드(outcome_complete=True)만 로드."""
        if not self.log_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("outcome_complete") and d.get("sub_scores"):
                    records.append(d)
            except json.JSONDecodeError:
                continue
        return records

    def _apply_to_scorer(self, weights: dict[str, float]) -> None:
        """최적화된 가중치를 scalping_scorer의 _WEIGHTS에 즉시 반영."""
        try:
            from deepsignal.crypto_trading.signal import scalping_scorer
            for k, v in weights.items():
                if k in scalping_scorer._WEIGHTS:
                    scalping_scorer._WEIGHTS[k] = float(v)
        except Exception:
            pass


def _kst_iso(ts: int) -> str:
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(ts, tz=kst).strftime("%Y-%m-%d %H:%M:%S")


# ── CLI 직접 실행 ──────────────────────────────────────────────────

def run_optimization(output_dir: str = "outputs") -> None:
    """커맨드라인에서 직접 실행용."""
    optimizer = WeightOptimizer(Path(output_dir))
    status = optimizer.status()
    print(f"신호 수: {status['n_complete_signals']}/{status['min_for_optimization']}")

    if not status["ready_to_optimize"]:
        remaining = status["min_for_optimization"] - status["n_complete_signals"]
        print(f"아직 데이터 부족 — {remaining}건 더 수집 필요")
        return

    print("가중치 최적화 실행 중...")
    result = optimizer.run()

    if "error" in result:
        print(f"오류: {result['error']}")
        return

    print(f"\n=== 최적화 완료 ===")
    print(f"샘플 수: {result['n_samples']}")
    print(f"기본 승률: {result['default_win_rate']:.1%}")
    print(f"최적화 후: {result['expected_win_rate']:.1%} (+{result['improvement']:.1%})")
    print(f"\n최적 가중치:")
    for k, v in result["weights"].items():
        default = DEFAULT_WEIGHTS[k]
        arrow = "↑" if v > default else "↓" if v < default else "="
        print(f"  {k:12}: {v:.3f}  (기본 {default:.2f} {arrow})")


if __name__ == "__main__":
    import sys
    run_optimization(sys.argv[1] if len(sys.argv) > 1 else "outputs")
