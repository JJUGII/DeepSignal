"""Pre-trade execution quality: min order, spread/slippage proxy, fee-adjusted expectancy."""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

from deepsignal.crypto_trading.upbit_broker import MIN_ORDER_KRW, UpbitBroker, UpbitOrderResult, UpbitTicker
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_COST = DEFAULT_ANALYSIS_CONDITIONS.cost
_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto


def effective_min_order_krw() -> float:
    """Upbit exchange floor vs configured policy floor (default 10,000 KRW)."""
    return max(float(MIN_ORDER_KRW), float(_COST.min_order_value_krw))


@dataclass
class PreTradeExecutionReport:
    allowed: bool
    reasons: list[str]
    side: str
    market: str
    order_krw: float
    effective_order_krw: float
    limit_price: float
    reference_price: float
    spread_bps: float
    slippage_bps_assumed: float
    fee_rate_one_way: float
    round_trip_fee_krw: float
    take_profit_pct: float
    stop_loss_pct: float
    gross_reward_krw: float
    gross_risk_krw: float
    net_reward_after_costs: float
    net_risk_after_costs: float
    expectancy_krw: float
    gross_rr: float
    net_rr_after_fees: float
    configured_min_rr: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def floor_order_krw(
    order_krw: float,
    *,
    available_krw: float,
    min_floor_krw: float | None = None,
) -> tuple[float, list[str]]:
    floor = float(min_floor_krw if min_floor_krw is not None else effective_min_order_krw())
    notes: list[str] = []
    raw = max(0.0, float(order_krw))
    if raw > 0 and raw < floor:
        notes.append(f"주문액 {raw:,.0f}원 → 최소 {floor:,.0f}원으로 상향")
    effective = max(floor, math.floor(raw)) if raw > 0 else 0.0
    if available_krw > 0 and effective > available_krw:
        effective = math.floor(available_krw * 0.95)
        notes.append(f"가용 잔고 대비 조정 → {effective:,.0f}원")
    if effective < floor:
        notes.append(f"최소주문 {floor:,.0f}원 미달 (가용 {available_krw:,.0f}원)")
        return 0.0, notes
    return effective, notes


def estimate_spread_bps(ticker: UpbitTicker) -> float:
    """Orderbook-free spread proxy from liquidity + short-term volatility."""
    vol24 = float(ticker.acc_trade_price_24h or 0.0)
    if vol24 < 300_000_000:
        liq_bps = 25.0
    elif vol24 < 1_000_000_000:
        liq_bps = 15.0
    elif vol24 < 5_000_000_000:
        liq_bps = 8.0
    else:
        liq_bps = 4.0
    vola_bps = abs(float(ticker.signed_change_rate or 0.0)) * 10_000.0 * 0.12
    return min(50.0, liq_bps + vola_bps + float(_COST.slippage_bps) * 0.5)


