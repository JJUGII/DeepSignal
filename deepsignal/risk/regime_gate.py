"""레짐 마스터게이트 (DM6) — risk-off 확정 시 신규 롱 매수 전면 차단.

근거: 엣지 리서치·DM1 백테스트 결론 = 현 상품군에서 하락으로 '수익'낼 검증된 엣지는
없고(타이밍 숏 기각), 검증된 엣지는 하락을 **회피**하는 것뿐(S&P500 200일선 롱/현금
타이밍, 98년 Sharpe 0.63 vs 0.42, MDD 절반). 그 회피력을 3자산 공통 마스터스위치로
연결한다 — risk-off(지수 200일선 하회) 확정 시 신규 롱을 막고, **청산은 계속**한다.

설계(킬스위치 TRADING_HALT·EDGE_GATE와 동일 패턴, AND 결합):
- 신호원: S&P500 200일선(글로벌 위험 프록시). regime_trend.compute_trend_signal 재사용.
- in-market(종가>SMA200) → 통과. out(하회) → 신규 롱 차단.
- 데이터 없음/오류 → **fail-open**(데이터 공백으로 매매를 벽돌화하지 않음). 차단은 명확한
  risk-off일 때만.
- 기본 ON. env DEEPSIGNAL_ENFORCE_REGIME_GATE=false 로 무시 가능.

⚠️ S&P500 신호를 코인·국내주식에도 글로벌 위험 프록시로 적용(상관 높음). 자산별 독립
검증은 아님 — KOSPI/코인 자체 레짐이 필요하면 추후 분리.
"""

from __future__ import annotations

import os
from pathlib import Path


def regime_gate_enforced() -> bool:
    return os.environ.get("DEEPSIGNAL_ENFORCE_REGIME_GATE", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def regime_allows_long_buy(
    output_dir: str | Path | None = None,
    *,
    db_path: str | None = None,
    asset: str = "",
) -> tuple[bool, str]:
    """레짐상 신규 롱 매수가 허용되는지. (허용여부, 사유).

    output_dir는 인터페이스 일관성용(미사용). 차단은 risk-off 확정 시에만.
    """
    _ = output_dir
    if not regime_gate_enforced():
        return (True, "")
    try:
        from deepsignal.live_trading.regime_trend import compute_trend_signal

        sig = compute_trend_signal(db_path)
    except Exception as exc:  # noqa: BLE001 — 신호 계산 실패는 fail-open
        return (True, f"레짐 신호 계산 실패({type(exc).__name__}) — 게이트 통과")

    if sig.source in ("error", "insufficient"):
        return (True, f"레짐 신호 없음({sig.reason}) — 게이트 통과")
    if sig.in_market:
        return (True, "")
    tag = f"[{asset}] " if asset else ""
    return (False, f"{tag}레짐 risk-off: {sig.reason} — 신규 롱 차단(청산 계속)")
