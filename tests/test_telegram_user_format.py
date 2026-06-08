"""telegram_user_format — concise Telegram copy."""

from __future__ import annotations

from deepsignal.crypto_trading.crypto_recommendation_diagnostics import (
    CryptoBuyCandidateDiagnostic,
    CryptoRecommendationDiagnostics,
    CryptoSellCandidateDiagnostic,
)
from deepsignal.live_trading.telegram_user_format import (
    format_crypto_no_recommendation_telegram,
    simplify_telegram_hint,
)


def test_simplify_telegram_hint_strips_bps() -> None:
    raw = (
        "KRW-CHIP(KRW-CHIP): 스프레드 추정 48.4bps > 한도 45.0bps; "
        "수수료·슬리피지 반영 R:R 0.80 < 최소 0.92 (목표 2.00% / 손절 -1.50%)"
    )
    out = simplify_telegram_hint(raw)
    assert "bps" not in out
    assert "스프레드 과다" in out or "수익비" in out


def test_format_crypto_no_recommendation_brief() -> None:
    diag = CryptoRecommendationDiagnostics(
        generated_at="2026-01-01T00:00:00+09:00",
        take_profit_pct=2.0,
        stop_loss_pct=-1.5,
        take_profit_buffer_pct=0.05,
        stop_loss_buffer_pct=0.05,
        max_order_value=10_000.0,
        holdings_summary=[],
        buy_candidates=[
            CryptoBuyCandidateDiagnostic(
                market="KRW-BTC",
                display_name="비트코인",
                current_price=1.0,
                signed_change_rate=0.01,
                acc_trade_price_24h=1e9,
                score=50.0,
                rsi=50.0,
                rsi_pass=True,
                volume_ratio=1.0,
                volume_pass=True,
                atr_pct=1.0,
                atr_action="pass",
                quality_ok=True,
                size_multiplier=1.0,
                gate_passed=True,
            )
        ],
        sell_candidates=[
            CryptoSellCandidateDiagnostic(
                market="KRW-UNI",
                display_name="유니스왑",
                quantity=1.0,
                avg_buy_price=100.0,
                current_price=101.0,
                pnl_pct=1.95,
                take_profit_pct=2.0,
                stop_loss_pct=-1.5,
                take_profit_buffer_pct=0.05,
                stop_loss_buffer_pct=0.05,
                valuation_krw=10_000.0,
                min_order_krw_pass=True,
                sell_trigger="near_take_profit",
            )
        ],
        final_no_recommendation_reason="test",
        final_summary_bullets=[
            "점수·게이트 통과 27건 — 체결품질(R:R·스프레드)로 최종 탈락",
            "KRW-RVN(KRW-RVN): 수수료·슬리피지 반영 R:R 0.86 < 최소 0.92",
        ],
    )
    lines = format_crypto_no_recommendation_telegram(diag)
    body = "\n".join(lines)
    assert "현재 매수·매도 추천 없음" in body
    assert "보유 익절 근접" in body
    assert "매수 후보 1건" in body
    assert len(lines) <= 6
    assert "27건" not in body
