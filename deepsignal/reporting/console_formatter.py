"""콘솔용 ASCII 표 포맷터 (표준 라이브러리만 사용)."""

from __future__ import annotations

import math
from typing import Any, Sequence


def _cell(v: Any, max_len: int | None = None) -> str:
    if v is None:
        s = "-"
    elif isinstance(v, float) and (math.isnan(v) or not math.isfinite(v)):
        s = "-"
    else:
        s = str(v).replace("\n", " ").replace("\r", " ")
    if max_len is not None and len(s) > max_len:
        return s[: max(0, max_len - 3)] + "..."
    return s


def _fmt_float(v: Any, nd: int = 2) -> str:
    if v is None:
        return "-"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(x):
        return "-"
    return f"{x:.{nd}f}"


def _ascii_table(headers: list[str], rows: list[list[str]], col_max: list[int]) -> str:
    n = len(headers)
    if len(col_max) != n:
        raise ValueError("headers와 col_max 길이가 일치해야 합니다.")
    caps = [max(col_max[j], len(headers[j])) for j in range(n)]
    data = [[_cell(rows[i][j], caps[j]) for j in range(n)] for i in range(len(rows))]
    widths: list[int] = []
    for j in range(n):
        content_max = max((len(data[i][j]) for i in range(len(data))), default=0)
        widths.append(min(max(len(headers[j]), content_max), caps[j]))

    def line(cells: Sequence[str]) -> str:
        parts: list[str] = []
        for j in range(n):
            c = cells[j]
            if len(c) > widths[j]:
                c = c[: max(0, widths[j] - 3)] + "..."
            parts.append(f" {c.ljust(widths[j])} ")
        return "|" + "|".join(parts) + "|"

    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    out = [sep, line(headers), sep]
    for r in data:
        out.append(line(r))
    out.append(sep)
    return "\n".join(out)


def format_signals_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "저장된 시그널이 없습니다."
    headers = [
        "symbol",
        "signal_date",
        "action",
        "technical_score",
        "news_score",
        "final_score",
        "confidence",
        "reason",
    ]
    caps = [10, 12, 16, 14, 12, 12, 10, 40]
    data: list[list[str]] = []
    for r in rows:
        data.append(
            [
                _cell(r.get("symbol"), caps[0]),
                _cell(r.get("signal_date"), caps[1]),
                _cell(r.get("action"), caps[2]),
                _fmt_float(r.get("technical_score")),
                _fmt_float(r.get("news_score")),
                _fmt_float(r.get("final_score")),
                _fmt_float(r.get("confidence")),
                _cell(r.get("reason"), caps[7]),
            ]
        )
    return _ascii_table(headers, data, caps)


def format_backtests_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "저장된 백테스트 결과가 없습니다."
    headers = [
        "symbol",
        "period",
        "final_value",
        "total_return_pct",
        "trade_count",
        "win_rate",
        "max_drawdown_pct",
    ]
    caps = [10, 28, 12, 16, 12, 10, 16]
    data: list[list[str]] = []
    for r in rows:
        sd = r.get("start_date") or "-"
        ed = r.get("end_date") or "-"
        period = f"{sd} ~ {ed}"
        wr = r.get("win_rate")
        wrs = _fmt_float(wr) + "%" if wr is not None else "-"
        mdd = r.get("max_drawdown_pct")
        mdds = _fmt_float(mdd) + "%" if mdd is not None else "-"
        data.append(
            [
                _cell(r.get("symbol"), caps[0]),
                _cell(period, caps[1]),
                _fmt_float(r.get("final_value")),
                _fmt_float(r.get("total_return_pct")) + "%",
                _cell(r.get("trade_count"), caps[4]),
                wrs,
                mdds,
            ]
        )
    return _ascii_table(headers, data, caps)


def format_paper_report(
    snapshot: dict[str, Any] | None,
    positions: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("=== paper_account_snapshots (최신 1건) ===")
    if snapshot is None:
        lines.append("저장된 스냅샷이 없습니다.")
    else:
        lines.append(f"  snapshot_date : {_cell(snapshot.get('snapshot_date'))}")
        lines.append(f"  cash          : {_fmt_float(snapshot.get('cash'))}")
        lines.append(f"  equity        : {_fmt_float(snapshot.get('equity'))}")
        lines.append(f"  positions_val : {_fmt_float(snapshot.get('positions_value'))}")
        lines.append(f"  last_action   : {_cell(snapshot.get('last_action'))}")
        lines.append(f"  reason        : {_cell(snapshot.get('reason'), 72)}")

    lines.append("")
    lines.append("=== paper_positions ===")
    if not positions:
        lines.append("포지션이 없습니다.")
    else:
        headers = ["symbol", "quantity", "avg_price", "updated_at"]
        caps = [10, 10, 12, 20]
        data = [
            [
                _cell(p.get("symbol"), caps[0]),
                _cell(p.get("quantity"), caps[1]),
                _fmt_float(p.get("avg_price")),
                _cell(p.get("updated_at"), caps[3]),
            ]
            for p in positions
        ]
        lines.append(_ascii_table(headers, data, caps))

    lines.append("")
    lines.append("=== paper_trades (최신 20) ===")
    if not trades:
        lines.append("저장된 모의 체결이 없습니다.")
    else:
        headers = [
            "symbol",
            "trade_date",
            "side",
            "price",
            "qty",
            "cash_before",
            "cash_after",
            "reason",
        ]
        caps = [8, 12, 6, 10, 6, 12, 12, 28]
        data = []
        for t in trades:
            data.append(
                [
                    _cell(t.get("symbol"), caps[0]),
                    _cell(t.get("trade_date"), caps[1]),
                    _cell(t.get("side"), caps[2]),
                    _fmt_float(t.get("price")),
                    _cell(t.get("quantity"), caps[4]),
                    _fmt_float(t.get("cash_before")),
                    _fmt_float(t.get("cash_after")),
                    _cell(t.get("reason"), caps[7]),
                ]
            )
        lines.append(_ascii_table(headers, data, caps))

    return "\n".join(lines)
