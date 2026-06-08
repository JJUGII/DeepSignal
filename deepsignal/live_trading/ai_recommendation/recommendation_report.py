"""Markdown report rendering for AI live recommendations."""

from __future__ import annotations

from deepsignal.live_trading.ai_recommendation.recommendation_model import RecommendationRunResult


def _fmt_money(value: float | None, currency: str) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f} {currency}"


def render_recommendation_markdown(result: RecommendationRunResult) -> str:
    currency = result.config.currency
    by_action: dict[str, list] = {}
    for rec in result.recommendations:
        by_action.setdefault(rec.action, []).append(rec)

    lines = [
        "# DeepSignal — AI Live Trade Recommendation",
        "",
        "## 오늘 AI 실거래 추천 요약",
        "",
        f"- 생성 시각: {result.generated_at}",
        f"- 상태: {result.status}",
        f"- 브로커: {result.config.broker}",
        f"- 추천 수: {len(result.recommendations)}",
        f"- 주문안 포함 주문 수: {len(result.order_plan.get('orders') or [])}",
        "- 실주문 자동 실행: 없음",
        "- 최종 승인은 운영자가 직접 `live-approve --execute` 절차에서 입력해야 함",
        "",
        "## 계좌/리스크/거시 요약",
        "",
        f"- 계좌 스냅샷: {result.account_context.snapshot_time or 'NOT_AVAILABLE'}",
        f"- 스냅샷 stale: {result.account_context.stale_snapshot}",
        f"- 현금: {_fmt_money(result.account_context.cash, currency)}",
        f"- 평가/총자산: {_fmt_money(result.account_context.total_equity, currency)}",
        f"- Macro regime: {result.macro_context.get('market_regime', 'unknown')}",
        f"- Macro score: {result.macro_context.get('macro_score', '-')}",
        f"- Safety audit: {result.operational_risk_context.safety_audit_status}",
        f"- Reconcile: {result.operational_risk_context.reconcile_status}",
        f"- Risk status: {result.operational_risk_context.risk_status}",
        "",
        "## BUY/INCREASE 후보",
        "",
        "| Symbol | Action | Confidence | Priority | Qty | Limit | Est. Value | Plan | Breakdown | Gates | Reason |",
        "|--------|--------|------------|----------|-----|-------|------------|------|-----------|-------|--------|",
    ]
    for rec in by_action.get("BUY", []) + by_action.get("INCREASE", []):
        reason = rec.reason.replace("|", "\\|")
        disp = rec.score_breakdown.get("display") if isinstance(rec.score_breakdown, dict) else {}
        breakdown = (
            f"T{disp.get('technical', 'n/a')} N{disp.get('news', 'n/a')} "
            f"M{disp.get('macro', 'n/a')} F{disp.get('final', 'n/a')}"
            if disp
            else "-"
        )
        gates = ",".join(f"{k}:{v}" for k, v in sorted(rec.quality_gates.items())) if rec.quality_gates else "-"
        lines.append(
            f"| {rec.symbol} | {rec.action_label} | {rec.confidence:.2f} | {rec.priority} | "
            f"{rec.suggested_quantity} | {rec.suggested_limit_price or '-'} | "
            f"{rec.estimated_order_value:,.2f} | {rec.allowed_for_plan} | {breakdown} | {gates} | {reason} |"
        )
    if not (by_action.get("BUY") or by_action.get("INCREASE")):
        lines.append("| (none) | - | - | - | - | - | - | - | - | - | - |")

    lines.extend(["", "## 추천 근거 breakdown (BUY/INCREASE)", ""])
    buy_recs = by_action.get("BUY", []) + by_action.get("INCREASE", [])
    if buy_recs:
        for rec in buy_recs:
            disp = rec.score_breakdown.get("display", {}) if isinstance(rec.score_breakdown, dict) else {}
            lines.append(f"### {rec.symbol}")
            lines.append(f"- 기술점수: {disp.get('technical', 'n/a')}")
            lines.append(f"- 뉴스점수: {disp.get('news', 'n/a')}")
            lines.append(f"- 거시점수: {disp.get('macro', 'n/a')} ({disp.get('macro_regime', 'n/a')})")
            lines.append(f"- 합산 final: {disp.get('final', 'n/a')}")
            if rec.quality_gates:
                lines.append(f"- 품질 게이트: {', '.join(f'{k}={v}' for k, v in rec.quality_gates.items())}")
            if rec.risk_notes:
                lines.append(f"- 참고: {'; '.join(rec.risk_notes[:4])}")
            lines.append("")
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## SELL/REDUCE 후보",
            "",
            "SELL/REDUCE는 기본 주문안에서 제외됩니다. 현재 live-approve 승인 경로는 BUY/LIMIT 주문만 검증합니다.",
            "",
            "| Symbol | Action | Confidence | Current Qty | Current Value | Reason |",
            "|--------|--------|------------|-------------|---------------|--------|",
        ]
    )
    for rec in by_action.get("SELL", []) + by_action.get("REDUCE", []):
        reason = rec.reason.replace("|", "\\|")
        lines.append(
            f"| {rec.symbol} | {rec.action_label} | {rec.confidence:.2f} | {rec.current_quantity} | "
            f"{rec.current_value:,.2f} | {reason} |"
        )
    if not (by_action.get("SELL") or by_action.get("REDUCE")):
        lines.append("| (none) | - | - | - | - | - |")

    lines.extend(["", "## HOLD/SKIP 후보", "", "| Symbol | Action | Reason |", "|--------|--------|--------|"])
    for rec in by_action.get("HOLD", []) + by_action.get("SKIP", []):
        reason = rec.reason.replace("|", "\\|")
        lines.append(f"| {rec.symbol} | {rec.action_label} | {reason} |")
    if not (by_action.get("HOLD") or by_action.get("SKIP")):
        lines.append("| (none) | - | - |")

    lines.extend(["", "## 차단 사유", ""])
    blockers = result.operational_risk_context.blocked_reasons
    for rec in result.recommendations:
        blockers.extend(rec.blocked_reasons)
    unique_blockers = sorted(set(blockers))
    if unique_blockers:
        for item in unique_blockers:
            lines.append(f"- {item}")
    else:
        lines.append("- 없음")

    lines.extend(
        [
            "",
            "## live-approve 연결 방식",
            "",
            "아래 예시는 사람이 직접 검토 후 수동으로 입력하는 명령입니다. 스크립트/자동 실행으로 생성하지 않습니다.",
            "",
            "```bash",
            "python main.py live-approve --broker kis --plan outputs/live_order_plan_ai_YYYYMMDD_HHMMSS.json --approved",
            "python main.py live-approve --broker kis --plan outputs/live_order_plan_ai_YYYYMMDD_HHMMSS.json --approved --execute --allow-live-env --final-confirm I_UNDERSTAND_REAL_ORDER",
            "```",
            "",
            "## Safety Boundary",
            "",
            "- live-approve 자동 호출 없음",
            "- --execute 자동 호출 없음",
            "- KIS order-cash POST 없음",
            "- 시장가 주문 생성 없음",
            "- final-confirm 자동 주입 없음",
            "- 최종 승인은 운영자 수동 입력",
        ]
    )
    return "\n".join(lines) + "\n"
