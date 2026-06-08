"""Ensemble: LightGBM + LSTM/Transformer + rule score — mode via CRYPTO_ENSEMBLE_MODE."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from deepsignal.crypto_trading.crypto_gate_config import (
    crypto_ensemble_mode,
    crypto_gate_mode,
    effective_gate_mode,
    effective_ml_threshold,
)
from deepsignal.crypto_trading.crypto_ml_gate import (
    CryptoMlBuyGate,
    default_buy_threshold,
    ml_buy_gate_enabled,
    upbit_market_to_binance_symbol,
)
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto

_WEIGHTED_LGBM = 0.5
_WEIGHTED_SEQ = 0.3
_WEIGHTED_RULE = 0.2


def fmt_ml_prob(value: float | None, *, ndigits: int = 3) -> str:
    """Safe probability formatting for Telegram/logs (never raises on None)."""
    if value is None:
        return "n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    return f"{v:.{ndigits}f}"


def ensemble_enabled() -> bool:
    raw = (
        os.environ.get("CRYPTO_ML_ENSEMBLE", "true")
        or os.environ.get("DEEPSIGNAL_CRYPTO_ML_ENSEMBLE", "true")
    ).strip().lower()
    return raw not in ("0", "false", "no", "off")


def rule_score_to_probability(final_score: float | None, *, reference: float | None = None) -> float:
    ref = float(reference if reference is not None else _CRYPTO.score_reference)
    if final_score is None:
        return 0.5
    fs = float(final_score)
    return float(np.clip(0.5 + (fs - ref) / (ref * 2.0), 0.0, 1.0))


def weighted_blend_probability(
    *,
    lgbm_p: float | None,
    seq_p: float | None,
    rule_p: float | None,
) -> float | None:
    parts: list[tuple[float, float]] = []
    if lgbm_p is not None:
        parts.append((_WEIGHTED_LGBM, float(lgbm_p)))
    if seq_p is not None:
        parts.append((_WEIGHTED_SEQ, float(seq_p)))
    if rule_p is not None:
        parts.append((_WEIGHTED_RULE, float(rule_p)))
    if not parts:
        return None
    w_sum = sum(w for w, _ in parts)
    return float(sum(w * p for w, p in parts) / w_sum)


@dataclass
class EnsembleResult:
    allowed: bool
    lgbm_p: float | None
    seq_p: float | None
    rule_p: float | None
    blended_p: float | None
    threshold: float
    status: str
    reason: str
    ensemble_mode: str
    seq_fallback: bool
    model_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "lgbm_p": self.lgbm_p,
            "seq_p": self.seq_p,
            "rule_p": self.rule_p,
            "blended_p": self.blended_p,
            "threshold": self.threshold,
            "status": self.status,
            "reason": self.reason,
            "ensemble_mode": self.ensemble_mode,
            "seq_fallback": self.seq_fallback,
            "model_paths": self.model_paths,
        }


class CryptoMlEnsemble:
    def __init__(
        self,
        output_dir: str | Path = "outputs",
        *,
        horizon_minutes: int = 5,
        threshold: float | None = None,
        seq_kind: str = "lstm",
    ) -> None:
        self.output_dir = Path(output_dir)
        self.horizon = int(horizon_minutes)
        self.threshold = float(threshold if threshold is not None else default_buy_threshold())
        self.seq_kind = str(seq_kind or "lstm").lower()
        self._lgbm = CryptoMlBuyGate(output_dir, horizon_minutes=horizon_minutes, threshold=self.threshold)
        self._seq_model: Any = None
        self._seq_payload: dict[str, Any] = {}
        self._seq_path: Path | None = None

    def _load_seq(self) -> bool:
        if self._seq_model is not None:
            return True
        for kind in (self.seq_kind, "lstm", "transformer"):
            p = self.output_dir / "models" / f"crypto_scalp_{kind}_{self.horizon}m.pt"
            if not p.is_file():
                continue
            try:
                from deepsignal.ml.crypto_scalp_seq_models import load_sequence_model

                self._seq_model, self._seq_payload = load_sequence_model(p)
                self._seq_path = p
                return True
            except Exception:
                continue
        return False

    def _sequence_probability(self, upbit_market: str) -> tuple[float | None, bool]:
        """Return (seq_p, bars_sufficient)."""
        if not self._load_seq():
            return None, False
        from deepsignal.ml.crypto_scalp_inference import recent_sequence_matrix
        from deepsignal.ml.crypto_scalp_seq_models import predict_sequence_proba

        bsym = upbit_market_to_binance_symbol(upbit_market)
        seq_len = int(self._seq_payload.get("seq_len") or 30)
        bars_dir = self.output_dir / "binance_stream" / "bars"
        mat = recent_sequence_matrix(bars_dir, bsym, seq_len=seq_len)
        if mat is None:
            return None, False
        return float(predict_sequence_proba(self._seq_model, mat)[0]), True

    def _resolve_mode(
        self,
        *,
        seq_bars_ok: bool,
        gate_mode: str,
    ) -> tuple[str, bool]:
        if gate_mode == "ml_only":
            return "lgbm_only", True
        mode = crypto_ensemble_mode()
        if mode != "lgbm_only" and not seq_bars_ok:
            return "lgbm_only", True
        return mode, False

    def predict(
        self,
        upbit_market: str,
        *,
        final_score: float | None = None,
    ) -> EnsembleResult:
        threshold = effective_ml_threshold()
        gate_mode = effective_gate_mode(self.output_dir)

        if not ensemble_enabled() and not ml_buy_gate_enabled():
            return EnsembleResult(
                allowed=True,
                lgbm_p=None,
                seq_p=None,
                rule_p=None,
                blended_p=None,
                threshold=threshold,
                status="disabled",
                reason="ensemble off",
                ensemble_mode="off",
                seq_fallback=False,
                model_paths={},
            )

        lgbm_r = self._lgbm.predict_for_upbit_market(upbit_market)
        lgbm_p = lgbm_r.win_probability

        seq_p, seq_bars_ok = self._sequence_probability(upbit_market)
        seq_path = str(self._seq_path) if self._seq_path else ""

        rule_p = rule_score_to_probability(final_score)
        mode, seq_fallback = self._resolve_mode(seq_bars_ok=seq_bars_ok, gate_mode=gate_mode)

        blended = weighted_blend_probability(lgbm_p=lgbm_p, seq_p=seq_p, rule_p=rule_p)

        if not ensemble_enabled():
            allowed = lgbm_r.allowed if lgbm_p is not None else True
            status = lgbm_r.status
            reason = f"LGBM={fmt_ml_prob(lgbm_p)}"
            return EnsembleResult(
                allowed=allowed,
                lgbm_p=lgbm_p,
                seq_p=seq_p,
                rule_p=rule_p,
                blended_p=blended,
                threshold=threshold,
                status=status,
                reason=reason,
                ensemble_mode="lgbm_gate_only",
                seq_fallback=seq_fallback,
                model_paths={"lgbm": lgbm_r.model_path or "", "sequence": seq_path},
            )

        if mode == "lgbm_only":
            if lgbm_p is None:
                allowed = bool(lgbm_r.allowed)
                status = str(lgbm_r.status or "lgbm_only_no_prob")
                reason = f"LGBM=n/a ({lgbm_r.reason})"
            else:
                allowed = float(lgbm_p) >= float(threshold)
                status = "lgbm_only_pass" if allowed else "lgbm_only_veto"
                reason = f"LGBM={fmt_ml_prob(lgbm_p)} (need ≥ {fmt_ml_prob(threshold)})"
            if seq_fallback:
                reason += "; seq_bars_fallback"
        elif mode == "weighted":
            if blended is None:
                allowed = lgbm_r.allowed
                status = lgbm_r.status
            else:
                allowed = blended >= threshold
                status = "weighted_pass" if allowed else "weighted_veto"
            reason = (
                f"blend={fmt_ml_prob(blended)}"
                f" (LGBM={fmt_ml_prob(lgbm_p)}"
                + (f", SEQ={fmt_ml_prob(seq_p)}" if seq_p is not None else "")
                + (f", RULE={fmt_ml_prob(rule_p)}" if rule_p is not None else "")
                + f"; need ≥ {fmt_ml_prob(threshold)})"
            )
            if seq_fallback:
                reason += "; seq_bars_fallback"
        else:
            probs = [p for p in (lgbm_p, seq_p, rule_p) if p is not None]
            if not probs:
                allowed = lgbm_r.allowed
                status = lgbm_r.status
            else:
                allowed = all(p >= threshold for p in probs)
                status = "ensemble_pass" if allowed else "ensemble_veto"
            reason_bits = []
            if lgbm_p is not None:
                reason_bits.append(f"LGBM={fmt_ml_prob(lgbm_p)}")
            if seq_p is not None:
                reason_bits.append(f"SEQ={fmt_ml_prob(seq_p)}")
            elif seq_fallback:
                reason_bits.append("SEQ=skipped")
            if rule_p is not None:
                reason_bits.append(f"RULE={fmt_ml_prob(rule_p)}")
            reason = "; ".join(reason_bits) + f" (need all ≥ {fmt_ml_prob(threshold)})"

        return EnsembleResult(
            allowed=allowed,
            lgbm_p=lgbm_p,
            seq_p=seq_p,
            rule_p=rule_p,
            blended_p=blended,
            threshold=threshold,
            status=status,
            reason=reason,
            ensemble_mode=mode,
            seq_fallback=seq_fallback,
            model_paths={
                "lgbm": lgbm_r.model_path or "",
                "sequence": seq_path,
            },
        )
