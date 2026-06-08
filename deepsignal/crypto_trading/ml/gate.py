"""LightGBM P(win) gate for crypto BUY recommendations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from deepsignal.market_data.feature_engine.spec import FEATURE_NAMES


def ml_buy_gate_enabled() -> bool:
    raw = (
        os.environ.get("CRYPTO_ML_BUY_GATE", "true")
        or os.environ.get("DEEPSIGNAL_CRYPTO_ML_BUY_GATE", "true")
    ).strip().lower()
    return raw not in ("0", "false", "no", "off")


def ml_buy_gate_strict() -> bool:
    """If true, block BUY when model file is missing."""
    raw = (
        os.environ.get("CRYPTO_ML_BUY_GATE_STRICT", "false")
        or os.environ.get("DEEPSIGNAL_CRYPTO_ML_BUY_GATE_STRICT", "false")
    ).strip().lower()
    return raw in ("1", "true", "yes", "on")


def default_buy_threshold() -> float:
    try:
        return float(os.environ.get("CRYPTO_ML_BUY_THRESHOLD", "0.55") or 0.55)
    except ValueError:
        return 0.55


def upbit_market_to_binance_symbol(market: str) -> str:
    m = str(market or "").strip().upper()
    if m.startswith("KRW-"):
        return f"{m.split('-', 1)[1]}USDT"
    if m.endswith("USDT"):
        return m
    return f"{m}USDT"


@dataclass
class MlGateResult:
    allowed: bool
    win_probability: float | None
    threshold: float
    model_path: str | None
    binance_symbol: str
    status: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "win_probability": self.win_probability,
            "threshold": self.threshold,
            "model_path": self.model_path,
            "binance_symbol": self.binance_symbol,
            "status": self.status,
            "reason": self.reason,
        }


class CryptoMlBuyGate:
    """Load LightGBM once; predict P(win) from FeatureEngine + live_state."""

    def __init__(
        self,
        output_dir: str | Path = "outputs",
        *,
        horizon_minutes: int = 5,
        threshold: float | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.horizon_minutes = int(horizon_minutes)
        self.threshold = float(threshold if threshold is not None else default_buy_threshold())
        self._model: Any = None
        self._model_path: Path | None = None
        self._engine: Any = None
        self._predict_fn: Callable[[str], float] | None = None
        self._degenerate: bool | None = None  # 모델이 상수만 뱉는지(고장) 자가진단 캐시

    def _detect_degenerate(self) -> bool:
        """모델이 입력과 무관하게 거의 상수만 출력하면 '고장(degenerate)'으로 판정.

        live_state의 여러 심볼로 예측해 분산이 사실상 0이거나, 전부 극단적으로
        낮으면(<0.02) 망가진 모델로 본다. 결과는 캐시(프로세스 1회 진단).
        """
        if self._degenerate is not None:
            return self._degenerate
        if self._predict_fn is None:
            self._degenerate = False
            return False
        try:
            import os as _os
            if _os.environ.get("CRYPTO_ML_DEGENERATE_CHECK", "true").strip().lower() in ("0", "false", "no", "off"):
                self._degenerate = False
                return False
            live = self.output_dir / "binance_stream" / "live_state.json"
            syms: list[str] = []
            if live.is_file():
                payload = json.loads(live.read_text(encoding="utf-8"))
                syms = [str(s).upper() for s in (payload.get("symbols") or [])][:12]
            if len(syms) < 4:
                syms = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT"]
            preds = []
            for s in syms:
                try:
                    preds.append(float(self._predict_fn(s)))  # type: ignore[misc]
                except Exception:
                    continue
            if len(preds) < 4:
                self._degenerate = False
                return False
            spread = max(preds) - min(preds)
            self._degenerate = bool(spread < 1e-4 or max(preds) < 0.02)
            if self._degenerate:
                import logging as _lg
                _lg.getLogger(__name__).warning(
                    "[ML] 모델 고장 감지(degenerate) — 예측 분산 %.6g, 최대 %.4f. ML 게이트 자동 무력화(fail-open).",
                    spread, max(preds),
                )
                print(
                    f"[ML] 모델 고장 감지 — 예측값이 사실상 상수(분산 {spread:.2g}, 최대 {max(preds):.4f}). "
                    f"ML 게이트 무력화 — 규칙점수로 거래합니다.",
                    flush=True,
                )
        except Exception:
            self._degenerate = False
        return self._degenerate

    def _resolve_model_path(self) -> Path | None:
        model_dir = self.output_dir / "models"
        active = model_dir / "crypto_scalp_lgbm_active.json"
        if active.is_file():
            try:
                meta = json.loads(active.read_text(encoding="utf-8"))
                rel = str(meta.get("model_path") or meta.get("deployed_model") or "")
                if rel:
                    p = Path(rel)
                    if not p.is_file():
                        p = model_dir / p.name
                    if p.is_file():
                        return p
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        for name in (
            f"crypto_scalp_lgbm_{self.horizon_minutes}m.txt",
            "crypto_scalp_lgbm_5m.txt",
            "crypto_scalp_lgbm_10m.txt",
        ):
            p = model_dir / name
            if p.is_file():
                return p
        return None

    def _ensure_loaded(self) -> bool:
        if self._predict_fn is not None:
            return True
        path = self._resolve_model_path()
        if path is None:
            return False
        try:
            from deepsignal.market_data.feature_engine import FeatureEngine
            from deepsignal.ml.crypto_scalp_lgbm import load_lgbm_model, predict_proba

            self._model = load_lgbm_model(path)
            self._model_path = path
            live = self.output_dir / "binance_stream" / "live_state.json"
            self._engine = FeatureEngine()
            if live.is_file():
                self._engine.ingest_live_state(json.loads(live.read_text(encoding="utf-8")))
                self._warmup_bars()  # 봉 히스토리 워밍업(필수): momentum/return 피처 0 방지

            model = self._model
            eng = self._engine

            def _pred(binance_sym: str) -> float:
                vec = eng.compute(binance_sym.upper())
                return float(predict_proba(model, np.asarray(vec).reshape(1, -1))[0])

            self._predict_fn = _pred
            return True
        except Exception:
            self._predict_fn = None
            self._model = None
            return False

    def _warmup_bars(self, n_bars: int = 120) -> None:
        """bars/ 에서 최근 봉을 로드해 returns/ATR/EMA/momentum 등 bar 기반 피처를 채운다.

        이게 없으면 추론 시 모멘텀·수익률 피처 25개가 0이 되어 모델이 상수만 뱉는다.
        """
        if self._engine is None:
            return
        try:
            live = self.output_dir / "binance_stream" / "live_state.json"
            bars_dir = self.output_dir / "binance_stream" / "bars"
            if not bars_dir.is_dir() or not live.is_file():
                return
            payload = json.loads(live.read_text(encoding="utf-8"))
            syms = [str(s).upper() for s in (payload.get("symbols") or [])]
            if syms:
                self._engine._load_historical_bars(bars_dir, syms, n_bars=n_bars)
        except Exception:
            pass

    def refresh_live_state(self) -> None:
        """Reload binance live_state into FeatureEngine (+ 봉 워밍업)."""
        if self._engine is None:
            return
        live = self.output_dir / "binance_stream" / "live_state.json"
        if live.is_file():
            try:
                self._engine.ingest_live_state(json.loads(live.read_text(encoding="utf-8")))
                self._warmup_bars()
            except (OSError, json.JSONDecodeError):
                pass

    def predict_for_upbit_market(self, market: str) -> MlGateResult:
        bsym = upbit_market_to_binance_symbol(market)
        if not ml_buy_gate_enabled():
            return MlGateResult(
                allowed=True,
                win_probability=None,
                threshold=self.threshold,
                model_path=None,
                binance_symbol=bsym,
                status="disabled",
                reason="CRYPTO_ML_BUY_GATE=off",
            )
        if not self._ensure_loaded():
            if ml_buy_gate_strict():
                return MlGateResult(
                    allowed=False,
                    win_probability=None,
                    threshold=self.threshold,
                    model_path=None,
                    binance_symbol=bsym,
                    status="no_model",
                    reason="LightGBM model missing (strict gate)",
                )
            return MlGateResult(
                allowed=True,
                win_probability=None,
                threshold=self.threshold,
                model_path=None,
                binance_symbol=bsym,
                status="skipped",
                reason="no model file — gate skipped",
            )
        # 모델 고장(상수 출력) 자가진단 → fail-open: LGBM 확률 무시(None), 통과 허용
        if self._detect_degenerate():
            return MlGateResult(
                allowed=True,
                win_probability=None,
                threshold=self.threshold,
                model_path=str(self._model_path) if self._model_path else None,
                binance_symbol=bsym,
                status="degenerate_failopen",
                reason="model degenerate (constant output) — gate failed open",
            )
        try:
            p = float(self._predict_fn(bsym))  # type: ignore[misc]
        except Exception as exc:
            return MlGateResult(
                allowed=not ml_buy_gate_strict(),
                win_probability=None,
                threshold=self.threshold,
                model_path=str(self._model_path) if self._model_path else None,
                binance_symbol=bsym,
                status="error",
                reason=str(exc),
            )
        ok = p >= self.threshold
        return MlGateResult(
            allowed=ok,
            win_probability=p,
            threshold=self.threshold,
            model_path=str(self._model_path) if self._model_path else None,
            binance_symbol=bsym,
            status="pass" if ok else "below_threshold",
            reason=f"P(win)={p:.3f} {'≥' if ok else '<'} {self.threshold}",
        )

    def feature_snapshot_for_market(self, market: str) -> dict[str, float] | None:
        bsym = upbit_market_to_binance_symbol(market)
        if self._engine is None and not self._ensure_loaded():
            try:
                from deepsignal.market_data.feature_engine import FeatureEngine

                live = self.output_dir / "binance_stream" / "live_state.json"
                if not live.is_file():
                    return None
                eng = FeatureEngine()
                eng.ingest_live_state(json.loads(live.read_text(encoding="utf-8")))
                return eng.feature_dict(bsym)
            except Exception:
                return None
        if self._engine is None:
            return None
        try:
            return self._engine.feature_dict(bsym)
        except Exception:
            return None
