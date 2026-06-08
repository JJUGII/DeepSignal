"""실전 매수 전 단계: 주문 계획(JSON/Markdown)만 생성. 브로커 API·실주문 없음."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


@dataclass
class LiveOrderPlanConfig:
    """live-plan 실행 파라미터 (API 키·계좌 정보 없음)."""

    capital: float = 300_000.0
    max_symbols: int = 3
    max_position_pct: float = 0.25
    min_order_value: float = 10_000.0
    cash_buffer_pct: float = 0.10
    currency: str = "USD"
    dry_run: bool = True


@dataclass
class LiveOrderItem:
    symbol: str
    side: str
    target_weight: float
    target_value: float
    estimated_price: float
    estimated_qty: int
    estimated_order_value: float
    reason: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class LiveOrderPlan:
    date: str
    capital: float
    investable_cash: float
    cash_buffer: float
    currency: str
    orders: list[LiveOrderItem]
    warnings: list[str]
    status: str = "PENDING_APPROVAL"
    approval_required: bool = True
    dry_run: bool = True


def _default_plan_warnings() -> list[str]:
    return [
        "실제 주문은 실행되지 않았습니다.",
        "본 계획은 승인 대기 상태입니다.",
        "매도/손절 자동화는 아직 지원하지 않습니다.",
        "현재가는 지연 데이터일 수 있습니다.",
    ]


def compute_investable_cash(capital: float, cash_buffer_pct: float) -> tuple[float, float]:
    """투자 가능 현금과 현금 버퍼 금액."""
    c = max(0.0, float(capital))
    bp = max(0.0, min(1.0, float(cash_buffer_pct)))
    buf = c * bp
    return c - buf, buf


def build_live_order_plan(
    db_path: str,
    config: LiveOrderPlanConfig,
    *,
    plan_date: str | None = None,
) -> LiveOrderPlan:
    """DB·`PortfolioEngine` 기반으로 BUY 후보만 담은 계획을 만든다 (주문 전송 없음)."""
    from deepsignal.portfolio.portfolio_engine import PortfolioEngine
    from deepsignal.scoring.macro_scorer import MacroScorer
    from deepsignal.storage.database import (
        fetch_latest_economic_indicators,
        fetch_latest_market_price,
        fetch_latest_signals,
    )

    d = plan_date or date.today().isoformat()
    investable, buf_amt = compute_investable_cash(config.capital, config.cash_buffer_pct)
    warnings = list(_default_plan_warnings())

    signals = fetch_latest_signals(db_path, limit=100)
    macro = MacroScorer().calculate_macro_score(fetch_latest_economic_indicators(db_path))
    snapshot = PortfolioEngine().build_portfolio(signals, investable, macro)

    raw_allocs = snapshot.raw.get("allocations_for_paper") or []
    if not isinstance(raw_allocs, list):
        raw_allocs = []

    rows: list[tuple[str, float, float, str]] = []
    for a in raw_allocs:
        if not isinstance(a, dict):
            continue
        sym = str(a.get("symbol", "")).strip().upper()
        if not sym:
            continue
        try:
            tw = float(a.get("target_weight") or 0.0)
            ta = float(a.get("target_amount") or 0.0)
        except (TypeError, ValueError):
            continue
        rationale = str(a.get("rationale") or "portfolio allocation")
        rows.append((sym, tw, ta, rationale))

    rows.sort(key=lambda x: x[2], reverse=True)
    cap = max(0.0, float(config.capital))
    max_pos_cap = cap * max(0.0, float(config.max_position_pct))

    orders: list[LiveOrderItem] = []
    for sym, tw, ta, rationale in rows:
        if len(orders) >= int(config.max_symbols):
            break
        target_value = min(max(0.0, ta), max_pos_cap)
        if target_value <= 0:
            continue

        px_row = fetch_latest_market_price(db_path, sym, source="yfinance")
        if px_row is None:
            warnings.append(f"{sym}: 시세 없음 — 계획에서 제외")
            continue
        try:
            est_px = float(px_row["close"])
        except (TypeError, KeyError, ValueError):
            warnings.append(f"{sym}: 종가 파싱 실패 — 제외")
            continue
        if not math.isfinite(est_px) or est_px <= 0:
            warnings.append(f"{sym}: 유효하지 않은 가격 — 제외")
            continue

        qty = int(math.floor(target_value / est_px))
        est_val = qty * est_px
        if qty <= 0:
            warnings.append(f"{sym}: 수량 0 — 제외")
            continue
        if est_val < float(config.min_order_value):
            warnings.append(
                f"{sym}: 추정 주문액 {est_val:.2f} < 최소 {config.min_order_value} — 제외"
            )
            continue

        orders.append(
            LiveOrderItem(
                symbol=sym,
                side="BUY",
                target_weight=float(tw),
                target_value=float(target_value),
                estimated_price=float(est_px),
                estimated_qty=int(qty),
                estimated_order_value=float(est_val),
                reason=rationale,
                warnings=[],
            )
        )

    return LiveOrderPlan(
        date=d,
        capital=float(config.capital),
        investable_cash=float(investable),
        cash_buffer=float(buf_amt),
        currency=str(config.currency),
        orders=orders,
        warnings=warnings,
        status="PENDING_APPROVAL",
        approval_required=True,
        dry_run=bool(config.dry_run),
    )


def live_order_plan_from_dict(data: dict[str, Any]) -> LiveOrderPlan:
    """`live_order_plan_*.json` 등에서 `LiveOrderPlan` 복원."""
    orders_out: list[LiveOrderItem] = []
    raw_orders = data.get("orders")
    if isinstance(raw_orders, list):
        for o in raw_orders:
            if not isinstance(o, dict):
                continue
            sym = str(o.get("symbol", "")).strip().upper()
            side = str(o.get("side", "BUY")).strip().upper()
            tw = float(o.get("target_weight") or 0.0)
            tv = float(o.get("target_value") or 0.0)
            ep = float(o.get("estimated_price") or 0.0)
            try:
                eq = int(o.get("estimated_qty") or 0)
            except (TypeError, ValueError):
                eq = 0
            eov = float(o.get("estimated_order_value") or 0.0)
            reason = str(o.get("reason") or "")
            ow: list[str] = []
            wraw = o.get("warnings")
            if isinstance(wraw, list):
                ow = [str(x) for x in wraw]
            orders_out.append(
                LiveOrderItem(
                    symbol=sym,
                    side=side,
                    target_weight=tw,
                    target_value=tv,
                    estimated_price=ep,
                    estimated_qty=eq,
                    estimated_order_value=eov,
                    reason=reason,
                    warnings=ow,
                )
            )
    warns: list[str] = []
    wr = data.get("warnings")
    if isinstance(wr, list):
        warns = [str(x) for x in wr]

    return LiveOrderPlan(
        date=str(data.get("date") or ""),
        capital=float(data.get("capital") or 0.0),
        investable_cash=float(data.get("investable_cash") or 0.0),
        cash_buffer=float(data.get("cash_buffer") or 0.0),
        currency=str(data.get("currency") or "USD"),
        orders=orders_out,
        warnings=warns,
        status=str(data.get("status") or "PENDING_APPROVAL"),
        approval_required=bool(data.get("approval_required", True)),
        dry_run=bool(data.get("dry_run", True)),
    )


def plan_to_json_dict(plan: LiveOrderPlan) -> dict[str, Any]:
    """JSON 직렬화용 dict."""
    return {
        "date": plan.date,
        "status": plan.status,
        "approval_required": plan.approval_required,
        "dry_run": plan.dry_run,
        "capital": plan.capital,
        "investable_cash": plan.investable_cash,
        "cash_buffer": plan.cash_buffer,
        "currency": plan.currency,
        "orders": [asdict(o) for o in plan.orders],
        "warnings": list(plan.warnings),
    }


def write_live_order_plan_files(
    plan: LiveOrderPlan,
    *,
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    """JSON·Markdown 저장. 브로커 호출 없음."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    ymd = plan.date.replace("-", "")[:8]
    json_path = root / f"live_order_plan_{ymd}.json"
    md_path = root / "TODAY_LIVE_ORDER_PLAN.md"

    payload = plan_to_json_dict(plan)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# DeepSignal Live Order Plan",
        "",
        f"- **Status**: {plan.status}",
        f"- **Date**: {plan.date}",
        f"- **Capital**: {plan.capital:,.2f} {plan.currency}",
        f"- **Investable Cash**: {plan.investable_cash:,.2f} {plan.currency}",
        f"- **Cash Buffer**: {plan.cash_buffer:,.2f} {plan.currency}",
        f"- **Currency**: {plan.currency}",
        f"- **Dry run**: {plan.dry_run}",
        "",
        "## Orders",
        "",
        "| Symbol | Side | Target Weight | Est. Price | Qty | Est. Value | Reason |",
        "|--------|------|---------------|------------|-----|------------|--------|",
    ]
    for o in plan.orders:
        rw = (o.reason or "").replace("|", "\\|")
        lines.append(
            f"| {o.symbol} | {o.side} | {o.target_weight:.4f} | {o.estimated_price:.4f} | "
            f"{o.estimated_qty} | {o.estimated_order_value:,.2f} | {rw} |"
        )
    if not plan.orders:
        lines.append("| (none) | — | — | — | — | — | — |")

    lines.extend(["", "## Warnings", ""])
    for w in plan.warnings:
        lines.append(f"- {w}")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def print_live_order_plan_summary(plan: LiveOrderPlan, json_path: Path, md_path: Path) -> None:
    """콘솔 요약."""
    print("DeepSignal live order plan created")
    print(f"Status: {plan.status}")
    print(f"Capital: {plan.capital:.2f}")
    print(f"Investable Cash: {plan.investable_cash:.2f}")
    print("Orders:")
    if not plan.orders:
        print("(none)")
    else:
        for o in plan.orders:
            print(
                f"BUY {o.symbol} qty={o.estimated_qty} "
                f"estimated_price={o.estimated_price:.2f} "
                f"estimated_value={o.estimated_order_value:.2f}"
            )
    print(f"Output JSON: {json_path.as_posix()}")
    print(f"Output Report: {md_path.as_posix()}")


def run_live_plan_cli(
    db_path: str,
    config: LiveOrderPlanConfig,
    *,
    output_dir: str | Path = "outputs",
) -> LiveOrderPlan:
    """CLI에서 호출: 계획 생성·저장·요약."""
    plan = build_live_order_plan(db_path, config)
    jp, mp = write_live_order_plan_files(plan, output_dir=output_dir)
    print_live_order_plan_summary(plan, jp, mp)
    return plan
