"""P0–P2 guards: rebuy cooldown, hourly/daily BUY caps, post-SELL reentry, min hold."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deepsignal.live_trading.time_utils import now_kst
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto


@dataclass(frozen=True)
class OvertradingGuardConfig:
    rebuy_cooldown_minutes: int = _CRYPTO.rebuy_cooldown_minutes
    max_buy_per_market_per_hour: int = _CRYPTO.max_buy_per_market_per_hour
    post_sell_reentry_cooldown_minutes: int = _CRYPTO.post_sell_reentry_cooldown_minutes
    max_buy_krw_per_market_per_day_pct: float = _CRYPTO.max_buy_krw_per_market_per_day_pct
    max_add_on_buys_per_market_per_day: int = _CRYPTO.max_add_on_buys_per_market_per_day
    min_hold_minutes_before_sell: int = _CRYPTO.min_hold_minutes_before_sell
    near_take_profit_min_pnl_pct: float = _CRYPTO.near_take_profit_min_pnl_pct


def _now_ts() -> float:
    return float(now_kst().timestamp())


def _market_key(market: str) -> str:
    return str(market or "").strip().upper()


def _dict_field(state: dict[str, Any], key: str) -> dict[str, Any]:
    raw = state.get(key)
    return dict(raw) if isinstance(raw, dict) else {}


def prune_state_for_new_day(state: dict[str, Any], daily_key: str) -> None:
    if state.get("daily_key") == daily_key:
        return
    state["daily_key"] = daily_key
    state["orders_today"] = 0
    state["buy_krw_today"] = 0.0
    state["buy_markets_today"] = []
    state["buy_krw_by_market_today"] = {}
    state["buy_count_by_market_today"] = {}


def excluded_markets_for_buy(state: dict[str, Any], cfg: OvertradingGuardConfig | None = None) -> tuple[str, ...]:
    """Markets blocked by rebuy / post-sell cooldown (for scan exclusion)."""
    g = cfg or OvertradingGuardConfig()
    now = _now_ts()
    out: set[str] = set()

    last_buy = _dict_field(state, "last_buy_by_market")
    for market, ts in last_buy.items():
        try:
            if (now - float(ts)) / 60.0 < float(g.rebuy_cooldown_minutes):
                out.add(_market_key(market))
        except (TypeError, ValueError):
            continue

    last_sell = _dict_field(state, "last_sell_by_market")
    for market, ts in last_sell.items():
        try:
            if (now - float(ts)) / 60.0 < float(g.post_sell_reentry_cooldown_minutes):
                out.add(_market_key(market))
        except (TypeError, ValueError):
            continue

    return tuple(sorted(out))


def _buy_events_last_hour(state: dict[str, Any], market: str) -> list[float]:
    key = _market_key(market)
    raw = _dict_field(state, "buy_timestamps_by_market")
    events = raw.get(key) or raw.get(market) or []
    if not isinstance(events, list):
        return []
    cutoff = _now_ts() - 3600.0
    return [float(t) for t in events if float(t) >= cutoff]


def check_buy_allowed(
    state: dict[str, Any],
    *,
    market: str,
    order_krw: float,
    total_portfolio_krw: float,
    cfg: OvertradingGuardConfig | None = None,
) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    g = cfg or OvertradingGuardConfig()
    m = _market_key(market)
    now = _now_ts()
    order = max(0.0, float(order_krw))
    total = max(0.0, float(total_portfolio_krw))

    if m in excluded_markets_for_buy(state, g):
        last_sell = _dict_field(state, "last_sell_by_market").get(m)
        last_buy = _dict_field(state, "last_buy_by_market").get(m)
        if last_sell is not None:
            try:
                if (now - float(last_sell)) / 60.0 < float(g.post_sell_reentry_cooldown_minutes):
                    return False, f"post_sell_cooldown:{m}"
            except (TypeError, ValueError):
                pass
        if last_buy is not None:
            try:
                if (now - float(last_buy)) / 60.0 < float(g.rebuy_cooldown_minutes):
                    return False, f"rebuy_cooldown:{m}"
            except (TypeError, ValueError):
                pass
        return False, f"cooldown:{m}"

    hourly = _buy_events_last_hour(state, m)
    if int(g.max_buy_per_market_per_hour) > 0 and len(hourly) >= int(g.max_buy_per_market_per_hour):
        return False, f"hourly_buy_cap:{m}:{len(hourly)}>={g.max_buy_per_market_per_hour}"

    counts = _dict_field(state, "buy_count_by_market_today")
    n_today = int(counts.get(m, 0) or 0)
    if int(g.max_add_on_buys_per_market_per_day) > 0 and n_today >= int(g.max_add_on_buys_per_market_per_day):
        return False, f"daily_buy_count_cap:{m}:{n_today}>={g.max_add_on_buys_per_market_per_day}"

    krw_by_m = _dict_field(state, "buy_krw_by_market_today")
    spent = float(krw_by_m.get(m, 0.0) or 0.0)
    cap_pct = float(g.max_buy_krw_per_market_per_day_pct)
    if total > 0 and cap_pct > 0:
        cap_krw = total * cap_pct
        if spent + order > cap_krw + 1.0:
            return False, f"daily_buy_krw_cap:{m}:{spent + order:,.0f}>{cap_krw:,.0f}"

    return True, "ok"


def record_buy_in_state(state: dict[str, Any], *, market: str, krw_amount: float) -> None:
    m = _market_key(market)
    now = _now_ts()
    krw = max(0.0, float(krw_amount))

    lbm = _dict_field(state, "last_buy_by_market")
    lbm[m] = now
    state["last_buy_by_market"] = lbm

    ts_map = _dict_field(state, "buy_timestamps_by_market")
    events = list(ts_map.get(m, []) or [])
    events.append(now)
    cutoff = now - 3600.0
    ts_map[m] = [t for t in events if float(t) >= cutoff]
    state["buy_timestamps_by_market"] = ts_map

    krw_map = _dict_field(state, "buy_krw_by_market_today")
    krw_map[m] = float(krw_map.get(m, 0.0) or 0.0) + krw
    state["buy_krw_by_market_today"] = krw_map

    cnt_map = _dict_field(state, "buy_count_by_market_today")
    cnt_map[m] = int(cnt_map.get(m, 0) or 0) + 1
    state["buy_count_by_market_today"] = cnt_map

    open_ts = _dict_field(state, "position_open_ts_by_market")
    if m not in open_ts:
        open_ts[m] = now
    state["position_open_ts_by_market"] = open_ts


def record_sell_in_state(state: dict[str, Any], *, market: str) -> None:
    m = _market_key(market)
    now = _now_ts()
    lsm = _dict_field(state, "last_sell_by_market")
    lsm[m] = now
    state["last_sell_by_market"] = lsm

    open_ts = _dict_field(state, "position_open_ts_by_market")
    open_ts.pop(m, None)
    state["position_open_ts_by_market"] = open_ts


def sell_blocked_by_min_hold(
    state: dict[str, Any],
    *,
    market: str,
    sell_trigger: str,
    cfg: OvertradingGuardConfig | None = None,
) -> tuple[bool, str]:
    """True if SELL should be blocked (except stop_loss / take_profit)."""
    g = cfg or OvertradingGuardConfig()
    trigger = str(sell_trigger or "").lower()
    if trigger in (
        "stop_loss",
        "take_profit",
        "ai_stop",
        "trailing_stop",
        "time_stop",
        "partial_take_profit",
    ):
        return False, "ok"
    if int(g.min_hold_minutes_before_sell) <= 0:
        return False, "ok"

    m = _market_key(market)
    open_ts = _dict_field(state, "position_open_ts_by_market").get(m)
    if open_ts is None:
        return False, "ok"
    try:
        held_min = (_now_ts() - float(open_ts)) / 60.0
    except (TypeError, ValueError):
        return False, "ok"
    if held_min < float(g.min_hold_minutes_before_sell):
        return True, f"min_hold:{held_min:.1f}m<{g.min_hold_minutes_before_sell}m"
    return False, "ok"


def near_take_profit_allowed(pnl_pct: float, *, cfg: OvertradingGuardConfig | None = None) -> bool:
    g = cfg or OvertradingGuardConfig()
    return float(pnl_pct) >= float(g.near_take_profit_min_pnl_pct)
