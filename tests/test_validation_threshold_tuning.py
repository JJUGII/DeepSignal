"""Tests for validation-driven min_final_score tuning."""

from __future__ import annotations

import json
from pathlib import Path

from deepsignal.live_trading.ai_recommendation.recommendation_model import RecommendationConfig
from deepsignal.live_trading.ai_recommendation.recommendation_quality import apply_per_symbol_quality_gates
from deepsignal.live_trading.ai_recommendation.validation_threshold_tuning import (
    THRESHOLD_SUMMARY_FILENAME,
    compute_threshold_tuning,
    load_threshold_summary,
    resolve_min_final_score,
    summary_from_dict,
    write_threshold_summary,
)


def _prices() -> dict[str, dict[str, float]]:
    days = [f"2024-01-{d:02d}" for d in range(1, 16)]
    out: dict[str, dict[str, float]] = {d: {} for d in days}
    px = 100.0
    for d in days:
        out[d]["AAA"] = px
        px += 2.0
    return out


def _signals_high_score_wins() -> dict[tuple[str, str], dict]:
    prices = _prices()
    days = sorted(prices)
    out: dict[tuple[str, str], dict] = {}
    for i, day in enumerate(days[:-6]):
        score = 72.0 if i % 2 == 0 else 52.0
        out[(day, "AAA")] = {"final_score": score, "action": "BUY"}
    return out


def test_compute_threshold_tuning_returns_symbol_and_bucket_maps() -> None:
    tuning = compute_threshold_tuning(
        prices_by_day=_prices(),
        signals=_signals_high_score_wins(),
        forward_days=5,
        min_samples=3,
        min_samples_symbol=2,
        target_win_rate=0.45,
    )
    assert 50.0 <= tuning.global_threshold <= 75.0
    assert "AAA" in tuning.by_symbol
    assert "small" in tuning.by_price_bucket


def test_write_and_load_threshold_summary(tmp_path: Path) -> None:
    tuning = compute_threshold_tuning(
        prices_by_day=_prices(),
        signals=_signals_high_score_wins(),
        min_samples=3,
        min_samples_symbol=2,
    )
    path = write_threshold_summary(tuning, tmp_path)
    assert path.name == THRESHOLD_SUMMARY_FILENAME
    loaded = load_threshold_summary(tmp_path)
    assert loaded is not None
    assert loaded.global_threshold == tuning.global_threshold


def test_resolve_min_final_score_uses_symbol_override() -> None:
    summary = summary_from_dict({"global_threshold": 60.0, "by_symbol": {"AAA": 68.0}})
    cfg = RecommendationConfig(use_validation_tuned_min_score=True, min_final_score=60.0)
    thr, src = resolve_min_final_score("AAA", config=cfg, summary=summary)
    assert thr == 68.0
    assert src == "validation_tuned"


def test_apply_gates_uses_tuned_threshold(tmp_path: Path) -> None:
    from deepsignal.live_trading.ai_recommendation.cost_model import CostModel
    from deepsignal.live_trading.ai_recommendation.liquidity_model import LiquidityConfig
    from deepsignal.live_trading.ai_recommendation.recommendation_model import RecommendationResult

    summary = summary_from_dict({"global_threshold": 70.0, "by_symbol": {}})
    write_threshold_summary(summary, tmp_path)
    rec = RecommendationResult(
        symbol="AAA",
        action="BUY",
        action_label="매수 후보",
        confidence=0.5,
        priority=1,
        reason="test",
        risk_notes=[],
        current_quantity=0,
        current_value=0.0,
        current_weight=0.0,
        target_weight=0.1,
        suggested_quantity=1,
        suggested_limit_price=100.0,
        estimated_order_value=100.0,
        source_signal_score=65.0,
        macro_context={},
        account_context={},
        blocked_reasons=[],
        allowed_for_plan=True,
    )
    cfg = RecommendationConfig(
        enable_quality_gates=True,
        min_final_score=60.0,
        use_validation_tuned_min_score=True,
        validation_threshold_summary_path=str(tmp_path / THRESHOLD_SUMMARY_FILENAME),
    )
    out = apply_per_symbol_quality_gates(
        rec,
        signal={"final_score": 65.0},
        day="2024-01-10",
        prices_by_day=_prices(),
        volumes_by_day={},
        liquidity_cfg=LiquidityConfig(),
        cost_model=CostModel(enabled=False),
        config=cfg,
        threshold_summary=load_threshold_summary(tmp_path),
    )
    assert not out.allowed_for_plan
    assert any("below_min_final_score" in b for b in out.blocked_reasons)
