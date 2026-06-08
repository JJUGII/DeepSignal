"""K-GSQS 기반 동적 포지션 사이징 — 국내/해외 주식.

크립토의 CryptoRuntimeSizing과 동일 로직이지만:
  - 계좌 잔고: KIS REST API에서 조회
  - 신호 점수: K-GSQS (0~100)
  - MIN_ORDER: 1주 (min_shares=1)
  - TP/SL: dynamic_tpsl 모듈 (kis_stock / kis_overseas 에셋 클래스)

NOTIFY≥72, AUTO≥82, STRONG≥88 기준 점수 팩터:
  STRONG → 1.5x
  AUTO   → 1.25x
  NOTIFY → 1.0x
  미달    → 0.0 (진입 불가)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# 기본 파라미터
NOTIFY_THRESHOLD  = 72.0
AUTO_THRESHOLD    = 82.0
STRONG_THRESHOLD  = 88.0

# 기본 사이징 비율 (포트폴리오 대비)
DEFAULT_BASE_ALLOC_PCT   = 0.10   # 기본 진입 자금 비율 (10%)
DEFAULT_MAX_ALLOC_PCT    = 0.25   # 최대 진입 자금 비율 (25%)
DEFAULT_MAX_POSITIONS    = 5      # 최대 동시 포지션 수

# 점수 → 비중 팩터
SCORE_FACTORS: list[tuple[float, float, str]] = [
    (STRONG_THRESHOLD, 1.5, "STRONG"),
    (AUTO_THRESHOLD,   1.25, "AUTO"),
    (NOTIFY_THRESHOLD, 1.0, "NOTIFY"),
]


def _score_factor(score: float) -> tuple[float, str]:
    for threshold, factor, label in SCORE_FACTORS:
        if score >= threshold:
            return factor, label
    return 0.0, "BELOW"


@dataclass
class KstockPositionRecommendation:
    """단일 종목 포지션 사이징 권고."""
    symbol: str
    name: str
    score: float
    action: str
    score_label: str         # STRONG / AUTO / NOTIFY / BELOW
    score_factor: float      # 비중 팩터 (0 ~ 1.5)
    recommended_krw: float   # 권고 주문금액 (KRW)
    recommended_shares: int  # 권고 주수 (현재가 기준)
    current_price: float     # 현재가 (KRW)
    tp_pct: float | None     # 동적 익절 비율 (%)
    sl_pct: float | None     # 동적 손절 비율 (%)
    atr_pct: float | None    # ATR (%)
    market_state: str | None # TRENDING / SIDEWAYS / VOLATILE
    blocked: bool            # 진입 차단 여부


@dataclass
class KstockSizingResult:
    """전체 사이징 결과."""
    available_cash: float                           # 가용 현금
    total_equity: float                             # 총 평가금액
    base_alloc_krw: float                           # 기본 진입 한도 (1건)
    max_alloc_krw: float                            # 최대 진입 한도 (1건)
    max_positions: int                              # 최대 포지션 수
    recommendations: list[KstockPositionRecommendation] = field(default_factory=list)
    kis_env: str = "paper"
    asset_label: str = "주식"
    error: str | None = None


def compute_kstock_sizing(
    *,
    available_cash: float,
    total_equity: float,
    scores: list[dict[str, Any]],          # K-GSQS 스트림의 scores 목록
    asset_class: str = "kis_stock",        # "kis_stock" or "kis_overseas"
    asset_label: str = "국내주식",
    kis_env: str = "paper",
    base_alloc_pct: float = DEFAULT_BASE_ALLOC_PCT,
    max_alloc_pct: float = DEFAULT_MAX_ALLOC_PCT,
    max_positions: int = DEFAULT_MAX_POSITIONS,
    project_root: Path | None = None,
) -> KstockSizingResult:
    """K-GSQS 점수 목록에서 동적 포지션 사이징을 계산합니다."""
    portfolio_base = max(available_cash, total_equity, 1.0)
    base_alloc = min(
        portfolio_base * base_alloc_pct,
        available_cash,  # 가용 현금 초과 불가
    )
    max_alloc = min(
        portfolio_base * max_alloc_pct,
        available_cash,
    )

    result = KstockSizingResult(
        available_cash=available_cash,
        total_equity=total_equity,
        base_alloc_krw=round(base_alloc, 0),
        max_alloc_krw=round(max_alloc, 0),
        max_positions=max_positions,
        kis_env=kis_env,
        asset_label=asset_label,
    )

    for s in scores:
        score = float(s.get("total_score") or 0)
        action = str(s.get("action") or "HOLD")
        factor, label = _score_factor(score)
        if factor == 0.0 or action == "HOLD":
            continue  # 임계값 미달

        symbol = str(s.get("symbol") or "").split(":")[-1]  # "NASD:NVDA" → "NVDA"
        full_symbol = str(s.get("symbol") or "")
        name = str(s.get("name") or symbol)
        price = float(s.get("price") or 0)

        recommended_krw = round(base_alloc * factor, 0)
        recommended_krw = min(recommended_krw, max_alloc)

        # 현재가 기준 주수 계산 (국장 1주 단위, 해외 소수점 불가)
        shares = 0
        if price > 0 and recommended_krw > 0:
            shares = max(1, math.floor(recommended_krw / price))
            recommended_krw = round(shares * price, 0)

        # 동적 TP/SL
        tp_pct = sl_pct = atr_pct = None
        market_state = None
        blocked = False
        try:
            if project_root:
                from deepsignal.risk.dynamic_tpsl import compute_dynamic_tpsl, load_bars_for_symbol
                bars, tf_min = load_bars_for_symbol(full_symbol or symbol, asset_class, project_root)
                tpsl = compute_dynamic_tpsl(full_symbol or symbol, asset_class, bars or None, timeframe_min=tf_min)
                tp_pct = round(tpsl.tp_pct * 100, 2)
                sl_pct = round(tpsl.sl_pct * 100, 2)
                atr_pct = round(tpsl.atr_pct, 2)
                market_state = tpsl.market_state.value
                blocked = tpsl.blocked
        except Exception:
            pass

        result.recommendations.append(KstockPositionRecommendation(
            symbol=symbol,
            name=name,
            score=round(score, 1),
            action=action,
            score_label=label,
            score_factor=factor,
            recommended_krw=recommended_krw,
            recommended_shares=shares,
            current_price=price,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            atr_pct=atr_pct,
            market_state=market_state,
            blocked=blocked,
        ))

    # 점수 내림차순 정렬
    result.recommendations.sort(key=lambda r: r.score, reverse=True)

    return result


def sizing_to_dict(r: KstockSizingResult) -> dict[str, Any]:
    """KstockSizingResult → JSON 직렬화 가능 dict."""
    d = asdict(r)
    return d
