"""운영자 검토용 수동 SELL 계획서 ([실전-15]). 주문 실행 없음."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from deepsignal.live_trading.risk_guard import RiskGuardPolicy
from deepsignal.storage.database import init_database, load_latest_real_positions

logger = logging.getLogger(__name__)

# 프로젝트 루트: ops/sell_plan.py → parents[3] = project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _detect_asset_class(symbol: str) -> str:
    """6자리 숫자 → kis_stock, 나머지(NVDA, NASD:NVDA 등) → kis_overseas."""
    clean = symbol.split(":")[-1]
    return "kis_stock" if clean.isdigit() else "kis_overseas"


def _compute_dynamic_policy(symbol: str) -> RiskGuardPolicy:
    """종목별 동적 TP/SL → RiskGuardPolicy 반환. 실패 시 기본값."""
    try:
        from deepsignal.risk.dynamic_tpsl import compute_dynamic_tpsl, load_bars_for_symbol
        asset_class = _detect_asset_class(symbol)
        bars, tf_min = load_bars_for_symbol(symbol, asset_class, _PROJECT_ROOT)
        result = compute_dynamic_tpsl(symbol, asset_class, bars or None, timeframe_min=tf_min)
        logger.debug(
            "[SellPlan] %s 동적 TP/SL: %s",
            symbol, result.summary_str(),
        )
        return RiskGuardPolicy(**result.as_policy_kwargs())
    except Exception as exc:
        logger.debug("[SellPlan] %s 동적 TP/SL 실패 (기본값 사용): %s", symbol, exc)
        return RiskGuardPolicy()

SELL_PLAN_STATUS_HOLD = "HOLD"
SELL_PLAN_STATUS_REVIEW = "REVIEW"
SELL_PLAN_STATUS_REDUCE = "REDUCE"
SELL_PLAN_STATUS_EXIT = "EXIT"
SELL_PLAN_STATUS_NO_DATA = "NO_DATA"


@dataclass
class SellPlanItem:
    symbol: str
    quantity: int
    current_price: float | None
    avg_price: float | None
    pnl_pct: float | None
    suggested_action: str
    suggested_sell_ratio: float
    suggested_sell_quantity: int
    reason: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class SellPlanResult:
    status: str
    generated_at: str
    items: list[SellPlanItem]
    warnings: list[str]


def _latest_json(output_dir: str | Path, pattern: str) -> dict[str, Any]:
    paths = sorted(Path(output_dir).glob(pattern))
    if not paths:
        return {}
    path = paths[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"_parse_error": True, "_path": path.as_posix()}
    if isinstance(data, dict):
        data["_path"] = path.as_posix()
        return data
    return {"_non_object_json": True, "_path": path.as_posix()}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _pnl_pct(avg_price: float | None, current_price: float | None) -> float | None:
    if avg_price is None or current_price is None or avg_price <= 0:
        return None
    return (current_price - avg_price) / avg_price


def _sell_quantity(quantity: int, ratio: float) -> int:
    if quantity <= 0 or ratio <= 0:
        return 0
    qty = int(quantity * ratio)
    return max(1, min(quantity, qty))


def build_sell_plan_item(
    position: dict[str, Any],
    *,
    policy: RiskGuardPolicy,
) -> SellPlanItem:
    symbol = str(position.get("symbol") or "").strip()
    quantity = _int_or_zero(position.get("quantity"))
    avg_price = _float_or_none(position.get("avg_price"))
    current_price = _float_or_none(position.get("current_price"))
    pnl_pct = _pnl_pct(avg_price, current_price)
    warnings: list[str] = []

    if quantity <= 0:
        warnings.append("quantity <= 0; excluded from actionable sell quantity")
    if pnl_pct is None:
        action = SELL_PLAN_STATUS_REVIEW
        ratio = 0.0
        reason = f"{symbol}: avg/current price missing; manual review required"
    elif pnl_pct <= policy.stop_loss_pct:
        action = SELL_PLAN_STATUS_EXIT
        ratio = 1.0
        reason = f"{symbol} breached stop_loss_pct ({policy.stop_loss_pct:.2%})"
    elif pnl_pct <= policy.warn_loss_pct:
        action = SELL_PLAN_STATUS_REVIEW
        ratio = 0.0
        reason = f"{symbol} exceeded warn_loss_pct ({policy.warn_loss_pct:.2%})"
    elif pnl_pct >= policy.take_profit_pct:
        action = SELL_PLAN_STATUS_REDUCE
        ratio = float(policy.take_profit_reduce_ratio)
        reason = f"{symbol} reached take_profit_pct ({policy.take_profit_pct:.2%})"
    else:
        action = SELL_PLAN_STATUS_HOLD
        ratio = 0.0
        reason = f"{symbol} within hold range"

    return SellPlanItem(
        symbol=symbol,
        quantity=quantity,
        current_price=current_price,
        avg_price=avg_price,
        pnl_pct=pnl_pct,
        suggested_action=action,
        suggested_sell_ratio=ratio,
        suggested_sell_quantity=_sell_quantity(quantity, ratio),
        reason=reason,
        warnings=warnings,
    )


def _aggregate_status(items: list[SellPlanItem]) -> str:
    if not items:
        return SELL_PLAN_STATUS_NO_DATA
    actions = {item.suggested_action for item in items}
    if SELL_PLAN_STATUS_EXIT in actions:
        return SELL_PLAN_STATUS_EXIT
    if SELL_PLAN_STATUS_REDUCE in actions:
        return SELL_PLAN_STATUS_REDUCE
    if SELL_PLAN_STATUS_REVIEW in actions:
        return SELL_PLAN_STATUS_REVIEW
    return SELL_PLAN_STATUS_HOLD


def build_sell_plan(
    db_path: str,
    *,
    output_dir: str | Path = "outputs",
    broker: str = "kis",
    policy: RiskGuardPolicy | None = None,
) -> SellPlanResult:
    """최신 real_positions 기반 수동 SELL 계획서를 만든다. 주문 실행 없음.

    policy가 명시적으로 주어지면 모든 포지션에 동일 적용.
    주어지지 않으면 포지션별로 동적 TP/SL을 계산한다 (ATR 기반).
    """
    init_database(db_path)
    positions = load_latest_real_positions(db_path, broker=broker)
    items = []
    for pos in positions:
        if _int_or_zero(pos.get("quantity")) <= 0:
            continue
        sym = str(pos.get("symbol") or "").strip()
        # 명시적 policy가 없으면 종목별 동적 TP/SL 계산
        pol = policy if policy is not None else _compute_dynamic_policy(sym)
        items.append(build_sell_plan_item(pos, policy=pol))
    warnings: list[str] = []
    if not items:
        warnings.append("No open real_positions with quantity > 0. Run live-sync-account first.")

    reconcile = _latest_json(output_dir, "reconcile_live_account_*.json")
    if reconcile and reconcile.get("success") is False:
        warnings.append("Latest reconcile report has success=false; verify broker app before any manual SELL decision.")

    ops = _latest_json(output_dir, "ops_dashboard_*.json")
    ops_status = str(ops.get("status") or "")
    if ops_status in {"RECONCILE_MISMATCH", "NO_DATA"}:
        warnings.append(f"Latest ops-dashboard status is {ops_status}; review operational state first.")

    risk = _latest_json(output_dir, "risk_alert_*.json")
    if not risk:
        warnings.append("No latest risk_alert report found; run risk-check before reviewing this sell plan.")

    fills = _latest_json(output_dir, "live_fill_summary_*.json")
    for row in fills.get("summaries") or []:
        if isinstance(row, dict) and _int_or_zero(row.get("remaining_quantity")) > 0:
            warnings.append(
                f"Open/partial fill may exist for order {row.get('order_id')}; verify fills before manual SELL."
            )

    return SellPlanResult(
        status=_aggregate_status(items),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        items=items,
        warnings=warnings,
    )


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def _cell(value: Any) -> str:
    return _fmt(value).replace("|", "\\|")


def write_sell_plan_report(
    result: SellPlanResult,
    *,
    output_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    """`outputs/sell_plan_*.json` 및 `outputs/SELL_PLAN.md` 저장."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    json_path = root / f"sell_plan_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}.json"
    md_path = root / "SELL_PLAN.md"
    body = asdict(result)
    body["disclaimer"] = "This plan does NOT place SELL orders. Manual operator review required."
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _SELL_STATUS_KO = {
        "HOLD": "관망",
        "SELL": "매도 권장",
        "STOP_LOSS": "손절 권장",
        "TAKE_PROFIT": "익절 권장",
        "SELL_ALL": "전량 매도 권장",
        "NO_POSITION": "보유 없음",
        "NO_DATA": "데이터 없음",
    }
    _ACTION_KO = {"HOLD": "관망", "SELL": "매도", "STOP_LOSS": "손절", "TAKE_PROFIT": "익절"}

    status_ko = _SELL_STATUS_KO.get(str(result.status), str(result.status))

    lines = [
        "# DeepSignal — 매도 계획",
        "",
        "## 상태",
        "",
        f"- 전체: **{status_ko}**",
        f"- 생성 시각: {result.generated_at}",
        "",
        "## 보유 종목",
        "",
        "| 종목코드 | 수량 | 평균단가 | 현재가 | 수익률 | 판단 | 권장 매도 수량 |",
        "|---------|------|---------|--------|--------|------|---------------|",
    ]
    for item in result.items:
        action_ko = _ACTION_KO.get(str(item.suggested_action), str(item.suggested_action))
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(item.symbol),
                    _cell(item.quantity),
                    _cell(item.avg_price),
                    _cell(item.current_price),
                    _pct(item.pnl_pct),
                    action_ko,
                    _cell(item.suggested_sell_quantity),
                ]
            )
            + " |"
        )
    if not result.items:
        lines.append("| (없음) | - | - | - | - | - | - |")

    lines.extend(["", "## 사유", ""])
    for item in result.items:
        lines.append(f"- {item.reason}")
    if not result.items:
        lines.append("- 수량이 있는 실제 보유 종목이 없습니다.")

    lines.extend(["", "## 경고", ""])
    all_warnings = list(result.warnings)
    for item in result.items:
        all_warnings.extend(item.warnings)
    for warning in all_warnings:
        lines.append(f"- {warning}")
    if not all_warnings:
        lines.append("- (없음)")

    lines.extend(
        [
            "",
            "## 참고사항",
            "",
            "- 이 계획은 매도 주문을 자동 실행하지 않습니다.",
            "- 반드시 직접 확인 후 수동으로 처리하세요.",
            "- live-approve SELL 자동 실행은 지원하지 않습니다.",
            "- 시장가 주문, 자동 손절 실행, 반복 주문, 취소, KIS POST 요청을 수행하지 않습니다.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def run_sell_plan(
    db_path: str,
    *,
    output_dir: str | Path = "outputs",
    broker: str = "kis",
    policy: RiskGuardPolicy | None = None,
) -> tuple[SellPlanResult, Path, Path]:
    result = build_sell_plan(db_path, output_dir=output_dir, broker=broker, policy=policy)
    json_path, md_path = write_sell_plan_report(result, output_dir=output_dir)
    return result, json_path, md_path
