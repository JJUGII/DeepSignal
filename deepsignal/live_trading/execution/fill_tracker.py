"""KIS 체결 추출·집계·partial fill 상태 ([실전-8]). `paper_*`와 무관."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence


@dataclass
class FillRecord:
    """단일 체결 행 (DB 저장 전)."""

    broker: str
    symbol: str
    side: str | None
    order_id: str | None
    fill_id: str | None
    fill_quantity: int
    fill_price: float | None
    fill_value: float | None
    fill_timestamp: str | None
    raw: dict[str, Any]


@dataclass
class PartialFillStatus:
    order_id: str | None
    symbol: str
    ordered_quantity: int
    filled_quantity: int
    remaining_quantity: int
    avg_fill_price: float | None
    fully_filled: bool
    partially_filled: bool
    unfilled: bool


def _norm_symbol(sym: str | None) -> str:
    s = (sym or "").strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def _pick_str(row: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _pick_int(row: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for k in keys:
        v = row.get(k)
        if v is None or str(v).strip() == "":
            continue
        try:
            return int(float(str(v).replace(",", "")))
        except (TypeError, ValueError):
            continue
    return None


def _pick_float(row: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = row.get(k)
        if v is None or str(v).strip() == "":
            continue
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return None


def _side_from_code(code: str | None) -> str | None:
    if code in ("02", "2"):
        return "BUY"
    if code in ("01", "1"):
        return "SELL"
    return code


def _fill_timestamp_from_row(row: Mapping[str, Any]) -> str | None:
    d = _pick_str(row, ("ord_dt", "ORD_DT", "ccld_dt", "CCLD_DT"))
    t = _pick_str(row, ("ord_tmd", "ORD_TMD", "ccld_tmd", "CCLD_TMD", "ord_tm", "ORD_TM"))
    if d and t:
        return f"{d}T{t}"
    if d:
        return d
    return None


def synthetic_fill_id(
    *,
    order_id: str | None,
    fill_timestamp: str | None,
    fill_quantity: int,
    fill_price: float | None,
) -> str:
    """fill_id 없을 때 dedupe용 synthetic key."""
    base = f"{order_id or ''}|{fill_timestamp or ''}|{fill_quantity}|{fill_price or ''}"
    return "syn_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _row_to_fill(
    row: Mapping[str, Any],
    *,
    broker: str,
    body: Mapping[str, Any] | None = None,
    default_order_id: str | None = None,
) -> FillRecord | None:
    fq = _pick_int(row, ("ccld_qty", "CCLD_QTY", "tot_ccld_qty", "TOT_CCLD_QTY", "exec_qty"))
    if fq is None or fq <= 0:
        return None
    sym = _norm_symbol(_pick_str(row, ("pdno", "PDNO", "prdt_cd", "PRDT_CD")) or "")
    oid = _pick_str(row, ("odno", "ODNO", "ord_no")) or default_order_id
    fp = _pick_float(
        row,
        ("ccld_unpr", "CCLD_UNPR", "avg_ccld_unpr", "AVG_CCLD_UNPR", "avg_prvs", "AVG_PRVS"),
    )
    fv = (fp * fq) if fp is not None else None
    ts = _fill_timestamp_from_row(row)
    side = _side_from_code(_pick_str(row, ("sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD")))
    fid = _pick_str(row, ("ccld_no", "CCLD_NO", "exec_no", "EXEC_NO"))
    if not fid:
        fid = synthetic_fill_id(
            order_id=oid,
            fill_timestamp=ts,
            fill_quantity=fq,
            fill_price=fp,
        )
    raw: dict[str, Any] = {"matched_row": dict(row)}
    if body:
        raw["response_body"] = dict(body)
    return FillRecord(
        broker=broker,
        symbol=sym,
        side=side,
        order_id=oid,
        fill_id=fid,
        fill_quantity=fq,
        fill_price=fp,
        fill_value=fv,
        fill_timestamp=ts,
        raw=raw,
    )


def extract_fills_from_kis_order_status(
    raw: Mapping[str, Any] | None,
    *,
    broker: str = "kis",
    default_order_id: str | None = None,
    default_symbol: str | None = None,
) -> list[FillRecord]:
    """
    KIS `inquire-daily-ccld` 응답 또는 `BrokerOrderStatus.raw`에서 체결 행 추출.

    - `output2`: 체결 상세(복수 행)
    - `output1`: 체결 수량이 있으면 집계 1건으로 synthetic fill
    """
    if not raw:
        return []
    body = raw.get("response_body") if isinstance(raw.get("response_body"), dict) else raw
    if not isinstance(body, dict):
        return []
    fills: list[FillRecord] = []
    seen_keys: set[str] = set()

    def _add(rec: FillRecord | None) -> None:
        if rec is None or rec.fill_quantity <= 0:
            return
        key = rec.fill_id or synthetic_fill_id(
            order_id=rec.order_id,
            fill_timestamp=rec.fill_timestamp,
            fill_quantity=rec.fill_quantity,
            fill_price=rec.fill_price,
        )
        if key in seen_keys:
            return
        seen_keys.add(key)
        if default_symbol and not rec.symbol:
            rec.symbol = _norm_symbol(default_symbol)
        fills.append(rec)

    out2 = body.get("output2") or body.get("Output2")
    rows2: list[Any] = out2 if isinstance(out2, list) else ([] if out2 is None else [out2])
    for row in rows2:
        if isinstance(row, dict):
            _add(_row_to_fill(row, broker=broker, body=body, default_order_id=default_order_id))

    orders_with_detail_fills = {f.order_id for f in fills if f.order_id}

    out1 = body.get("output1") or body.get("Output1")
    rows1: list[Any] = out1 if isinstance(out1, list) else ([] if out1 is None else [out1])
    for row in rows1:
        if not isinstance(row, dict):
            continue
        fq = _pick_int(row, ("tot_ccld_qty", "TOT_CCLD_QTY", "ccld_qty", "CCLD_QTY"))
        if fq is None or fq <= 0:
            continue
        oid = _pick_str(row, ("odno", "ODNO")) or default_order_id
        if oid and oid in orders_with_detail_fills:
            continue
        sym = _norm_symbol(_pick_str(row, ("pdno", "PDNO")) or (default_symbol or ""))
        ap = _pick_float(row, ("avg_prvs", "AVG_PRVS", "avg_ccld_unpr", "AVG_CCLD_UNPR"))
        ts = _fill_timestamp_from_row(row)
        fid = synthetic_fill_id(order_id=oid, fill_timestamp=ts, fill_quantity=fq, fill_price=ap)
        if fid in seen_keys:
            continue
        seen_keys.add(fid)
        fills.append(
            FillRecord(
                broker=broker,
                symbol=sym,
                side=_side_from_code(_pick_str(row, ("sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD"))),
                order_id=oid,
                fill_id=fid,
                fill_quantity=fq,
                fill_price=ap,
                fill_value=(ap * fq) if ap is not None else None,
                fill_timestamp=ts,
                raw={"matched_row": dict(row), "response_body": dict(body), "source": "output1_aggregate"},
            )
        )

    matched = raw.get("matched_row")
    if isinstance(matched, dict) and not fills:
        _add(_row_to_fill(matched, broker=broker, body=body, default_order_id=default_order_id))

    return fills


def extract_fills_from_kis_status_dicts(
    kis_statuses: Sequence[Mapping[str, Any]],
    *,
    broker: str = "kis",
) -> list[FillRecord]:
    """`live-order-status`용 KIS 조회 dict 목록에서 체결 추출."""
    all_fills: list[FillRecord] = []
    for st in kis_statuses:
        raw = st.get("raw") if isinstance(st.get("raw"), dict) else {}
        oid = st.get("order_id")
        sym = st.get("symbol")
        all_fills.extend(
            extract_fills_from_kis_order_status(
                raw,
                broker=broker,
                default_order_id=str(oid) if oid else None,
                default_symbol=str(sym) if sym else None,
            )
        )
        fq = st.get("filled_quantity")
        oq = st.get("quantity")
        if (
            isinstance(fq, int)
            and fq > 0
            and not all_fills
            and oid
        ):
            ap = st.get("avg_fill_price")
            all_fills.append(
                FillRecord(
                    broker=broker,
                    symbol=_norm_symbol(str(sym or "")),
                    side=st.get("side") if isinstance(st.get("side"), str) else None,
                    order_id=str(oid),
                    fill_id=synthetic_fill_id(
                        order_id=str(oid),
                        fill_timestamp=None,
                        fill_quantity=int(fq),
                        fill_price=float(ap) if ap is not None else None,
                    ),
                    fill_quantity=int(fq),
                    fill_price=float(ap) if ap is not None else None,
                    fill_value=(
                        float(ap) * int(fq) if ap is not None else None
                    ),
                    fill_timestamp=None,
                    raw={"source": "status_dict", "status_row": dict(st)},
                )
            )
    return all_fills


def aggregate_order_fills(
    fills: Sequence[FillRecord | Mapping[str, Any]],
    *,
    order_quantity: int | None = None,
    order_id: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """체결 행 집계 → avg fill price·remaining 등."""
    rows: list[FillRecord] = []
    for f in fills:
        if isinstance(f, FillRecord):
            rows.append(f)
        elif isinstance(f, Mapping):
            rows.append(
                FillRecord(
                    broker=str(f.get("broker") or "kis"),
                    symbol=_norm_symbol(str(f.get("symbol") or "")),
                    side=f.get("side") if f.get("side") is not None else None,
                    order_id=str(f.get("order_id")) if f.get("order_id") else None,
                    fill_id=str(f.get("fill_id")) if f.get("fill_id") else None,
                    fill_quantity=int(f.get("fill_quantity") or 0),
                    fill_price=f.get("fill_price"),
                    fill_value=f.get("fill_value"),
                    fill_timestamp=f.get("fill_timestamp"),
                    raw=f.get("raw") if isinstance(f.get("raw"), dict) else {},
                )
            )

    if order_id:
        rows = [r for r in rows if r.order_id == order_id or not r.order_id]
    if symbol:
        sym = _norm_symbol(symbol)
        rows = [r for r in rows if not r.symbol or r.symbol == sym]

    filled = sum(r.fill_quantity for r in rows if r.fill_quantity > 0)
    fill_count = len([r for r in rows if r.fill_quantity > 0])
    total_value = 0.0
    value_qty = 0
    for r in rows:
        if r.fill_value is not None and r.fill_quantity > 0:
            total_value += float(r.fill_value)
            value_qty += r.fill_quantity
        elif r.fill_price is not None and r.fill_quantity > 0:
            total_value += float(r.fill_price) * r.fill_quantity
            value_qty += r.fill_quantity
    avg_fill = (total_value / value_qty) if value_qty > 0 else None

    oq = int(order_quantity or 0)
    if oq <= 0 and rows:
        oq = filled
    remaining = max(0, oq - filled) if oq > 0 else 0
    fully = oq > 0 and filled >= oq
    partial = filled > 0 and not fully
    unfilled = filled == 0 and oq > 0

    return {
        "order_id": order_id or (rows[0].order_id if rows else None),
        "symbol": symbol or (rows[0].symbol if rows else ""),
        "order_quantity": oq,
        "filled_quantity": filled,
        "remaining_quantity": remaining,
        "avg_fill_price": avg_fill,
        "fill_count": fill_count,
        "fully_filled": fully,
        "partially_filled": partial,
        "unfilled": unfilled,
    }


def build_partial_fill_status(
    agg: Mapping[str, Any],
    *,
    order_id: str | None = None,
    symbol: str | None = None,
) -> PartialFillStatus:
    """집계 dict → `PartialFillStatus`."""
    oq = int(agg.get("order_quantity") or 0)
    fq = int(agg.get("filled_quantity") or 0)
    rq = int(agg.get("remaining_quantity") or max(0, oq - fq))
    fully = bool(agg.get("fully_filled"))
    partial = bool(agg.get("partially_filled"))
    unfilled = bool(agg.get("unfilled"))
    if not fully and not partial and not unfilled:
        if fq > 0 and oq > 0 and fq < oq:
            partial = True
        elif fq >= oq > 0:
            fully = True
        elif fq == 0 and oq > 0:
            unfilled = True
    return PartialFillStatus(
        order_id=order_id or (str(agg.get("order_id")) if agg.get("order_id") else None),
        symbol=_norm_symbol(symbol or str(agg.get("symbol") or "")),
        ordered_quantity=oq,
        filled_quantity=fq,
        remaining_quantity=rq,
        avg_fill_price=agg.get("avg_fill_price"),
        fully_filled=fully,
        partially_filled=partial,
        unfilled=unfilled,
    )


def partial_fill_status_from_kis_status(
    status_row: Mapping[str, Any],
) -> PartialFillStatus | None:
    """KIS `BrokerOrderStatus` dict 한 건에서 partial fill 상태."""
    oid = status_row.get("order_id")
    sym = _norm_symbol(str(status_row.get("symbol") or ""))
    oq = status_row.get("quantity")
    fq = status_row.get("filled_quantity")
    rq = status_row.get("remaining_quantity")
    try:
        oqi = int(oq) if oq is not None else 0
    except (TypeError, ValueError):
        oqi = 0
    try:
        fqi = int(fq) if fq is not None else 0
    except (TypeError, ValueError):
        fqi = 0
    if rq is not None:
        try:
            rqi = int(rq)
        except (TypeError, ValueError):
            rqi = max(0, oqi - fqi)
    else:
        rqi = max(0, oqi - fqi) if oqi > 0 else 0
    if oqi <= 0 and fqi <= 0:
        return None
    agg = aggregate_order_fills(
        [],
        order_quantity=oqi,
        order_id=str(oid) if oid else None,
        symbol=sym,
    )
    agg["filled_quantity"] = fqi
    agg["remaining_quantity"] = rqi
    agg["avg_fill_price"] = status_row.get("avg_fill_price")
    agg["fully_filled"] = oqi > 0 and fqi >= oqi
    agg["partially_filled"] = fqi > 0 and (oqi <= 0 or fqi < oqi)
    agg["unfilled"] = fqi == 0 and oqi > 0
    return build_partial_fill_status(agg, order_id=str(oid) if oid else None, symbol=sym)


def write_fill_summary_report(
    summaries: list[dict[str, Any]],
    *,
    output_dir: str,
    audit_path: str | None = None,
) -> tuple[Any, Any]:
    """`outputs/live_fill_summary_*.json` 및 `LIVE_FILL_SUMMARY.md`."""
    from pathlib import Path

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ymd = now.strftime("%Y%m%d")
    hms = now.strftime("%H%M%S")
    json_path = root / f"live_fill_summary_{ymd}_{hms}.json"
    md_path = root / "LIVE_FILL_SUMMARY.md"
    body: dict[str, Any] = {
        "timestamp": now.isoformat(timespec="seconds"),
        "audit_path": audit_path,
        "summaries": summaries,
    }
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# DeepSignal — Live fill summary",
        "",
        f"- Generated: {body['timestamp']}",
    ]
    if audit_path:
        lines.append(f"- Audit: `{audit_path}`")
    lines.append("")
    for s in summaries:
        lines.extend(
            [
                f"## Order `{s.get('order_id')}` — `{s.get('symbol')}`",
                "",
                f"- Status: **{s.get('status_label', s.get('status'))}**",
                f"- Ordered: {s.get('order_quantity')}",
                f"- Filled: {s.get('filled_quantity')}",
                f"- Remaining: {s.get('remaining_quantity')}",
                f"- Avg fill price: {s.get('avg_fill_price')}",
                f"- Fill count (DB rows): {s.get('fill_count')}",
                "",
            ]
        )
    if not summaries:
        lines.append("- (no fill summaries)")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def format_fill_summary_console(s: Mapping[str, Any]) -> str:
    """콘솔용 한 주문 요약."""
    status = s.get("status_label") or (
        "FULLY_FILLED"
        if s.get("fully_filled")
        else ("PARTIALLY_FILLED" if s.get("partially_filled") else "UNFILLED")
    )
    lines = [
        f"Order: {s.get('order_id')}",
        f"Symbol: {s.get('symbol')}",
        f"Ordered: {s.get('order_quantity')}",
        f"Filled: {s.get('filled_quantity')}",
        f"Remaining: {s.get('remaining_quantity')}",
        f"Avg Fill Price: {s.get('avg_fill_price')}",
        f"Status: {status}",
    ]
    return "\n".join(lines)


def persist_fill_records_to_db(
    db_path: str,
    fills: Sequence[FillRecord],
    *,
    broker: str | None = None,
) -> tuple[int, int]:
    """`real_fill_history` 저장. 반환 (inserted, skipped_duplicate)."""
    from deepsignal.storage.database import save_real_fill

    inserted = 0
    skipped = 0
    for rec in fills:
        br = broker or rec.broker
        rid = save_real_fill(
            db_path,
            broker=br,
            symbol=rec.symbol,
            fill_quantity=rec.fill_quantity,
            side=rec.side,
            order_id=rec.order_id,
            fill_id=rec.fill_id,
            fill_price=rec.fill_price,
            fill_value=rec.fill_value,
            fill_timestamp=rec.fill_timestamp,
            raw_payload=rec.raw,
        )
        if rid:
            inserted += 1
        else:
            skipped += 1
    return inserted, skipped


def load_open_partial_fill_statuses(
    db_path: str,
    *,
    broker: str = "kis",
    symbol: str | None = None,
    since_minutes: int = 1440,
) -> list[PartialFillStatus]:
    """미완료(partial) 체결 상태 목록 — order guard 입력용."""
    from deepsignal.storage.database import load_recent_real_fills

    fills = load_recent_real_fills(db_path, broker=broker, symbol=symbol, since_minutes=since_minutes)
    by_order: dict[str, list[dict[str, Any]]] = {}
    for f in fills:
        oid = str(f.get("order_id") or "")
        if not oid:
            continue
        by_order.setdefault(oid, []).append(f)
    out: list[PartialFillStatus] = []
    from deepsignal.storage.database import load_recent_real_orders

    order_qty_map: dict[str, int] = {}
    for o in load_recent_real_orders(db_path, broker=broker, symbol=symbol, since_minutes=since_minutes):
        oid = str(o.get("order_id") or "")
        if oid:
            order_qty_map[oid] = int(o.get("quantity") or 0)

    for oid, rows in by_order.items():
        sym = symbol or (rows[0].get("symbol") if rows else "")
        oq = order_qty_map.get(oid)
        agg = aggregate_order_fills(rows, order_id=oid, symbol=str(sym), order_quantity=oq)
        pfs = build_partial_fill_status(agg, order_id=oid, symbol=str(sym))
        if pfs.partially_filled and pfs.remaining_quantity > 0:
            out.append(pfs)
        elif pfs.filled_quantity > 0 and pfs.ordered_quantity <= 0:
            pfs.partially_filled = True
            pfs.remaining_quantity = max(1, pfs.filled_quantity)
            out.append(pfs)
    return out


def fill_summary_for_display(pfs: PartialFillStatus) -> dict[str, Any]:
    """리포트·콘솔용 dict."""
    status_label = (
        "FULLY_FILLED"
        if pfs.fully_filled
        else ("PARTIALLY_FILLED" if pfs.partially_filled else ("UNFILLED" if pfs.unfilled else "UNKNOWN"))
    )
    d = asdict(pfs)
    d["status_label"] = status_label
    d["status"] = status_label
    return d
