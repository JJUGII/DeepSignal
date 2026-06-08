"""Scalping mode threshold recovery."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import (
    apply_active_thresholds_to_runner,
    load_active_crypto_thresholds,
    reset_scalping_active_thresholds,
)
from deepsignal.crypto_trading.crypto_position_sizing import merge_tp_sl
from deepsignal.crypto_trading.crypto_recommendation_quality import resolve_crypto_min_final_score
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto


def test_resolve_min_final_score_uses_scalping_floor():
    score, src = resolve_crypto_min_final_score(output_dir="outputs")
    assert score == float(_CRYPTO.min_final_score)
    assert src == "scalping_default"


def test_merge_tp_sl_keeps_scalping_tp_sl(tmp_path: Path):
    reset_scalping_active_thresholds(tmp_path)
    tuned = load_active_crypto_thresholds(tmp_path)
    assert tuned is not None
    tp, sl, _, _, mvr, source = merge_tp_sl(tuned, None)
    assert tp == float(_CRYPTO.take_profit_pct)
    assert sl == float(_CRYPTO.stop_loss_pct)
    assert mvr <= float(_CRYPTO.outcome_tune_max_volume_ratio)
    assert source == "scalping_default"


def test_merge_tp_sl_ignores_atr_in_scalping_mode(tmp_path: Path):
    reset_scalping_active_thresholds(tmp_path)
    tuned = load_active_crypto_thresholds(tmp_path)
    tp, sl, _, _, _, source = merge_tp_sl(tuned, 2.01)
    assert tp == float(_CRYPTO.take_profit_pct)
    assert sl == float(_CRYPTO.stop_loss_pct)
    assert source == "scalping_default"


def test_reset_scalping_active_thresholds(tmp_path: Path):
    path = tmp_path / "CRYPTO_ACTIVE_THRESHOLDS.json"
    path.write_text(
        json.dumps(
            {
                "take_profit_pct": 10.0,
                "stop_loss_pct": -4.2,
                "min_volume_ratio": 0.85,
            }
        ),
        encoding="utf-8",
    )

    from dataclasses import dataclass

    @dataclass
    class _Cfg:
        take_profit_pct: float = 0.0
        stop_loss_pct: float = 0.0
        min_volume_ratio: float = 0.0
        take_profit_buffer_pct: float = 0.0
        stop_loss_buffer_pct: float = 0.0

    cfg = _Cfg()
    reset_scalping_active_thresholds(tmp_path)
    apply_active_thresholds_to_runner(cfg, tmp_path)
    assert cfg.take_profit_pct == 2.0
    assert cfg.stop_loss_pct == -1.5
    assert cfg.min_volume_ratio == 0.3