def evaluate_pre_trade(
    broker: UpbitBroker,
    *,
    market: str,
    side: Literal["buy", "sell"],
    order_krw: float,
    limit_price: float | None = None,
    take_profit_pct: float | None = None,
    stop_loss_pct: float | None = None,
    min_rr_after_fees: float | None = None,
    max_spread_bps: float | None = None,
    available_krw: float | None = None,
) -> PreTradeExecutionReport:
    blockers: list[str] = []
    tp = float(take_profit_pct if take_profit_pct is not None else _CRYPTO.take_profit_pct)
    sl = float(stop_loss_pct if stop_loss_pct is not None else _CRYPTO.stop_loss_pct)
    min_rr = float(min_rr_after_fees if min_rr_after_fees is not None else getattr(_CRYPTO, "min_expected_rr_after_fees", 1.15))
    max_spread = float(max_spread_bps if max_spread_bps is not None else getattr(_CRYPTO, "max_spread_bps_for_entry", 35.0))
    fee_one = float(_COST.commission_rate)
    slip_bps = float(_COST.slippage_bps)

    if available_krw is None:
        try:
            available_krw = float(broker.get_krw_available())
        except Exception:
            available_krw = 0.0

    effective, floor_notes = floor_order_krw(order_krw, available_krw=float(available_krw or 0.0))
    blockers.extend(floor_notes)
    if effective <= 0:
        return _blocked_report(
            side=side,
            market=market,
            order_krw=order_krw,
            effective=0.0,
            blockers=blockers,
            tp=tp,
            sl=sl,
            min_rr=min_rr,
            fee_one=fee_one,
            slip_bps=slip_bps,
        )

    try:
        ticker = broker.get_ticker(market)
    except Exception as exc:
        blockers.append(f"시세 조회 실패: {exc}")
        return _blocked_report(
            side=side,
            market=market,
            order_krw=order_krw,
            effective=effective,
            blockers=blockers,
            tp=tp,
            sl=sl,
            min_rr=min_rr,
            fee_one=fee_one,
            slip_bps=slip_bps,
        )

    ref = float(limit_price) if limit_price and limit_price > 0 else float(ticker.trade_price)
    spread_bps = estimate_spread_bps(ticker)
    spread_hard = False
    if side == "buy" and spread_bps > max_spread:
        blockers.append(f"스프레드 추정 {spread_bps:.1f}bps > 한도 {max_spread:.1f}bps")
        spread_hard = True

    slip_krw = effective * (spread_bps / 10_000.0) * 0.5
    round_trip_fee = effective * fee_one * 2.0
    gross_reward = effective * (tp / 100.0)
    gross_risk = effective * (abs(sl) / 100.0)
    total_cost = round_trip_fee + slip_krw
    net_reward = gross_reward - total_cost
    net_risk = gross_risk + total_cost
    gross_rr = gross_reward / gross_risk if gross_risk > 0 else 0.0
    net_rr = net_reward / net_risk if net_risk > 0 else 0.0
    expectancy = net_reward - net_risk  # simplified edge vs full loss

    hard_flags: list[bool] = [spread_hard]
    if side == "buy":
        if gross_risk <= 0:
            blockers.append("손절 폭이 0 — R:R 계산 불가")
            hard_flags.append(True)
        elif net_rr < min_rr:
            blockers.append(
                f"수수료·슬리피지 반영 R:R {net_rr:.2f} < 최소 {min_rr:.2f} "
                f"(목표 {tp:.2f}% / 손절 {sl:.2f}%)"
            )
            hard_flags.append(True)
        if net_reward <= 0:
            blockers.append(f"수수료·슬리피지 반영 순이익 목표 {net_reward:,.0f}원 ≤ 0")
            hard_flags.append(True)

    allowed = not any(hard_flags)

    return PreTradeExecutionReport(
        allowed=allowed,
        reasons=blockers,
        side=side,
        market=market,
        order_krw=order_krw,
        effective_order_krw=effective,
        limit_price=ref,
        reference_price=ref,
        spread_bps=spread_bps,
        slippage_bps_assumed=slip_bps,
        fee_rate_one_way=fee_one,
        round_trip_fee_krw=round_trip_fee,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        gross_reward_krw=gross_reward,
        gross_risk_krw=gross_risk,
        net_reward_after_costs=net_reward,
        net_risk_after_costs=net_risk,
        expectancy_krw=expectancy,
        gross_rr=gross_rr,
        net_rr_after_fees=net_rr,
        configured_min_rr=min_rr,
    )


def _blocked_report(
    *,
    side: str,
    market: str,
    order_krw: float,
    effective: float,
    blockers: list[str],
    tp: float,
    sl: float,
    min_rr: float,
    fee_one: float,
    slip_bps: float,
) -> PreTradeExecutionReport:
    return PreTradeExecutionReport(
        allowed=False,
        reasons=blockers,
        side=side,
        market=market,
        order_krw=order_krw,
        effective_order_krw=effective,
        limit_price=0.0,
        reference_price=0.0,
        spread_bps=0.0,
        slippage_bps_assumed=slip_bps,
        fee_rate_one_way=fee_one,
        round_trip_fee_krw=0.0,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        gross_reward_krw=0.0,
        gross_risk_krw=0.0,
        net_reward_after_costs=0.0,
        net_risk_after_costs=0.0,
        expectancy_krw=0.0,
        gross_rr=0.0,
        net_rr_after_fees=0.0,
        configured_min_rr=min_rr,
    )


def should_block_entry_by_execution_quality(report: PreTradeExecutionReport) -> bool:
    return report.side.lower() == "buy" and not report.allowed


