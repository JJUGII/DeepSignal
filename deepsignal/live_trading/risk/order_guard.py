"""중복·대기·스냅샷·reconcile 기반 실주문 전 보호 ([실전-7]). `paper_*`와 무관."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from collections.abc import Sequence
from typing import Any

from deepsignal.live_trading.fill_tracker import PartialFillStatus
from deepsignal.live_trading.reconcile import ReconcileResult

_PENDING_STATUSES = frozenset(
    {
        "PENDING",
        "SUBMITTED",
        "UNKNOWN",
        "KIS_ORDER_SUBMITTED",
        "PARTIAL",
        "PARTIALLY_FILLED",
    }
)

_BLOCKING_RECONCILE_TYPES = frozenset(
    {"missing_in_db", "missing_in_broker", "quantity_mismatch"}
)


@dataclass
class OrderGuardIssue:
    symbol: str
    issue_type: str
    severity: str
    message: str


@dataclass
class OrderGuardResult:
    blocked: bool
    issues: list[OrderGuardIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _norm_symbol(sym: str | None) -> str:
    s = (sym or "").strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    s = str(ts).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").split("+")[0])
    except ValueError:
        return None


def _order_raw(order: dict[str, Any]) -> dict[str, Any]:
    raw = order.get("raw")
    if isinstance(raw, dict):
        return raw
    rj = order.get("raw_json")
    if isinstance(rj, dict):
        return rj
    if isinstance(rj, str) and rj.strip():
        try:
            parsed = json.loads(rj)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def _is_partial_fill_order(order: dict[str, Any]) -> bool:
    raw = _order_raw(order)
    qty = int(order.get("quantity") or 0)
    filled = raw.get("filled_quantity")
    remaining = raw.get("remaining_quantity")
    if filled is not None and qty > 0:
        try:
            if int(filled) < qty:
                return True
        except (TypeError, ValueError):
            pass
    if remaining is not None:
        try:
            if int(remaining) > 0:
                return True
        except (TypeError, ValueError):
            pass
    st = str(order.get("status") or raw.get("status") or "").upper()
    if st in ("PARTIAL", "PARTIALLY_FILLED"):
        return True
    return False


def _same_limit(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def check_duplicate_order_risk(
    *,
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float | None,
    broker: str,
    recent_orders: list[dict[str, Any]],
    reconcile_result: ReconcileResult | None,
    latest_snapshot_time: str | None,
    now: datetime | None = None,
    stale_snapshot_minutes: int = 10,
    recent_duplicate_minutes: int = 30,
    block_on_missing_snapshot: bool = True,
    block_on_missing_reconcile: bool = False,
    open_partial_fills: Sequence[PartialFillStatus] | None = None,
) -> OrderGuardResult:
    """
    실주문 직전 중복·대기·reconcile·스냅샷 위험 검사.

  - `recent_orders`: `load_recent_real_orders` 결과.
  - `reconcile_result`: 최신 reconcile 요약 (`None`이면 reconcile 미실행).
    """
    sym = _norm_symbol(symbol)
    side_u = (side or "").strip().upper()
    qty = int(quantity or 0)
    now_dt = now or datetime.now()
    issues: list[OrderGuardIssue] = []
    warnings: list[str] = []

    cutoff = now_dt - timedelta(minutes=max(0, recent_duplicate_minutes))

    for pfs in open_partial_fills or []:
        if _norm_symbol(pfs.symbol) != sym:
            continue
        if pfs.partially_filled and pfs.remaining_quantity > 0:
            issues.append(
                OrderGuardIssue(
                    symbol=sym,
                    issue_type="partial_fill_open",
                    severity="HIGH",
                    message=(
                        f"open partially-filled order exists for {sym} "
                        f"(order_id={pfs.order_id}, remaining={pfs.remaining_quantity})"
                    ),
                )
            )

    for o in recent_orders:
        osym = _norm_symbol(str(o.get("symbol") or ""))
        if osym != sym:
            continue
        oside = str(o.get("side") or "").strip().upper()
        if oside != side_u:
            continue
        ots = _parse_ts(str(o.get("created_at") or ""))
        ost = str(o.get("status") or "").strip().upper()

        if _is_partial_fill_order(o):
            issues.append(
                OrderGuardIssue(
                    symbol=sym,
                    issue_type="partial_fill_pending",
                    severity="HIGH",
                    message=f"partial fill or remaining quantity for {sym} (status={ost})",
                )
            )

        if ost in _PENDING_STATUSES:
            issues.append(
                OrderGuardIssue(
                    symbol=sym,
                    issue_type="pending_order",
                    severity="HIGH",
                    message=f"recent order status {ost!r} for {sym}",
                )
            )

        if ots is not None and ots >= cutoff and side_u == "BUY":
            issues.append(
                OrderGuardIssue(
                    symbol=sym,
                    issue_type="recent_duplicate_buy",
                    severity="HIGH",
                    message=f"duplicate BUY for {sym} within {recent_duplicate_minutes} minutes",
                )
            )

        oqty = int(o.get("quantity") or 0)
        olim = o.get("limit_price")
        if (
            oqty == qty
            and qty > 0
            and _same_limit(olim, limit_price)
            and ots is not None
            and ots >= cutoff
        ):
            issues.append(
                OrderGuardIssue(
                    symbol=sym,
                    issue_type="duplicate_same_params",
                    severity="HIGH",
                    message=(
                        f"same symbol/qty/limit_price repeat for {sym} "
                        f"qty={qty} limit={limit_price}"
                    ),
                )
            )

    if reconcile_result is not None and not reconcile_result.success:
        for bucket, itype in (
            (reconcile_result.missing_in_db, "missing_in_db"),
            (reconcile_result.missing_in_broker, "missing_in_broker"),
            (reconcile_result.quantity_mismatch, "quantity_mismatch"),
        ):
            for item in bucket:
                if _norm_symbol(item.symbol) == sym or not sym:
                    issues.append(
                        OrderGuardIssue(
                            symbol=item.symbol,
                            issue_type="reconcile_mismatch",
                            severity="HIGH",
                            message=item.message,
                        )
                    )
        if not any(i.issue_type == "reconcile_mismatch" for i in issues):
            issues.append(
                OrderGuardIssue(
                    symbol=sym,
                    issue_type="reconcile_mismatch",
                    severity="HIGH",
                    message="reconcile mismatch detected (account state not aligned)",
                )
            )
        warnings.append(
            "reconcile_mismatch: do not submit new automated orders before reconciliation"
        )
    elif reconcile_result is None and block_on_missing_reconcile:
        issues.append(
            OrderGuardIssue(
                symbol=sym,
                issue_type="reconcile_missing",
                severity="MEDIUM",
                message="no reconcile report found — run reconcile-live-account first",
            )
        )

    snap_dt = _parse_ts(latest_snapshot_time)
    if latest_snapshot_time is None or snap_dt is None:
        if block_on_missing_snapshot:
            issues.append(
                OrderGuardIssue(
                    symbol=sym,
                    issue_type="snapshot_missing",
                    severity="HIGH",
                    message="no real_account_snapshots found — run live-sync-account first",
                )
            )
    else:
        age_min = (now_dt - snap_dt).total_seconds() / 60.0
        if age_min > float(stale_snapshot_minutes):
            issues.append(
                OrderGuardIssue(
                    symbol=sym,
                    issue_type="stale_snapshot",
                    severity="HIGH",
                    message=(
                        f"snapshot age {age_min:.1f}m exceeds "
                        f"stale_snapshot_minutes={stale_snapshot_minutes}"
                    ),
                )
            )
            warnings.append("stale_snapshot: refresh with live-sync-account before ordering")

    blocked = any(i.severity == "HIGH" for i in issues)
    if blocked:
        warnings.insert(
            0,
            "WARNING: duplicate or unsafe order risk — KIS POST must not proceed",
        )
    return OrderGuardResult(blocked=blocked, issues=issues, warnings=warnings)


def order_guard_result_to_audit_fields(result: OrderGuardResult) -> dict[str, Any]:
    """감사 로그용 guard 요약 필드."""
    types = {i.issue_type for i in result.issues}
    return {
        "guard_result": {
            "blocked": result.blocked,
            "issues": [asdict(x) for x in result.issues],
            "warnings": list(result.warnings),
        },
        "duplicate_risk_detected": result.blocked,
        "stale_snapshot": "stale_snapshot" in types or "snapshot_missing" in types,
        "reconcile_mismatch": "reconcile_mismatch" in types,
        "recent_orders_found": "recent_duplicate_buy" in types
        or "pending_order" in types
        or "duplicate_same_params" in types,
        "partial_fill_risk": "partial_fill_pending" in types or "partial_fill_open" in types,
    }


def load_order_guard_inputs(
    db_path: str,
    *,
    broker: str = "kis",
    symbol: str | None = None,
    output_dir: str = "outputs",
    since_minutes: int = 30,
) -> tuple[list[dict[str, Any]], ReconcileResult | None, str | None, list[PartialFillStatus]]:
    """DB·reconcile·open partial fills에서 guard 검사 입력을 모은다."""
    from deepsignal.live_trading.reconcile import load_latest_reconcile_state
    from deepsignal.storage.database import (
        load_latest_real_snapshot_time,
        load_recent_real_orders,
    )

    recent = load_recent_real_orders(
        db_path,
        broker=broker,
        symbol=symbol,
        since_minutes=since_minutes,
    )
    from deepsignal.live_trading.fill_tracker import load_open_partial_fill_statuses

    reconcile = load_latest_reconcile_state(output_dir)
    snap_time = load_latest_real_snapshot_time(db_path, broker=broker)
    partials = load_open_partial_fill_statuses(
        db_path, broker=broker, symbol=symbol, since_minutes=since_minutes
    )
    return recent, reconcile, snap_time, partials


def persist_execute_results_to_history(
    db_path: str,
    *,
    broker: str,
    audit_path: str,
    orders: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> int:
    """`live-approve` 실행 후 `real_order_history`에 기록."""
    from deepsignal.storage.database import save_real_order_history

    n = 0
    for i, res in enumerate(results):
        ord_row = orders[i] if i < len(orders) else {}
        sym = str(res.get("symbol") or ord_row.get("symbol") or "")
        if not sym:
            continue
        raw = res.get("raw") if isinstance(res.get("raw"), dict) else {}
        filled = raw.get("filled_quantity")
        remaining = raw.get("remaining_quantity")
        payload = {
            "order": ord_row,
            "result": res,
            "filled_quantity": filled,
            "remaining_quantity": remaining,
        }
        save_real_order_history(
            db_path,
            broker=broker,
            symbol=sym,
            side=str(res.get("side") or ord_row.get("side") or "BUY"),
            quantity=int(res.get("quantity") or ord_row.get("quantity") or 0),
            limit_price=res.get("submitted_price") or ord_row.get("limit_price"),
            estimated_order_value=ord_row.get("estimated_value"),
            status=str(res.get("status") or ""),
            order_id=res.get("broker_order_id"),
            audit_path=audit_path,
            raw_payload=payload,
        )
        n += 1
    return n
