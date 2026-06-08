"""CRYPTO_GATE_MODE / CRYPTO_ENSEMBLE_MODE — buy gate and ensemble policy."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal

GateMode = Literal["hybrid", "ml_primary", "ml_only"]
EnsembleMode = Literal["unanimous", "weighted", "lgbm_only"]

_LOG = logging.getLogger(__name__)

_ML_ONLY_MIN_VAL_SHARPE = 0.5


def crypto_gate_mode() -> GateMode:
    raw = (
        os.environ.get("CRYPTO_GATE_MODE")
        or os.environ.get("DEEPSIGNAL_CRYPTO_GATE_MODE")
        or "hybrid"
    ).strip().lower()
    if raw in ("ml_primary", "ml-primary", "mlprimary"):
        return "ml_primary"
    if raw in ("ml_only", "ml-only", "mlonly"):
        return "ml_only"
    return "hybrid"


def crypto_ensemble_mode() -> EnsembleMode:
    raw = (
        os.environ.get("CRYPTO_ENSEMBLE_MODE")
        or os.environ.get("DEEPSIGNAL_CRYPTO_ENSEMBLE_MODE")
        or "unanimous"
    ).strip().lower()
    if raw in ("weighted", "weight"):
        return "weighted"
    if raw in ("lgbm_only", "lgbm-only", "lgbmonly"):
        return "lgbm_only"
    return "unanimous"


def hybrid_ml_threshold() -> float:
    try:
        return float(os.environ.get("CRYPTO_ML_HYBRID_THRESHOLD", "0.50") or 0.50)
    except ValueError:
        return 0.50


def ml_buy_threshold() -> float:
    from deepsignal.crypto_trading.crypto_ml_gate import default_buy_threshold

    return float(default_buy_threshold())


def effective_ml_threshold() -> float:
    """hybrid → 0.50; ml_primary / ml_only → CRYPTO_ML_BUY_THRESHOLD."""
    if crypto_gate_mode() == "hybrid":
        return hybrid_ml_threshold()
    return ml_buy_threshold()


def skip_rule_score_gate() -> bool:
    """ml_only: do not block on min_final_score."""
    return crypto_gate_mode() == "ml_only"


def skip_min_final_score_block() -> bool:
    """ml_primary / ml_only: final_score used for ranking only."""
    return crypto_gate_mode() in ("ml_primary", "ml_only")


def ml_only_allowed(output_dir: str | Path = "outputs") -> tuple[bool, str]:
    """Phase 2: ml_only only when every fold val_sharpe >= 0.5."""
    p = Path(output_dir) / "crypto_ml_validation_latest.json"
    if not p.is_file():
        return False, "crypto_ml_validation_latest.json missing — run crypto-validate-ml"
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        folds = payload.get("folds") or []
        if not folds:
            return False, "no folds in validation json"
        sharpes = [float(f.get("val_sharpe", 0.0)) for f in folds]
        min_sh = min(sharpes)
        if min_sh >= _ML_ONLY_MIN_VAL_SHARPE:
            return True, f"min_val_sharpe={min_sh:.2f}"
        return False, f"min_val_sharpe={min_sh:.2f}<{_ML_ONLY_MIN_VAL_SHARPE}"
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return False, str(exc)


def effective_gate_mode(output_dir: str | Path = "outputs") -> GateMode:
    """Downgrade ml_only → ml_primary when Phase 2 validation not met."""
    gm = crypto_gate_mode()
    if gm != "ml_only":
        return gm
    ok, msg = ml_only_allowed(output_dir)
    if ok:
        return "ml_only"
    _LOG.warning("[GATE] ml_only blocked (%s) — using ml_primary", msg)
    print(f"[GATE] ml_only blocked ({msg}) — using ml_primary", flush=True)
    return "ml_primary"


def sell_rule_fallback_only() -> bool:
    """When execution engine on: near_tp/near_sl only as fallback."""
    raw = (
        os.environ.get("CRYPTO_SELL_FALLBACK_ONLY", "true")
        or os.environ.get("DEEPSIGNAL_CRYPTO_SELL_FALLBACK_ONLY", "true")
    ).strip().lower()
    return raw not in ("0", "false", "no", "off")


def log_gate_decision(
    *,
    market: str,
    mode: GateMode | None = None,
    prob: float | None = None,
    final_score: float | None = None,
    extra: str = "",
) -> None:
    gm = mode or crypto_gate_mode()
    parts = [f"[GATE] mode={gm}"]
    if prob is not None:
        parts.append(f"prob={prob:.2f}")
    if final_score is not None:
        parts.append(f"score={final_score:.0f}")
    if extra:
        parts.append(extra.strip())
    msg = " ".join(parts)
    _LOG.info(msg)
    print(msg, flush=True)
