"""Phase 3 — gate modes, ensemble modes, threshold suggest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepsignal.crypto_trading.crypto_gate_config import (
    crypto_gate_mode,
    crypto_ensemble_mode,
    effective_gate_mode,
    effective_ml_threshold,
    hybrid_ml_threshold,
    ml_only_allowed,
    skip_min_final_score_block,
)
from deepsignal.crypto_trading.crypto_ml_ensemble import (
    weighted_blend_probability,
)
from deepsignal.crypto_trading.crypto_recommendation_quality import check_crypto_validation_gate
from deepsignal.ml.crypto_ml_config_suggest import parse_threshold_report


def test_hybrid_threshold_default() -> None:
    assert hybrid_ml_threshold() == pytest.approx(0.50)


def test_validation_gate_skips_score_when_ml_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_GATE_MODE", "ml_primary")
    assert skip_min_final_score_block()
    status, reasons = check_crypto_validation_gate(
        30.0,
        macro_regime="neutral",
        min_final_score=45.0,
        enforce_min_score=False,
    )
    assert status == "ok"
    assert not reasons


def test_effective_ml_threshold_hybrid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_GATE_MODE", "hybrid")
    assert effective_ml_threshold() == pytest.approx(0.50)


def test_weighted_blend() -> None:
    b = weighted_blend_probability(lgbm_p=0.6, seq_p=0.5, rule_p=0.4)
    assert b is not None
    assert 0.45 < b < 0.55


def test_ml_only_allowed_from_json(tmp_path: Path) -> None:
    payload = {
        "folds": [
            {"val_sharpe": 0.6},
            {"val_sharpe": 0.55},
        ]
    }
    p = tmp_path / "crypto_ml_validation_latest.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    ok, msg = ml_only_allowed(tmp_path)
    assert ok
    assert "0.55" in msg


def test_ml_only_blocked_low_sharpe(tmp_path: Path) -> None:
    payload = {"folds": [{"val_sharpe": 0.3}, {"val_sharpe": 0.6}]}
    (tmp_path / "crypto_ml_validation_latest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    ok, _ = ml_only_allowed(tmp_path)
    assert not ok


def test_effective_gate_downgrades_ml_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CRYPTO_GATE_MODE", "ml_only")
    assert effective_gate_mode(tmp_path) == "ml_primary"


def test_parse_threshold_report_recommendation(tmp_path: Path) -> None:
    md = """# Threshold sweep

## Recommendation

Best Sharpe at `P=0.58` × `N=5m` — Sharpe **1.23**, trades=42, EV=0.12%
"""
    p = tmp_path / "CRYPTO_ML_THRESHOLD_REPORT.md"
    p.write_text(md, encoding="utf-8")
    row = parse_threshold_report(p)
    assert row is not None
    assert row.prob_threshold == pytest.approx(0.58)
    assert row.horizon_minutes == 5


def test_gate_mode_env_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYPTO_GATE_MODE", "ml-primary")
    assert crypto_gate_mode() == "ml_primary"
    monkeypatch.setenv("CRYPTO_ENSEMBLE_MODE", "lgbm-only")
    assert crypto_ensemble_mode() == "lgbm_only"
