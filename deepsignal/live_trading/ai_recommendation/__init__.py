"""AI live trade recommendation package.

Recommendation only. It never calls live-approve, --execute, or KIS order POST.
"""

from deepsignal.live_trading.ai_recommendation.recommendation_engine import run_ai_live_recommendation
from deepsignal.live_trading.ai_recommendation.recommendation_model import (
    AccountContext,
    RecommendationConfig,
    RecommendationResult,
    RecommendationRunResult,
)

__all__ = [
    "AccountContext",
    "RecommendationConfig",
    "RecommendationResult",
    "RecommendationRunResult",
    "run_ai_live_recommendation",
]
