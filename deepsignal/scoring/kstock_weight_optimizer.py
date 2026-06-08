"""K-GSQS 가중치 자동 최적화 — 국내/해외 주식 K-GSQS 신호 로그 기반.

WeightOptimizer (코인)와 동일 알고리즘이지만:
  - 6개 서브스코어 (trend/volume/orderbook/momentum/market/risk)
  - MIN_SAMPLES=50 (주식은 신호 발생이 느림)
  - _apply_to_scorer() → kstock_scorer.WEIGHTS 갱신

output_dir 구조:
  {output_dir}/kstock/signal_log.jsonl   ← 신호 로그
  {output_dir}/kstock_optimized_weights.json ← 최적화 결과 저장
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

# 최적화 실행 최소 샘플 수 (주식 신호는 느리게 쌓이므로 50으로 낮춤)
MIN_SAMPLES = 50
# 마지막 최적화 이후 신규 샘플 수
UPDATE_INTERVAL = 20

# K-GSQS 기본 가중치 (kstock_scorer.py 와 동기화)
DEFAULT_WEIGHTS: dict[str, float] = {
    "trend":     0.20,
    "volume":    0.20,
    "orderbook": 0.20,
    "momentum":  0.20,
    "market":    0.10,
    "risk":      0.10,
}

COMPONENT_KEYS = list(DEFAULT_WEIGHTS.keys())


def _safe_float(v: Any) -> float:
    try:
        f = float(v)
        return f if not math.isnan(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _kst_iso(ts: int) -> str:
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(ts, tz=kst).strftime("%Y-%m-%d %H:%M:%S")


class KStockWeightOptimizer:
    """K-GSQS 신호 로그 기반 가중치 자동 최적화.

    Args:
        output_dir: output/kis_stream/ 또는 output/kis_overseas/ 경로.
                    signal_log은 {output_dir}/kstock/signal_log.jsonl 에서 읽음.
        horizon_minutes: 승률 계산 기준 시간대 (1/3/5/15).
        asset_label: 로그용 라벨 ("국장" 또는 "해외").
    """

    def __init__(
        self,
        output_dir: Path | str,
        horizon_minutes: int = 5,
        asset_label: str = "주식",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.asset_label = asset_label
        self.log_path = self.output_dir / "kstock" / "signal_log.jsonl"
        self.weights_path = self.output_dir / "kstock_optimized_weights.json"
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
        ])  # (N, 6)

        # 결과 벡터: horizon분 후 수익률
        y = np.array([
            _safe_float(r.get(f"ret_{self.horizon}m", 0.0))
            for r in records
        ])  # (N,)

        # 목적함수: 음의 Sharpe Ratio
        def neg_sharpe(w: "np.ndarray") -> float:
            w = np.abs(w)
            w = w / w.sum()
            scores = X @ w
            threshold = np.percentile(scores, 50)
            mask = scores >= threshold
            if mask.sum() < 5:
                return 1.0
            selected_ret = y[mask]
            mean_ret = selected_ret.mean()
            std_ret = selected_ret.std()
            if std_ret < 1e-8:
                return -mean_ret * 100
            return -(mean_ret / std_ret)

        w0 = np.array([DEFAULT_WEIGHTS[k] for k in COMPONENT_KEYS])
        constraints = {"type": "eq", "fun": lambda w: w.sum() - 1.0}
        bounds = [(0.02, 0.40)] * len(COMPONENT_KEYS)

        result = minimize(
            neg_sharpe, w0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )

        if not result.success:
            return {
                "error": f"최적화 실패: {result.message}",
                "weights": DEFAULT_WEIGHTS,
                "n_samples": len(records),
                "applied": False,
            }

        w_opt = np.abs(result.x)
        w_opt = w_opt / w_opt.sum()
        optimized = {k: round(float(w_opt[i]), 4) for i, k in enumerate(COMPONENT_KEYS)}

        # 성과 측정
        scores_opt = X @ w_opt
        threshold = np.percentile(scores_opt, 50)
        mask = scores_opt >= threshold
        win_rate = float((y[mask] > 0).mean()) if mask.sum() > 0 else 0.5

        scores_def = X @ w0
        threshold_def = np.percentile(scores_def, 50)
        mask_def = scores_def >= threshold_def
        win_rate_def = float((y[mask_def] > 0).mean()) if mask_def.sum() > 0 else 0.5

        improvement = round(win_rate - win_rate_def, 4)

        applied = improvement >= 0
        if applied:
            self._apply_to_scorer(optimized)

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
            "asset_label": self.asset_label,
        }

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

        last_optimized_at: str | None = None
        last_improvement: float | None = None
        last_applied: bool | None = None
        last_win_rate: float | None = None
        last_default_win_rate: float | None = None

        if self.weights_path.exists():
            try:
                saved = json.loads(self.weights_path.read_text(encoding="utf-8"))
                ts = saved.get("optimized_at")
                if ts:
                    last_optimized_at = _kst_iso(int(ts))
                last_improvement = saved.get("improvement")
                last_applied = saved.get("applied")
                last_win_rate = saved.get("expected_win_rate")
                last_default_win_rate = saved.get("default_win_rate")
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
            "last_win_rate": last_win_rate,
            "last_default_win_rate": last_default_win_rate,
            "asset_label": self.asset_label,
        }

    # ── 내부 헬퍼 ────────────────────────────────────────────────

    def _load_complete(self) -> list[dict[str, Any]]:
        """outcome_complete=True 레코드만 로드."""
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
        """최적화된 가중치를 kstock_scorer.WEIGHTS에 즉시 반영."""
        try:
            from deepsignal.scoring import kstock_scorer
            for k, v in weights.items():
                if k in kstock_scorer.WEIGHTS:
                    kstock_scorer.WEIGHTS[k] = float(v)
        except Exception:
            pass