def apply_execution_quality_to_buy_amount(
    broker: UpbitBroker,
    *,
    market: str,
    order_krw: float,
    take_profit_pct: float | None = None,
    stop_loss_pct: float | None = None,
) -> tuple[float, PreTradeExecutionReport]:
    report = evaluate_pre_trade(
        broker,
        market=market,
        side="buy",
        order_krw=order_krw,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
    )
    if should_block_entry_by_execution_quality(report):
        return 0.0, report
    return report.effective_order_krw, report


def record_fill_slippage_feedback(
    output_dir: str | Path,
    *,
    market: str,
    side: str,
    limit_price: float,
    fill_price: float,
    order_krw: float,
) -> None:
    """Append realized slippage (bps) for post-trade tuning."""
    from pathlib import Path
    import json

    if limit_price <= 0 or fill_price <= 0:
        return
    from datetime import datetime, timezone
    slip_bps = abs(float(fill_price) - float(limit_price)) / float(limit_price) * 10_000.0
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "market": market,
        "side": side,
        "limit_price": limit_price,
        "fill_price": fill_price,
        "order_krw": order_krw,
        "slippage_bps": slip_bps,
    }
    path = Path(output_dir) / "CRYPTO_FILL_SLIPPAGE.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def limit_buy_price_from_ticker(ticker: UpbitTicker, *, aggression_tick_pct: float = 0.0) -> float:
    """Limit at last trade; optional tiny tick toward ask without market order."""
    base = float(ticker.trade_price)
    if aggression_tick_pct <= 0:
        return base
    return base * (1.0 + aggression_tick_pct / 100.0)


def place_limit_buy_with_requote(
    broker: UpbitBroker,
    *,
    market: str,
    krw_amount: float,
    price: float | None = None,
    execute: bool = False,
    requote_attempts: int | None = None,
    requote_wait_sec: float | None = None,
    requote_tick_pct: float | None = None,
) -> tuple[UpbitOrderResult, list[dict[str, Any]]]:
    """
    Post limit buy at trade price; on unfilled wait, cancel and re-quote (+tick).
    Never sends market (ord_type=market) orders.
    """
    attempts = int(requote_attempts if requote_attempts is not None else getattr(_CRYPTO, "limit_buy_requote_max_attempts", 2))
    wait_sec = float(requote_wait_sec if requote_wait_sec is not None else getattr(_CRYPTO, "limit_buy_requote_wait_sec", 8.0))
    tick_pct = float(requote_tick_pct if requote_tick_pct is not None else getattr(_CRYPTO, "limit_buy_requote_tick_pct", 0.05))
    audit_steps: list[dict[str, Any]] = []
    ticker = broker.get_ticker(market)
    limit_price = float(price if price is not None else limit_buy_price_from_ticker(ticker))
    remaining_krw = float(krw_amount)

    last: UpbitOrderResult | None = None
    for attempt in range(max(1, attempts + 1)):
        last = broker.place_limit_buy(
            market=market,
            krw_amount=remaining_krw,
            price=limit_price,
            execute=execute,
        )
        audit_steps.append(
            {
                "attempt": attempt,
                "limit_price": limit_price,
                "krw_amount": remaining_krw,
                "status": last.status,
                "uuid": last.uuid,
            }
        )
        if not execute or not last.uuid or broker.config.dry_run:
            return last, audit_steps

        time.sleep(max(wait_sec, 0.5))
        raw = broker.get_order(str(last.uuid))
        state = str(raw.get("state", ""))
        executed_vol = float(raw.get("executed_volume", 0) or 0)
        if state == "done":
            return last, audit_steps
        if executed_vol > 0:
            # partial — do not chase with market; leave resting
            audit_steps[-1]["partial_fill"] = True
            return last, audit_steps
        if state == "cancel":
            break
        if attempt < attempts:
            try:
                broker.cancel_order(str(last.uuid))
                audit_steps[-1]["cancelled_for_requote"] = True
            except Exception as exc:
                audit_steps[-1]["cancel_failed"] = str(exc)
                return last, audit_steps
            limit_price = limit_buy_price_from_ticker(broker.get_ticker(market), aggression_tick_pct=tick_pct * (attempt + 1))
        else:
            try:
                broker.cancel_order(str(last.uuid))
                audit_steps[-1]["cancelled_unfilled"] = True
            except Exception:
                pass
    assert last is not None
    return last, audit_steps
