"""Crypto SELL trigger classification (take-profit / stop-loss buffers)."""

from __future__ import annotations

from typing import Literal

from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto


def get_dynamic_tp_sl_for_market(
    market: str,
) -> tuple[float, float] | None:
    """KRW-BTC 등 Upbit 심볼에 대한 동적 TP/SL 조회.

    Returns:
        (take_profit_pct, stop_loss_pct) in % — e.g. (3.2, -1.8)
        또는 실패/데이터 없으면 None
    """
    try:
        from deepsignal.crypto_trading.risk.sizing import compute_crypto_dynamic_tpsl
        result = compute_crypto_dynamic_tpsl(market)
        if result is None:
            return None
        tp_d, sl_d, _ = result
        from deepsignal.crypto_trading.risk.sizing import _clamp, _eff_sl_pct_max
        tp = _clamp(tp_d, float(_CRYPTO.tp_pct_min), float(_CRYPTO.tp_pct_max))
        sl = _clamp(sl_d, float(_CRYPTO.sl_pct_min), _eff_sl_pct_max())
        return tp, sl
    except Exception:
        return None


def classify_crypto_sell_trigger_dynamic(
    pnl_pct: float,
    market: str,
    *,
    fallback_take_profit_pct: float = _CRYPTO.take_profit_pct,
    fallback_stop_loss_pct: float = _CRYPTO.stop_loss_pct,
    take_profit_buffer_pct: float = _CRYPTO.take_profit_buffer_pct,
    stop_loss_buffer_pct: float = _CRYPTO.stop_loss_buffer_pct,
) -> SellTrigger | None:
    """종목별 동적 TP/SL 기반 SELL 트리거 분류.

    동적 TP/SL 계산에 실패하면 fallback 고정값으로 평가한다.
    """
    dyn = get_dynamic_tp_sl_for_market(market)
    if dyn is not None:
        tp, sl = dyn
    else:
        tp = float(fallback_take_profit_pct)
        sl = float(fallback_stop_loss_pct)
    return classify_crypto_sell_trigger(
        pnl_pct,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        take_profit_buffer_pct=take_profit_buffer_pct,
        stop_loss_buffer_pct=stop_loss_buffer_pct,
    )

SellTrigger = Literal[
    "take_profit",
    "near_take_profit",
    "stop_loss",
    "near_stop_loss",
    "overweight_reduce",
]

_TRIGGER_PRIORITY: dict[str, int] = {
    "take_profit": 4,
    "near_take_profit": 3,
    "stop_loss": 2,
    "near_stop_loss": 1,
    "overweight_reduce": 0,
}


def sell_trigger_priority(trigger: str) -> int:
    return _TRIGGER_PRIORITY.get(trigger, 0)


def classify_crypto_sell_trigger(
    pnl_pct: float,
    *,
    take_profit_pct: float = _CRYPTO.take_profit_pct,
    stop_loss_pct: float = _CRYPTO.stop_loss_pct,
    take_profit_buffer_pct: float = _CRYPTO.take_profit_buffer_pct,
    stop_loss_buffer_pct: float = _CRYPTO.stop_loss_buffer_pct,
) -> SellTrigger | None:
    """Classify holding PnL into SELL trigger (or None = hold)."""
    pnl = float(pnl_pct)
    tp = float(take_profit_pct)
    sl = float(stop_loss_pct)
    tp_buf = float(take_profit_buffer_pct)
    sl_buf = float(stop_loss_buffer_pct)

    if pnl >= tp:
        return "take_profit"
    if pnl >= tp - tp_buf:
        return "near_take_profit"
    if pnl <= sl:
        return "stop_loss"
    if pnl <= sl + sl_buf:
        return "near_stop_loss"
    entry_review = float(_CRYPTO.entry_drawdown_review_pct)
    if pnl <= entry_review:
        return "near_stop_loss"
    warn_loss = float(_CRYPTO.warn_loss_pct)
    if pnl <= warn_loss:
        return "near_stop_loss"
    return None
