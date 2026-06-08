"""감사 로그에서 주문번호 추출·주문 상태 리포트 ([실전-5])."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

_ODNO_KEYS = frozenset(
    {
        "odno",
        "ord_no",
        "order_no",
        "org_odno",
        "krx_fwdg_ord_orgno",
    }
)


def load_audit_log(path: str | Path) -> dict[str, Any]:
    """감사 JSON 로드."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("audit log root must be a JSON object")
    return data


def _walk_for_odno(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in _ODNO_KEYS and v is not None and str(v).strip():
                s = str(v).strip()
                if s and s not in ("0", "0000000000"):
                    out.append(s)
            elif lk == "output" and isinstance(v, dict):
                for kk, vv in v.items():
                    if str(kk).upper() == "ODNO" and vv is not None and str(vv).strip():
                        out.append(str(vv).strip())
            _walk_for_odno(v, out)
    elif isinstance(obj, list):
        for x in obj:
            _walk_for_odno(x, out)


def extract_order_ids_from_audit(audit: Mapping[str, Any]) -> list[str]:
    """`results[].raw` 등에서 KIS 주문번호 후보 추출 (중복 제거, 순서 유지)."""
    if not isinstance(audit, Mapping):
        return []
    found: list[str] = []
    _walk_for_odno(dict(audit), found)
    seen: set[str] = set()
    uniq: list[str] = []
    for x in found:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def summarize_order_results(audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    """감사 로그 `results` 요약 (체결 연결용)."""
    if not isinstance(audit, Mapping):
        return []
    rows = audit.get("results")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        if not isinstance(r, Mapping):
            continue
        raw = r.get("raw") if isinstance(r.get("raw"), dict) else {}
        oid = r.get("broker_order_id")
        if oid is None and isinstance(raw, dict):
            rb = raw.get("response_body")
            if isinstance(rb, dict):
                outp = rb.get("output") or rb.get("Output")
                if isinstance(outp, dict):
                    oid = outp.get("ODNO") or outp.get("odno")
        out.append(
            {
                "index": i,
                "symbol": r.get("symbol"),
                "status": r.get("status"),
                "broker_order_id": str(oid) if oid is not None else None,
                "message": r.get("message"),
            }
        )
    return out


def write_order_status_report(
    *,
    audit_path: str | Path,
    audit: dict[str, Any],
    extracted_order_ids: list[str],
    kis_statuses: list[dict[str, Any]] | None,
    output_dir: str | Path = "outputs",
    fill_summaries: list[dict[str, Any]] | None = None,
    fills_saved: dict[str, int] | None = None,
) -> tuple[Path, Path]:
    """JSON·Markdown 리포트 저장."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ymd = now.strftime("%Y%m%d")
    hms = now.strftime("%H%M%S")
    json_path = root / f"live_order_status_{ymd}_{hms}.json"
    md_path = root / "LIVE_ORDER_STATUS.md"

    body: dict[str, Any] = {
        "timestamp": now.isoformat(timespec="seconds"),
        "audit_path": str(Path(audit_path).as_posix()),
        "audit_status": audit.get("status"),
        "extracted_order_ids": extracted_order_ids,
        "audit_results_summary": summarize_order_results(audit),
        "kis_query": kis_statuses,
        "fill_summaries": fill_summaries or [],
        "fills_saved": fills_saved or {},
    }
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# DeepSignal — Live order status",
        "",
        f"- Generated: {body['timestamp']}",
        f"- Audit: `{body['audit_path']}`",
        f"- Audit status: `{body.get('audit_status')}`",
        "",
        "## Extracted order IDs",
        "",
    ]
    if extracted_order_ids:
        for oid in extracted_order_ids:
            lines.append(f"- `{oid}`")
    else:
        lines.append("- (none)")
    lines.extend(["", "## Audit results (from live_approval audit)", ""])
    for row in body["audit_results_summary"]:
        lines.append(
            f"- idx={row.get('index')} symbol={row.get('symbol')} plan_status={row.get('status')} "
            f"broker_order_id={row.get('broker_order_id')}"
        )
    if not body["audit_results_summary"]:
        lines.append("- (none)")
    lines.extend(["", "## KIS query", ""])
    if kis_statuses:
        for row in kis_statuses:
            lines.append(f"- order_id `{row.get('order_id')}`: **{row.get('status')}** — {row.get('message', '')}")
            if row.get("symbol"):
                lines.append(f"  - symbol: {row.get('symbol')}")
    else:
        lines.append("- (no network query — audit parse only, or no results)")
    lines.extend(["", "## Fill summaries", ""])
    for fs in body.get("fill_summaries") or []:
        lines.append(
            f"- Order `{fs.get('order_id')}` **{fs.get('symbol')}**: "
            f"filled={fs.get('filled_quantity')}/{fs.get('order_quantity')} "
            f"remaining={fs.get('remaining_quantity')} avg={fs.get('avg_fill_price')} "
            f"**{fs.get('status_label', fs.get('status'))}**"
        )
    if not body.get("fill_summaries"):
        lines.append("- (none)")
    if fills_saved:
        lines.extend(
            [
                "",
                "## Fills persisted to DB",
                "",
                f"- inserted: {fills_saved.get('inserted', 0)}",
                f"- skipped (duplicate): {fills_saved.get('skipped', 0)}",
            ]
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
