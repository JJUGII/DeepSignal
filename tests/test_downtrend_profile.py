"""Phase1 하락장 프로파일 + L10 paper 가드 테스트."""

from __future__ import annotations

from deepsignal.crypto_trading.downtrend.profile import (
    DOWNTREND_SCALP,
    apply_downtrend_profile,
    l10_is_paper_only,
)


def test_profile_conservative_values():
    p = DOWNTREND_SCALP
    assert p.max_spread_pct <= 0.18           # 좁은 스프레드
    assert p.min_bid_ask_ratio >= 1.2          # 강한 매수벽
    assert p.aggressive_fill is False          # 추격 금지
    assert p.position_mult <= 0.4              # 작은 베팅
    assert p.min_score >= 70.0                 # 높은 문턱
    assert p.tp_pct > 0 and p.sl_pct < 0


def test_apply_sets_env():
    env: dict[str, str] = {}
    applied = apply_downtrend_profile(env)
    assert env["CRYPTO_MAX_SPREAD_PCT"] == "0.15"
    assert env["CRYPTO_AGGRESSIVE_FILL"] == "false"
    assert env["CRYPTO_MIN_FINAL_SCORE"] == "70.0"
    assert env["CRYPTO_MAX_HOLD_MINUTES"] == "10.0"
    assert applied  # 비어있지 않음


def test_l10_paper_only():
    assert l10_is_paper_only({"DEEPSIGNAL_AGGRESSION": "10"}) is True
    assert l10_is_paper_only({"DEEPSIGNAL_AGGRESSION": "9"}) is True
    assert l10_is_paper_only({"DEEPSIGNAL_AGGRESSION": "4"}) is False
    # 명시적 override면 실거래 허용
    assert l10_is_paper_only({"DEEPSIGNAL_AGGRESSION": "10", "DEEPSIGNAL_ALLOW_LIVE_L9_PLUS": "true"}) is False
    # 잘못된 값은 보수적으로 paper 아님(저단계 취급)
    assert l10_is_paper_only({"DEEPSIGNAL_AGGRESSION": "x"}) is False
