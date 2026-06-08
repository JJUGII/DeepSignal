"""실계좌 포지션 손익·손절/익절 경고 ([실전-12]). 자동매도·SELL 주문 없음."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from deepsignal.scoring.analysis_conditions import risk_guard_policy_defaults

RISK_STATUS_OK = "OK"
RISK_STATUS_WARNING = "WARNING"
RISK_STATUS_STOP_LOSS = "STOP_LOSS_ALERT"
RISK_STATUS_TAKE_PROFIT = "TAKE_PROFIT_ALERT"
RISK_STATUS_MIXED = "MIXED_ALERT"

_RISK_OK = RISK_STATUS_OK
_RISK_WARNING = RISK_STATUS_WARNING
_RISK_STOP = RISK_STATUS_STOP_LOSS
_RISK_TAKE = RISK_STATUS_TAKE_PROFIT
_RISK_MIXED = RISK_STATUS_MIXED


@dataclass
class PositionRisk:
    symbol: str
    quantity: int
    avg_price: float | None
    current_price: float | None
    market_value: float | None
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    risk_level: str
    alerts: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskGuardPolicy:
    stop_loss_pct: float = field(default_factory=lambda: risk_guard_policy_defaults()["stop_loss_pct"])
    take_profit_pct: float = field(default_factory=lambda: risk_guard_policy_defaults()["take_profit_pct"])
    warn_loss_pct: float = field(default_factory=lambda: risk_guard_policy_defaults()["warn_loss_pct"])
    warn_profit_pct: float = field(default_factory=lambda: risk_guard_policy_defaults()["warn_profit_pct"])
    drawdown_from_peak_review_pct: float = field(
        default_factory=lambda: risk_guard_policy_defaults()["drawdown_from_peak_review_pct"]
    )
    entry_drawdown_review_pct: float = field(
        default_factory=lambda: risk_guard_policy_defaults()["entry_drawdown_review_pct"]
    )
    take_profit_reduce_ratio: float = field(
        default_factory=lambda: risk_guard_policy_defaults()["take_profit_reduce_ratio"]
    )


def risk_policy_from_namespace(args: Any) -> RiskGuardPolicy:
    """CLI namespace 등에서 risk-check와 동일한 기본 policy를 만든다."""
    defaults = risk_guard_policy_defaults()
    return RiskGuardPolicy(
        stop_loss_pct=float(getattr(args, "stop_loss_pct", defaults["stop_loss_pct"])),
        take_profit_pct=float(getattr(args, "take_profit_pct", defaults["take_profit_pct"])),
        warn_loss_pct=float(getattr(args, "warn_loss_pct", defaults["warn_loss_pct"])),
        warn_profit_pct=float(getattr(args, "warn_profit_pct", defaults["warn_profit_pct"])),
        drawdown_from_peak_review_pct=float(
            getattr(args, "drawdown_from_peak_review_pct", defaults["drawdown_from_peak_review_pct"])
        ),
        entry_drawdown_review_pct=float(
            getattr(args, "entry_drawdown_review_pct", defaults["entry_drawdown_review_pct"])
        ),
        take_profit_reduce_ratio=float(
            getattr(args, "take_profit_reduce_ratio", defaults["take_profit_reduce_ratio"])
        ),
    )


@dataclass
class RiskGuardResult:
    status: str
    positions: list[PositionRisk] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)
    snapshot_time: str | None = None
    broker: str = "kis"


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm_symbol(sym: str | None) -> str:
    s = (sym or "").strip()
    if s.isdigit():
        return s.zfill(6)
    return s


def _position_raw(position: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(position, Mapping):
        raw = position.get("raw")
        if isinstance(raw, dict):
            return dict(raw)
        return {k: v for k, v in position.items() if k != "raw"}
    raw = getattr(position, "raw", None)
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def evaluate_position_risk(
    position: Mapping[str, Any] | Any,
    policy: RiskGuardPolicy | None = None,
) -> PositionRisk:
    """단일 `real_positions` 행(또는 동일 dict)에 대한 손익·리스크 판정."""
    pol = policy or RiskGuardPolicy()
    if isinstance(position, Mapping):
        sym = _norm_symbol(str(position.get("symbol") or ""))
        qty = int(position.get("quantity") or 0)
        avg = _float_or_none(position.get("avg_price"))
        cur = _float_or_none(position.get("current_price"))
        mv = _float_or_none(position.get("market_value"))
        raw = _position_raw(position)
    else:
        sym = _norm_symbol(str(getattr(position, "symbol", "") or ""))
        qty = int(getattr(position, "quantity", 0) or 0)
        avg = _float_or_none(getattr(position, "avg_price", None))
        cur = _float_or_none(getattr(position, "current_price", None))
        mv = _float_or_none(getattr(position, "market_value", None))
        raw = _position_raw(position)

    alerts: list[str] = []
    risk_level = _RISK_OK

    if avg is None or avg <= 0:
        alerts.append(f"{sym}: avg_price missing or invalid — cannot compute PnL %")
        risk_level = _RISK_WARNING
    if cur is None or cur <= 0:
        alerts.append(f"{sym}: current_price missing or invalid — cannot compute PnL %")
        risk_level = _RISK_WARNING

    pnl: float | None = None
    pnl_pct: float | None = None
    if avg is not None and cur is not None and avg > 0 and qty > 0:
        pnl = (cur - avg) * qty
        pnl_pct = (cur - avg) / avg
    elif avg is not None and cur is not None and avg > 0:
        pnl_pct = (cur - avg) / avg
        pnl = (cur - avg) * qty if qty else None

    if pnl_pct is not None and risk_level != _RISK_WARNING:
        if pnl_pct <= pol.stop_loss_pct:
            risk_level = _RISK_STOP
            alerts.append(f"{sym} stop-loss threshold breached ({pnl_pct * 100:.2f}%)")
        elif pnl_pct >= pol.take_profit_pct:
            risk_level = _RISK_TAKE
            alerts.append(f"{sym} take-profit threshold reached ({pnl_pct * 100:.2f}%)")
        elif pnl_pct <= pol.warn_loss_pct:
            risk_level = _RISK_WARNING
            alerts.append(f"{sym} loss warning ({pnl_pct * 100:.2f}%)")
        elif pnl_pct >= pol.warn_profit_pct:
            risk_level = _RISK_WARNING
            alerts.append(f"{sym} profit warning ({pnl_pct * 100:.2f}%)")
        elif pnl_pct <= pol.entry_drawdown_review_pct and pnl_pct > pol.stop_loss_pct:
            risk_level = _RISK_WARNING
            alerts.append(
                f"{sym} entry drawdown review ({pnl_pct * 100:.2f}% <= {pol.entry_drawdown_review_pct:.2%})"
            )

    peak = _float_or_none(raw.get("peak_price") or raw.get("high_price"))
    if peak is not None and cur is not None and peak > 0 and cur > 0:
        dd_peak = (cur - peak) / peak
        if dd_peak <= pol.drawdown_from_peak_review_pct:
            if risk_level == _RISK_OK:
                risk_level = _RISK_WARNING
            alerts.append(
                f"{sym} peak drawdown review ({dd_peak * 100:.2f}% from peak {peak:.2f})"
            )

    return PositionRisk(
        symbol=sym,
        quantity=qty,
        avg_price=avg,
        current_price=cur,
        market_value=mv,
        unrealized_pnl=pnl,
        unrealized_pnl_pct=pnl_pct,
        risk_level=risk_level,
        alerts=alerts,
        raw=raw,
    )


def _aggregate_status(levels: list[str]) -> str:
    has_stop = _RISK_STOP in levels
    has_take = _RISK_TAKE in levels
    has_warn = _RISK_WARNING in levels
    if has_stop and has_take:
        return _RISK_MIXED
    if has_stop:
        return _RISK_STOP
    if has_take:
        return _RISK_TAKE
    if has_warn:
        return _RISK_WARNING
    return _RISK_OK


def evaluate_portfolio_risk(
    positions: list[Mapping[str, Any] | Any],
    policy: RiskGuardPolicy | None = None,
    *,
    broker: str = "kis",
    snapshot_time: str | None = None,
    total_equity: float | None = None,
) -> RiskGuardResult:
    """포트폴리오 전체 리스크 요약."""
    pol = policy or RiskGuardPolicy()
    evaluated: list[PositionRisk] = []
    for pos in positions:
        pr = evaluate_position_risk(pos, pol)
        if pr.quantity <= 0:
            continue
        evaluated.append(pr)

    portfolio_alerts: list[str] = []
    portfolio_warnings: list[str] = []
    for pr in evaluated:
        for a in pr.alerts:
            if a not in portfolio_alerts:
                portfolio_alerts.append(a)
        if pr.risk_level == _RISK_WARNING:
            portfolio_warnings.append(f"{pr.symbol}: review position (WARNING)")

    if not evaluated:
        portfolio_warnings.append("no open real_positions with quantity > 0")

    concentration_block: dict[str, Any] = {}
    if total_equity is not None and total_equity > 0 and evaluated:
        from deepsignal.analysis.portfolio_concentration import check_position_concentration

        conc = check_position_concentration(positions, total_equity=total_equity)
        concentration_block = conc.to_dict()
        for w in conc.warnings:
            portfolio_warnings.append(w)
            if _RISK_WARNING not in (p.risk_level for p in evaluated):
                pass
        for a in conc.alerts:
            portfolio_alerts.append(a)

    status = _aggregate_status([p.risk_level for p in evaluated]) if evaluated else _RISK_OK
    if concentration_block.get("status") == "ALERT" and status == _RISK_OK:
        status = _RISK_WARNING
    elif concentration_block.get("status") == "WARNING" and status == _RISK_OK:
        status = _RISK_WARNING

    snap = snapshot_time
    if not snap and evaluated:
        first = positions[0] if positions else None
        if isinstance(first, Mapping):
            snap = str(first.get("snapshot_time") or "") or None

    return RiskGuardResult(
        status=status,
        positions=evaluated,
        alerts=portfolio_alerts,
        warnings=portfolio_warnings,
        policy={
            "stop_loss_pct": pol.stop_loss_pct,
            "take_profit_pct": pol.take_profit_pct,
            "warn_loss_pct": pol.warn_loss_pct,
            "warn_profit_pct": pol.warn_profit_pct,
            "drawdown_from_peak_review_pct": pol.drawdown_from_peak_review_pct,
            "entry_drawdown_review_pct": pol.entry_drawdown_review_pct,
            "concentration": concentration_block,
        },
        snapshot_time=snap,
        broker=broker,
    )


def is_risk_alert_status(status: str) -> bool:
    """손절/익절/혼합 알림 수준 — post-trade `POST_TRADE_RISK_ALERT` 판정용."""
    return status in (_RISK_STOP, _RISK_TAKE, _RISK_MIXED)


def count_risk_levels(positions: list[PositionRisk]) -> dict[str, int]:
    """포지션별 risk_level 집계."""
    counts = {
        "stop_loss_alert_count": 0,
        "take_profit_alert_count": 0,
        "warning_count": 0,
        "ok_count": 0,
    }
    for p in positions:
        if p.risk_level == _RISK_STOP:
            counts["stop_loss_alert_count"] += 1
        elif p.risk_level == _RISK_TAKE:
            counts["take_profit_alert_count"] += 1
        elif p.risk_level == _RISK_WARNING:
            counts["warning_count"] += 1
        else:
            counts["ok_count"] += 1
    return counts


def summarize_risk_result(
    result: RiskGuardResult,
    *,
    risk_report_path: str | None = None,
) -> dict[str, Any]:
    """runbook·CLI 공통 risk 요약 필드."""
    counts = count_risk_levels(result.positions)
    return {
        "risk_status": result.status,
        "stop_loss_alert_count": counts["stop_loss_alert_count"],
        "take_profit_alert_count": counts["take_profit_alert_count"],
        "warning_count": counts["warning_count"],
        "risk_report_path": risk_report_path or "",
        "risk_alerts": list(result.alerts),
    }


def run_portfolio_risk_check(
    db_path: str,
    *,
    broker: str = "kis",
    output_dir: str | Path = "outputs",
    policy: RiskGuardPolicy | None = None,
    write_report: bool = True,
) -> tuple[RiskGuardResult, dict[str, Any], Path | None, Path | None]:
    """
    DB `real_positions` 기준 리스크 평가·리포트.
    `risk-check` CLI와 post-trade runbook이 동일 로직을 공유한다.
    """
    rows, snap, equity = load_positions_from_db(db_path, broker=broker)
    result = evaluate_portfolio_risk(
        rows, policy, broker=broker, snapshot_time=snap, total_equity=equity
    )
    json_path: Path | None = None
    md_path: Path | None = None
    if write_report:
        json_path, md_path = write_risk_report(result, output_dir=output_dir)
    summary = summarize_risk_result(
        result,
        risk_report_path=json_path.as_posix() if json_path else None,
    )
    return result, summary, json_path, md_path


def load_positions_from_db(
    db_path: str,
    *,
    broker: str = "kis",
) -> tuple[list[dict[str, Any]], str | None]:
    """최신 `real_positions` 목록과 snapshot_time."""
    from deepsignal.storage.database import load_latest_real_positions

    from deepsignal.analysis.position_peak_tracker import enrich_positions_with_peaks
    from deepsignal.storage.database import load_latest_real_account_snapshot

    rows = enrich_positions_with_peaks(db_path, broker, load_latest_real_positions(db_path, broker=broker))
    snap = str(rows[0]["snapshot_time"]) if rows else None
    equity: float | None = None
    snap_row = load_latest_real_account_snapshot(db_path, broker=broker)
    if snap_row:
        try:
            equity = float(snap_row.get("total_equity")) if snap_row.get("total_equity") is not None else None
        except (TypeError, ValueError):
            equity = None
        if equity is None:
            try:
                equity = float(snap_row.get("cash") or 0) + float(snap_row.get("total_market_value") or 0)
            except (TypeError, ValueError):
                equity = None
    return rows, snap, equity


def write_risk_report(
    result: RiskGuardResult,
    *,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    """`outputs/risk_alert_*.json` 및 `outputs/RISK_ALERT.md` 저장."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ymd = now.strftime("%Y%m%d")
    hms = now.strftime("%H%M%S")
    json_path = root / f"risk_alert_{ymd}_{hms}.json"
    md_path = root / "RISK_ALERT.md"

    body: dict[str, Any] = {
        "timestamp": now.isoformat(timespec="seconds"),
        "status": result.status,
        "broker": result.broker,
        "snapshot_time": result.snapshot_time,
        "policy": result.policy,
        "alerts": result.alerts,
        "warnings": result.warnings,
        "positions": [asdict(p) for p in result.positions],
        "disclaimer": "This report does not place SELL orders. Manual review required.",
    }
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    _RISK_STATUS_KO = {
        "OK": "정상",
        "WARNING": "경고",
        "CRITICAL": "위험",
        "ALERT": "경보",
    }
    _RISK_LEVEL_KO = {
        "OK": "정상",
        "WARNING": "경고",
        "CRITICAL": "위험",
        "STOP_LOSS": "손절 도달",
        "TAKE_PROFIT": "익절 도달",
    }

    pol = result.policy
    sl = float(pol.get("stop_loss_pct", -0.07)) * 100
    tp = float(pol.get("take_profit_pct", 0.15)) * 100
    status_ko = _RISK_STATUS_KO.get(str(result.status), str(result.status))

    lines = [
        "# DeepSignal — 위험 경보",
        "",
        f"- 생성 시각: {body['timestamp']}",
        f"- 상태: **{status_ko}**",
        f"- 브로커: {result.broker}",
        f"- 스냅샷 시각: {result.snapshot_time or '(알 수 없음)'}",
        f"- 손절선: {sl:.2f}%",
        f"- 익절선: {tp:.2f}%",
        "",
        "| 종목코드 | 수량 | 평균단가 | 현재가 | 손익 | 수익률 | 상태 |",
        "|---------|------|---------|--------|------|--------|------|",
    ]
    for p in result.positions:
        pnl_s = f"{p.unrealized_pnl:,.0f}원" if p.unrealized_pnl is not None else "-"
        pct_s = f"{p.unrealized_pnl_pct * 100:+.2f}%" if p.unrealized_pnl_pct is not None else "-"
        avg_s = f"{p.avg_price:,.0f}원" if p.avg_price is not None else "-"
        cur_s = f"{p.current_price:,.0f}원" if p.current_price is not None else "-"
        risk_ko = _RISK_LEVEL_KO.get(str(p.risk_level), str(p.risk_level))
        lines.append(
            f"| {p.symbol} | {p.quantity} | {avg_s} | {cur_s} | {pnl_s} | {pct_s} | {risk_ko} |"
        )
    if not result.positions:
        lines.append("| (없음) | - | - | - | - | - | - |")

    lines.extend(["", "## 경보", ""])
    for a in result.alerts:
        lines.append(f"- {a}")
    if not result.alerts:
        lines.append("- (없음)")

    lines.extend(["", "## 경고", ""])
    for w in result.warnings:
        lines.append(f"- {w}")
    if not result.warnings:
        lines.append("- (없음)")

    lines.extend(
        [
            "",
            "## 참고사항",
            "",
            "- 이 리포트는 매도 주문을 자동 실행하지 않습니다.",
            "- 이상 징후 발견 시 직접 확인 후 수동으로 대응하세요.",
            "- 시장가 주문 및 자동 청산은 실행되지 않습니다.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def format_risk_console(result: RiskGuardResult) -> str:
    """CLI용 텍스트."""
    lines = ["DeepSignal risk check", f"Status: {result.status}", ""]
    for p in result.positions:
        pct = f"{p.unrealized_pnl_pct * 100:.2f}%" if p.unrealized_pnl_pct is not None else "n/a"
        pnl = f"{p.unrealized_pnl:.2f}" if p.unrealized_pnl is not None else "n/a"
        avg = f"{p.avg_price:.0f}" if p.avg_price is not None else "n/a"
        cur = f"{p.current_price:.0f}" if p.current_price is not None else "n/a"
        lines.append(
            f"{p.symbol} qty={p.quantity} avg={avg} current={cur} pnl={pnl} pnl_pct={pct}"
        )
        for a in p.alerts:
            lines.append(f"ALERT: {a}")
        if p.alerts:
            lines.append("")
    for a in result.alerts:
        if a not in "\n".join(lines):
            lines.append(f"ALERT: {a}")
    return "\n".join(lines).rstrip()
