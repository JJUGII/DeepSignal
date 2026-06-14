"""Dynamic crypto order size, daily limits, and fund-manager-aligned TP/SL."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import (
    CryptoTunedThresholds,
    load_active_crypto_thresholds,
)
from deepsignal.crypto_trading.crypto_quality import compute_atr_pct_from_candles
from deepsignal.crypto_trading.crypto_execution_quality import effective_min_order_krw
from deepsignal.crypto_trading.upbit_broker import MIN_ORDER_KRW, UpbitBroker
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

logger = logging.getLogger(__name__)

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto
ACTIVE_SIZING_JSON = "CRYPTO_ACTIVE_SIZING.json"
ATR_PROXY_MARKET = "KRW-BTC"

# 프로젝트 루트: crypto_trading/risk/sizing.py → parents[3] = project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Upbit KRW-* → Binance USDT 심볼 매핑 (동적 TP/SL 바 데이터 조회용)
_KRW_TO_BINANCE: dict[str, str] = {
    "KRW-BTC":  "BTCUSDT",
    "KRW-ETH":  "ETHUSDT",
    "KRW-XRP":  "XRPUSDT",
    "KRW-SOL":  "SOLUSDT",
    "KRW-DOGE": "DOGEUSDT",
    "KRW-ADA":  "ADAUSDT",
    "KRW-AVAX": "AVAXUSDT",
    "KRW-LINK": "LINKUSDT",
    "KRW-DOT":  "DOTUSDT",
    "KRW-MATIC":"MATICUSDT",
    "KRW-TRX":  "TRXUSDT",
    "KRW-SHIB": "SHIBUSDT",
    "KRW-LTC":  "LTCUSDT",
    "KRW-ATOM": "ATOMUSDT",
    "KRW-SUI":  "SUIUSDT",
}


def _krw_to_binance_symbol(market: str) -> str:
    """KRW-BTC → BTCUSDT (없으면 BTC 부분 + USDT)."""
    if market in _KRW_TO_BINANCE:
        return _KRW_TO_BINANCE[market]
    # 패턴 변환 시도: KRW-XXX → XXXUSDT
    if market.upper().startswith("KRW-"):
        return market[4:].upper() + "USDT"
    return market.upper()


def compute_crypto_dynamic_tpsl(
    market: str = ATR_PROXY_MARKET,
) -> tuple[float, float, str] | None:
    """코인 심볼(KRW-BTC 등)에 대한 동적 TP/SL 계산.

    Returns:
        (tp_pct, sl_pct, source_label) in % (e.g. 3.2, -1.8, "dynamic_A_SIDEWAYS")
        또는 실패 시 None
    """
    try:
        from deepsignal.risk.dynamic_tpsl import compute_dynamic_tpsl, load_bars_for_symbol
        binance_sym = _krw_to_binance_symbol(market)
        bars, tf_min = load_bars_for_symbol(binance_sym, "crypto", _PROJECT_ROOT)
        result = compute_dynamic_tpsl(
            binance_sym, "crypto",
            bars or None,
            timeframe_min=tf_min,
        )
        tp_pct = result.tp_pct * 100   # 소수 → %
        sl_pct = result.sl_pct * 100   # 소수 → %
        source = f"dynamic_{result.grade.value}_{result.market_state.value}"
        logger.debug(
            "[CryptoDynTpSl] %s(%s) ATR=%.2f%% %s → TP=+%.2f%% SL=%.2f%%",
            market, binance_sym, result.atr_pct, result.market_state.value, tp_pct, sl_pct,
        )
        return tp_pct, sl_pct, source
    except Exception as exc:
        logger.debug("[CryptoDynTpSl] %s 계산 실패 (기본값 사용): %s", market, exc)
        return None


@dataclass
class CryptoRuntimeSizing:
    available_krw: float
    total_portfolio_krw: float
    max_order_krw: float
    max_orders_per_day: int
    take_profit_pct: float
    stop_loss_pct: float
    take_profit_buffer_pct: float
    stop_loss_buffer_pct: float
    min_volume_ratio: float
    macro_regime: str
    score_factor: float
    size_multiplier_hint: float = 1.0
    atr_pct: float | None = None
    tp_source: str = "fund_manager"
    order_source: str = "dynamic"
    notes: list[str] = field(default_factory=list)
    # 동적 TP/SL 메타 (대시보드·텔레그램 표시용)
    dynamic_grade: str | None = None
    dynamic_market_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _eff_sl_pct_min() -> float:
    """손절 하한(가장 깊은 손절, 음수 중 가장 작은 값). 공격성 다이얼의
    CRYPTO_SL_PCT_MIN 이 있으면 손절 깊이를 줄인다(실측 평균 -2.6% 출혈 교정).
    '덜 깊게'(0 쪽으로)만 이동 — 더 깊어지는 방향은 무시."""
    base = float(_CRYPTO.sl_pct_min)
    import os as _o
    ov = _o.environ.get("CRYPTO_SL_PCT_MIN", "").strip()
    if ov:
        try:
            return max(base, float(ov))
        except ValueError:
            pass
    return base


def _eff_sl_pct_max() -> float:
    """손절 상한(0에 가장 가까운 = 가장 타이트한 손절). 공격성 다이얼의
    CRYPTO_SL_PCT_MAX 가 있으면 그만큼 손절을 넓힌다(스프레드 churn 방지)."""
    base = float(_CRYPTO.sl_pct_max)
    import os as _o
    ov = _o.environ.get("CRYPTO_SL_PCT_MAX", "").strip()
    if ov:
        try:
            return min(base, float(ov))  # 더 음수(넓은 손절)로만 이동
        except ValueError:
            pass
    return base


def portfolio_totals(broker: UpbitBroker) -> tuple[float, float, float]:
    """total_krw, available_krw, holdings_valuation_krw."""
    try:
        available = float(broker.get_krw_available())
    except Exception:
        available = 0.0
    hold_val = 0.0
    try:
        hold_val = sum(float(h.valuation_krw or 0) for h in broker.get_crypto_holdings())
    except Exception:
        pass
    return available + hold_val, available, hold_val


def _score_factor(final_score: float | None) -> float:
    ref = float(_CRYPTO.score_reference)
    floor = float(_CRYPTO.score_factor_floor)
    if final_score is None or not math.isfinite(float(final_score)):
        return max(floor, 0.65)
    fs = float(final_score)
    if ref <= 0:
        return 1.0
    return _clamp(fs / ref, floor, 1.25)


def merge_tp_sl(
    tuned: CryptoTunedThresholds | None,
    atr_pct: float | None,
    *,
    min_sell_samples: int = 3,
) -> tuple[float, float, float, float, float, str]:
    """Crypto default TP/SL; outcomes and ATR adjust within configured band."""
    tp = float(_CRYPTO.take_profit_pct)
    sl = float(_CRYPTO.stop_loss_pct)
    tp_buf = float(_CRYPTO.take_profit_buffer_pct)
    sl_buf = float(_CRYPTO.stop_loss_buffer_pct)
    mvr = float(_CRYPTO.min_volume_ratio)
    source = "crypto_default"

    if tuned is not None:
        tp_buf = float(tuned.take_profit_buffer_pct)
        sl_buf = float(tuned.stop_loss_buffer_pct)
        mvr_cap = float(getattr(_CRYPTO, "outcome_tune_max_volume_ratio", 0.45))
        mvr = min(float(tuned.min_volume_ratio), mvr_cap)
        scalping = bool(getattr(_CRYPTO, "scalping_mode", True)) and not bool(_CRYPTO.prefer_fund_manager_tp_sl)
        apply_tuned_tp_sl = bool(getattr(_CRYPTO, "outcome_tune_apply_tp_sl", False))
        if scalping:
            source = "scalping_default"
            if apply_tuned_tp_sl and int(tuned.sample_sell_closed) >= min_sell_samples:
                tp = _clamp(
                    float(tuned.take_profit_pct),
                    float(_CRYPTO.tp_pct_min),
                    float(_CRYPTO.tp_pct_max),
                )
                sl = _clamp(
                    float(tuned.stop_loss_pct),
                    _eff_sl_pct_min(),
                    _eff_sl_pct_max(),
                )
                source = "outcomes"
        elif apply_tuned_tp_sl and int(tuned.sample_sell_closed) >= min_sell_samples:
            tp = _clamp(
                float(tuned.take_profit_pct),
                float(_CRYPTO.tp_pct_min),
                float(_CRYPTO.tp_pct_max),
            )
            sl = _clamp(
                float(tuned.stop_loss_pct),
                _eff_sl_pct_min(),
                _eff_sl_pct_max(),
            )
            source = "outcomes"
        elif not bool(_CRYPTO.prefer_fund_manager_tp_sl) and atr_pct and atr_pct > 0:
            tp_atr, sl_atr, _ = _tp_sl_from_atr(atr_pct)
            tp, sl = tp_atr, sl_atr
            source = "atr"

    # ── 동적 TP/SL 오버라이드 (ATR × 등급배수 × 시장상태배수) ──────────────
    # scalping_mode 여부와 무관하게 BTCUSDT 바 데이터가 있으면 dynamic_tpsl 우선 적용
    dynamic = compute_crypto_dynamic_tpsl(ATR_PROXY_MARKET)
    if dynamic is not None:
        tp_d, sl_d, src_d = dynamic
        # 기존 밴드 내로 클램프 (tp: 1%~4%, sl: -3%~-0.8%)
        tp = _clamp(tp_d, float(_CRYPTO.tp_pct_min), float(_CRYPTO.tp_pct_max))
        sl = _clamp(sl_d, _eff_sl_pct_min(), _eff_sl_pct_max())
        source = src_d

    # ── 김치프리미엄 SL 조정 ────────────────────────────────────────────────
    # 프리미엄이 높을수록 입매가가 '공정가치' 대비 고평가 상태.
    # → 프리미엄이 mean-revert하면 손실 확대 가능 → SL을 타이트하게 조정.
    # 조정식: sl *= (1 + premium_pct / 100)  (SL 절댓값 축소, 더 빨리 손절)
    # 예) premium 5% → sl × 1.05 → -1.5% SL이 -1.43%로 타이트해짐
    # 단, 밴드 내 클램프 유지.
    try:
        from deepsignal.crypto_trading.kimchi_premium import get_premium, LEVEL_LOW

        kp = get_premium("BTC")  # BTC를 시장 대표 프록시로 사용
        if kp is not None and kp.premium_pct > LEVEL_LOW:
            factor = 1.0 + kp.premium_pct / 100.0
            sl_adj = _clamp(sl * factor, _eff_sl_pct_min(), _eff_sl_pct_max())
            if sl_adj != sl:
                _LOG.debug(
                    "[kimchi_tpsl] 김치프리미엄 %.1f%% → SL %.3f→%.3f",
                    kp.premium_pct, sl, sl_adj,
                )
                sl = sl_adj
                source = source + "+kimchi"
    except Exception:
        pass

    return tp, sl, tp_buf, sl_buf, mvr, source


def tp_sl_from_atr(atr_pct: float) -> tuple[float, float, str]:
    """Public ATR-based TP/SL (clamped to fund-manager bands)."""
    return _tp_sl_from_atr(atr_pct)


def _tp_sl_from_atr(atr_pct: float) -> tuple[float, float, str]:
    tp = _clamp(
        float(atr_pct) * float(_CRYPTO.atr_tp_multiplier),
        float(_CRYPTO.tp_pct_min),
        float(_CRYPTO.tp_pct_max),
    )
    sl = -_clamp(
        float(atr_pct) * float(_CRYPTO.atr_sl_multiplier),
        abs(_eff_sl_pct_min()),
        abs(_eff_sl_pct_max()),
    )
    return round(tp, 3), round(sl, 3), "atr"


def compute_max_order_krw(
    available_krw: float,
    *,
    total_portfolio_krw: float | None = None,
    final_score: float | None = None,
    size_multiplier: float = 1.0,
    hard_cap_krw: float = 0.0,
) -> tuple[float, float, list[str]]:
    notes: list[str] = []
    avail = max(0.0, float(available_krw))
    total = max(avail, float(total_portfolio_krw or avail))
    policy_min = effective_min_order_krw()
    if avail < policy_min:
        notes.append(f"가용 KRW {avail:,.0f} < 최소주문 {policy_min:,.0f}")
        return 0.0, 0.0, notes

    sf = _score_factor(final_score)
    mult = _clamp(float(size_multiplier), 0.25, 1.0)
    pct = float(_CRYPTO.order_pct_of_available) * sf * mult
    raw = avail * pct
    cap_pct = avail * float(_CRYPTO.max_order_pct_of_available)
    cap_single = total * float(_CRYPTO.max_single_position_pct)
    cap_order_pct = total * float(_CRYPTO.max_single_order_pct_of_total)
    dyn_floor = max(policy_min, float(_CRYPTO.dynamic_cap_floor_krw))
    dyn_target = max(dyn_floor, total * float(_CRYPTO.dynamic_cap_target_pct_total) * sf * mult)
    dyn_ceil = max(dyn_floor, total * float(_CRYPTO.dynamic_cap_ceiling_pct_total))
    dyn_cap = _clamp(dyn_target, dyn_floor, dyn_ceil)
    static_cap = float(_CRYPTO.max_order_cap_krw)
    effective_cap = min(dyn_cap, static_cap) if static_cap > 0 else dyn_cap
    order_krw = min(raw, cap_pct, cap_single, cap_order_pct, effective_cap)
    if hard_cap_krw > 0:
        order_krw = min(order_krw, float(hard_cap_krw))
        notes.append(f"CLI 상한 {hard_cap_krw:,.0f}원 적용")
    order_krw = max(policy_min, math.floor(order_krw))
    if order_krw > avail:
        order_krw = max(policy_min, math.floor(avail * 0.95))
        notes.append("가용 잔고 대비 주문액 조정")
    notes.append(
        f"주문액 {order_krw:,.0f}원 (가용×{pct*100:.1f}%, 단일종목≤{cap_single:,.0f}=총자산×{float(_CRYPTO.max_single_position_pct)*100:.0f}%, score×{sf:.2f})"
    )
    notes.append(
        f"유동 상한 {effective_cap:,.0f}원 (target {dyn_target:,.0f}, floor {dyn_floor:,.0f}, ceil {dyn_ceil:,.0f})"
    )
    return order_krw, sf, notes


def compute_max_orders_per_day(
    available_krw: float,
    *,
    holdings_count: int = 0,
    macro_regime: str = "neutral",
    block_buy_risk_off: bool | None = None,
) -> tuple[int, list[str]]:
    notes: list[str] = []
    regime = str(macro_regime or "neutral").lower()
    block = bool(_CRYPTO.block_buy_on_risk_off) if block_buy_risk_off is None else block_buy_risk_off
    if block and regime in ("risk_off", "risk-off", "bear", "defensive"):
        notes.append(f"macro {regime} — BUY 일일 횟수 0 (SELL만)")
        return 0, notes

    avail = max(0.0, float(available_krw))
    slot = max(float(_CRYPTO.min_krw_per_order_slot), effective_min_order_krw())
    afford = max(0, int(avail // slot))
    hi = int(_CRYPTO.max_orders_per_day_max)
    lo = int(_CRYPTO.max_orders_per_day_min)
    n = _clamp(float(afford), float(lo), float(hi))
    if holdings_count >= int(_CRYPTO.max_buy_scan_markets):
        n = max(lo, n - 1)
        notes.append(f"보유 {holdings_count}종 — 일일 BUY 횟수 완화")
    n = int(n)
    notes.append(f"일일 BUY 최대 {n}회 (펀드형 슬롯 {slot:,.0f}원)")
    return n, notes


def _fetch_atr_proxy(broker: UpbitBroker, market: str = ATR_PROXY_MARKET) -> float | None:
    try:
        candles = broker.get_daily_candles(market, count=30)
        return compute_atr_pct_from_candles(candles)
    except Exception:
        return None


def resolve_crypto_runtime_sizing(
    broker: UpbitBroker,
    *,
    output_dir: str | Path,
    macro_regime: str = "neutral",
    final_score: float | None = None,
    size_multiplier: float = 1.0,
    hard_cap_order_krw: float = 0.0,
    hard_cap_orders_per_day: int = 0,
) -> CryptoRuntimeSizing:
    notes: list[str] = []
    total, available, hold_val = portfolio_totals(broker)
    if hold_val > 0:
        notes.append(f"총자산 {total:,.0f}원 (코인평가 {hold_val:,.0f})")

    tuned = load_active_crypto_thresholds(output_dir)
    atr_pct = _fetch_atr_proxy(broker)
    tp, sl, tp_buf, sl_buf, mvr, tp_src = merge_tp_sl(tuned, atr_pct)

    # 동적 TP/SL 메타 추출 (grade, market_state — 대시보드 표시용)
    dyn_grade: str | None = None
    dyn_market_state: str | None = None
    try:
        from deepsignal.risk.dynamic_tpsl import compute_dynamic_tpsl, load_bars_for_symbol
        _btc_sym = _krw_to_binance_symbol(ATR_PROXY_MARKET)
        _bars, _tf = load_bars_for_symbol(_btc_sym, "crypto", _PROJECT_ROOT)
        if _bars:
            _r = compute_dynamic_tpsl(_btc_sym, "crypto", _bars, timeframe_min=_tf)
            dyn_grade = _r.grade.value
            dyn_market_state = _r.market_state.value
    except Exception:
        pass

    order_krw, sf, order_notes = compute_max_order_krw(
        available,
        total_portfolio_krw=total,
        final_score=final_score,
        size_multiplier=size_multiplier,
        hard_cap_krw=hard_cap_order_krw,
    )
    notes.extend(order_notes)

    max_day, day_notes = compute_max_orders_per_day(
        available,
        holdings_count=len(broker.get_crypto_holdings()),
        macro_regime=macro_regime,
    )
    notes.extend(day_notes)
    if hard_cap_orders_per_day > 0:
        max_day = min(max_day, int(hard_cap_orders_per_day)) if max_day > 0 else 0
        notes.append(f"CLI 일일 횟수 상한 {hard_cap_orders_per_day}")

    return CryptoRuntimeSizing(
        available_krw=available,
        total_portfolio_krw=total,
        max_order_krw=order_krw,
        max_orders_per_day=max_day,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        take_profit_buffer_pct=tp_buf,
        stop_loss_buffer_pct=sl_buf,
        min_volume_ratio=mvr,
        macro_regime=str(macro_regime),
        score_factor=sf,
        size_multiplier_hint=float(size_multiplier),
        atr_pct=atr_pct,
        tp_source=tp_src,
        order_source="dynamic" if hard_cap_order_krw <= 0 else "dynamic_capped",
        notes=notes,
        dynamic_grade=dyn_grade,
        dynamic_market_state=dyn_market_state,
    )


def apply_runtime_sizing_to_runner(cfg: Any, sizing: CryptoRuntimeSizing) -> None:
    if float(sizing.max_order_krw) > 0:
        cfg.max_order_value = float(sizing.max_order_krw)
    cfg.max_orders_per_day = int(sizing.max_orders_per_day)
    cfg.take_profit_pct = float(sizing.take_profit_pct)
    cfg.stop_loss_pct = float(sizing.stop_loss_pct)
    cfg.take_profit_buffer_pct = float(sizing.take_profit_buffer_pct)
    cfg.stop_loss_buffer_pct = float(sizing.stop_loss_buffer_pct)
    cfg.min_volume_ratio = float(sizing.min_volume_ratio)


def save_active_sizing_snapshot(output_dir: str | Path, sizing: CryptoRuntimeSizing) -> Path:
    path = Path(output_dir) / ACTIVE_SIZING_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sizing.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
