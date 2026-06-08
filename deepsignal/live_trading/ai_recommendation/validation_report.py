"""Report rendering for AI recommendation validation."""

from __future__ import annotations

from deepsignal.live_trading.ai_recommendation.validation_model import ValidationResult


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.2f}%"


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.2f}"


def _num(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):,.4f}" if isinstance(value, float) else str(value)


def render_validation_markdown(result: ValidationResult) -> str:
    s = result.summary
    m = result.metrics
    a = result.advanced_metrics
    b = result.benchmark
    c = result.cost_model
    cs = result.cost_summary
    liq = result.liquidity_model
    liq_summary = liq.get("summary") or {}
    liq_config = liq.get("config") or {}
    fxm = result.fx_model
    fxs = fxm.get("summary") or {}
    pr = result.portfolio_risk
    lines = [
        "# DeepSignal — AI Recommendation Validation",
        "",
        "## 검증 요약",
        "",
        f"- 검증 기간: {s.get('start_date') or '-'} ~ {s.get('end_date') or '-'}",
        f"- 대상 종목: {', '.join(s.get('symbols') or []) if s.get('symbols') else '전체'}",
        f"- 초기 자금: {_money(m.get('initial_cash'))}",
        f"- 최종 평가액: {_money(m.get('final_equity'))}",
        f"- 총 수익률: {_pct(m.get('total_return'))}",
        f"- 최대 낙폭: {_pct(m.get('max_drawdown'))}",
        f"- 거래 수: {m.get('trade_count')}",
        f"- 승률: {_pct(m.get('win_rate'))}",
        f"- 평균 수익: {_money(m.get('average_profit'))}",
        f"- 평균 손실: {_money(m.get('average_loss'))}",
        f"- SELL/REDUCE 반영: {s.get('include_sell_reduce')}",
        "",
        "## 거래 비용 가정",
        "",
        f"- 비용 적용 여부: {c.get('enabled')}",
        f"- 수수료율: {_pct(c.get('commission_rate'))}",
        f"- 세금율: {_pct(c.get('tax_rate'))}",
        f"- 슬리피지: {c.get('slippage_bps')} bps",
        f"- 최소 주문금액: {_money(c.get('min_order_value'))} {c.get('currency', '')}",
        f"- 최대 주문금액: {_money(c.get('max_order_value')) if c.get('max_order_value') is not None else '-'}",
        f"- 통화: {c.get('currency')}",
        "",
        "## 비용 반영 성과",
        "",
        f"- 비용 차감 전 수익률: {_pct(a.get('gross_return_pct'))}",
        f"- 비용 차감 후 수익률: {_pct(a.get('net_return_pct'))}",
        f"- 총 수수료: {_money(cs.get('total_commission'))}",
        f"- 총 세금: {_money(cs.get('total_tax'))}",
        f"- 총 슬리피지 비용: {_money(cs.get('total_slippage_cost'))}",
        f"- 총 거래 비용: {_money(cs.get('total_trading_cost'))}",
        f"- 비용으로 인한 수익률 감소: {_pct(a.get('cost_drag_pct'))}",
        "",
        "## 비용으로 스킵된 거래",
        "",
        f"- 최소 주문금액 미만: {cs.get('skipped_by_min_order_count', 0)}",
        f"- 최대 주문금액 초과: {cs.get('skipped_by_max_order_count', 0)}",
        f"- 현금 부족: {cs.get('skipped_by_cash_count', 0)}",
        "",
        "## 유동성 제한 검증",
        "",
        f"- 유동성 제한 적용 여부: {liq_summary.get('enabled', False)}",
        f"- 거래량 제한 비율: {_pct(liq_config.get('liquidity_limit_pct'))}",
        f"- 최소 평균 거래량: {_num(liq_config.get('min_daily_volume'))}",
        f"- 최소 평균 거래대금: {_money(liq_config.get('min_daily_value'))}",
        f"- 평균 거래량 lookback: {liq_config.get('volume_lookback_days', 20)}일",
        f"- 유동성으로 축소된 거래 수: {liq_summary.get('adjusted_by_liquidity_count', 0)}",
        f"- 유동성으로 스킵된 거래 수: {liq_summary.get('skipped_by_liquidity_count', 0)}",
        f"- 유동성 데이터 부족 경고 수: {liq_summary.get('liquidity_unavailable_count', 0)}",
        f"- 유동성으로 감소한 주문가치: {_money(liq_summary.get('total_liquidity_reduced_value', 0.0))}",
        "",
        "### 유동성 제한 적용 참고",
        "",
        "- 거래량/거래대금 기준은 로컬 `market_prices.volume`에 의존합니다.",
        "- volume이 없거나 0이면 주문을 임의로 막지 않고 경고로 남깁니다.",
        "- 이 검증은 실제 호가 잔량이나 장중 체결 가능성을 보장하지 않습니다.",
        "",
        "## 통화 / 환율 검증",
        "",
        f"- 기준 통화: {fxs.get('base_currency') or (fxm.get('config') or {}).get('base_currency', '-')}",
        f"- FX rates file: {(fxm.get('config') or {}).get('fx_rates_path') or '-'}",
        f"- Symbol currency map: {(fxm.get('config') or {}).get('symbol_currency_map_path') or '-'}",
        f"- FX conversion count: {fxs.get('fx_conversion_count', 0)}",
        f"- FX unavailable warning count: {fxs.get('fx_unavailable_count', 0)}",
        f"- 외화 노출 비중: {_pct(fxs.get('foreign_currency_exposure_pct'))}",
        "",
        "### 통화별 현금",
        "",
        "| Currency | Cash |",
        "|----------|------|",
    ]
    for currency, amount in sorted((fxs.get("cash_by_currency") or {}).items()):
        lines.append(f"| {currency} | {_money(amount)} |")
    if not fxs.get("cash_by_currency"):
        lines.append("| (none) | - |")
    lines.extend(["", "### 통화별 포지션 평가액", "", "| Currency | Position Value |", "|----------|----------------|"])
    for currency, amount in sorted((fxs.get("position_value_by_currency") or {}).items()):
        lines.append(f"| {currency} | {_money(amount)} |")
    if not fxs.get("position_value_by_currency"):
        lines.append("| (none) | - |")
    lines.extend(
        [
            "",
            "### 환율 리스크 적용 참고",
            "",
            "- FX rate는 로컬 JSON 또는 `--fallback-fx` 값만 사용하며 외부 환율 API를 호출하지 않습니다.",
            "- 환율 누락 시 fallback을 쓰거나 1.0으로 처리하고 warning을 남깁니다.",
            "- 환전 수수료, 세금, 실제 체결 환율은 이번 검증에서 별도 모델링하지 않습니다.",
        "",
        "## 포트폴리오 리스크 검증",
        "",
        f"- 집중도 점수: {_num(pr.get('concentration_score'))}",
        f"- 분산 점수: {_num(pr.get('diversification_score'))}",
        f"- 리스크 상태: {pr.get('severity', 'ok')}",
        f"- 단일 종목 최대 허용 비중: {_pct((pr.get('config') or {}).get('max_symbol_weight'))}",
        f"- 섹터 최대 허용 비중: {_pct((pr.get('config') or {}).get('max_sector_weight'))}",
        f"- 상관계수 경고 기준: {_num((pr.get('config') or {}).get('high_correlation_threshold'))}",
        "",
        "### 단일 종목 비중",
        "",
        "| Symbol | Weight | Threshold |",
        "|--------|--------|-----------|",
    ]
    )
    for symbol, weight in sorted((pr.get("symbol_weights") or {}).items()):
        lines.append(f"| {symbol} | {_pct(weight)} | {_pct((pr.get('config') or {}).get('max_symbol_weight'))} |")
    if not pr.get("symbol_weights"):
        lines.append("| (none) | - | - |")
    lines.extend(["", "### 섹터 비중", "", "| Sector | Weight | Threshold |", "|--------|--------|-----------|"])
    for sector, weight in sorted((pr.get("sector_weights") or {}).items()):
        lines.append(f"| {sector} | {_pct(weight)} | {_pct((pr.get('config') or {}).get('max_sector_weight'))} |")
    if not pr.get("sector_weights"):
        lines.append("| (none) | - | - |")
    lines.extend(["", "### 초과 비중 종목", "", "| Symbol | Weight | Threshold | Severity |", "|--------|--------|-----------|----------|"])
    for row in pr.get("overweight_symbols") or []:
        lines.append(f"| {row.get('symbol')} | {_pct(row.get('weight'))} | {_pct(row.get('threshold'))} | {row.get('severity')} |")
    if not pr.get("overweight_symbols"):
        lines.append("| (none) | - | - | ok |")
    lines.extend(["", "### 초과 비중 섹터", "", "| Sector | Weight | Threshold | Severity |", "|--------|--------|-----------|----------|"])
    for row in pr.get("overweight_sectors") or []:
        lines.append(f"| {row.get('sector')} | {_pct(row.get('weight'))} | {_pct(row.get('threshold'))} | {row.get('severity')} |")
    if not pr.get("overweight_sectors"):
        lines.append("| (none) | - | - | ok |")
    lines.extend(["", "### 고상관 종목쌍", "", "| Symbol A | Symbol B | Correlation | Points | Severity |", "|----------|----------|-------------|--------|----------|"])
    for row in pr.get("high_correlation_pairs") or []:
        lines.append(f"| {row.get('symbol_a')} | {row.get('symbol_b')} | {_num(row.get('correlation'))} | {row.get('points')} | {row.get('severity')} |")
    if not pr.get("high_correlation_pairs"):
        lines.append("| (none) | - | - | - | ok |")
    lines.extend(
        [
            "",
            "### 포트폴리오 리스크 적용 참고",
            "",
            "- `blocked`는 검증 리포트상의 강한 경고이며 주문 실행 차단 기능이 아닙니다.",
            "- 섹터가 `UNKNOWN`이면 `--sector-map` 로컬 JSON을 보강해 해석 품질을 높입니다.",
            "- 상관관계는 로컬 `market_prices` 일별 수익률 기준이며 데이터 포인트가 부족하면 경고로 표시됩니다.",
        "",
        "## 고급 성과 지표",
        "",
        f"- 연환산 수익률: {_pct(a.get('annualized_return_pct'))}",
        f"- 변동성: {_pct(a.get('volatility_pct'))}",
        f"- Sharpe Ratio: {_num(a.get('sharpe_ratio'))}",
        f"- Profit Factor: {_num(a.get('profit_factor'))}",
        f"- Expectancy: {_money(a.get('expectancy'))}",
        f"- Win Rate / Loss Rate: {_pct(a.get('win_rate'))} / {_pct(a.get('loss_rate'))}",
        f"- Exposure Ratio: {_pct(a.get('exposure_ratio'))}",
        f"- Turnover Ratio: {_pct(a.get('turnover_ratio'))}",
        f"- 평균 보유일: {_num(a.get('average_holding_days'))}",
        "",
        "## 벤치마크 비교",
        "",
    ]
    )
    if b.get("available"):
        lines.extend(
            [
                f"- Benchmark final equity: {_money(b.get('benchmark_final_equity'))}",
                f"- Benchmark return: {_pct(b.get('benchmark_return_pct'))}",
                f"- Benchmark net return: {_pct(b.get('benchmark_net_return_pct'))}",
                f"- Benchmark max drawdown: {_pct(b.get('benchmark_max_drawdown_pct'))}",
                f"- Benchmark costs applied: {b.get('benchmark_costs_applied')}",
                f"- Benchmark total cost: {_money(b.get('benchmark_total_cost'))}",
                f"- Excess return: {_pct(b.get('excess_return_pct'))}",
                f"- Strategy vs benchmark: {b.get('strategy_vs_benchmark')}",
            ]
        )
    else:
        lines.append(f"- Benchmark unavailable: {b.get('reason', 'unknown')}")
    lines.extend(
        [
            "",
            "## 최대 낙폭 구간",
            "",
            f"- 최대 낙폭: {_pct(a.get('max_drawdown_pct'))}",
            f"- 시작: {a.get('max_drawdown_start') or '-'}",
            f"- 종료: {a.get('max_drawdown_end') or '-'}",
            "",
            "## 연속 손실 위험",
            "",
            f"- 최대 연속 수익 거래: {a.get('consecutive_wins_max')}",
            f"- 최대 연속 손실 거래: {a.get('consecutive_losses_max')}",
            f"- Best trade: {a.get('best_trade') or '-'}",
            f"- Worst trade: {a.get('worst_trade') or '-'}",
        "",
        "## action별 성과",
        "",
        "| Action | Trades | Buy Value | Sell Value | Quantity | Realized PnL |",
        "|--------|--------|-----------|------------|----------|--------------|",
        ]
    )
    for action, row in sorted(result.action_breakdown.items()):
        lines.append(
            f"| {action} | {row.get('trades', 0)} | {_money(row.get('buy_value', 0.0))} | "
            f"{_money(row.get('sell_value', 0.0))} | {row.get('quantity', 0)} | {_money(row.get('realized_pnl', 0.0))} |"
        )
    if not result.action_breakdown:
        lines.append("| (none) | 0 | 0 | 0 | 0 |")

    lines.extend(["", "## Symbol별 손익", "", "| Symbol | Trades | Buy Value | Sell Value | Realized PnL | Open Qty | Open Value |", "|--------|--------|-----------|------------|--------------|----------|------------|"])
    for symbol, row in sorted(result.symbol_breakdown.items()):
        lines.append(
            f"| {symbol} | {row.get('trades', 0)} | {_money(row.get('buy_value', 0.0))} | "
            f"{_money(row.get('sell_value', 0.0))} | {_money(row.get('realized_pnl', 0.0))} | {row.get('open_quantity', 0)} | {_money(row.get('open_value', 0.0))} |"
        )
    if not result.symbol_breakdown:
        lines.append("| (none) | 0 | 0 | 0 | 0 | 0 |")

    lines.extend(
        [
            "",
            "## risk_off 기간 성과",
            "",
            f"- risk_off 거래 수: {m.get('risk_off_trade_count', 0)}",
            "",
            "## 실전 적용 판단 참고",
            "",
            "- Sharpe Ratio가 낮거나 최대 낙폭이 큰 경우 실거래 후보로 쓰기 전에 정책을 보수적으로 조정해야 합니다.",
            "- Profit Factor와 Expectancy는 닫힌 거래 기준이므로 거래 수가 적으면 신뢰도가 낮습니다.",
            "- Benchmark 대비 초과 수익이 지속적으로 양수인지 기간/종목을 바꿔 확인해야 합니다.",
            "",
            "## 데이터 부족/검증 한계",
            "",
            "- 종가 기반 근사 체결이며 호가, 슬리피지, 수수료, 세금, 유동성은 단순화되어 있습니다.",
            "- signals/market_prices DB에 저장된 과거 데이터 품질에 의존합니다.",
            "- 이 검증은 수익 보장 기능이 아니며 실계좌 주문과 무관합니다.",
            "",
            "## 실거래 적용 전 주의사항",
            "",
            "- `ai-live-recommend`를 실거래 후보 생성에 쓰기 전 충분한 기간과 종목군으로 검증해야 합니다.",
            "- 결과가 좋아도 최종 주문은 기존 `live-approve --execute` 수동 승인 절차에서만 가능합니다.",
            "- 이 명령은 KIS, live-approve, --execute, paper_* 운영 테이블을 호출/수정하지 않습니다.",
            "",
            "## Warnings",
            "",
        ]
    )
    if result.warnings:
        for warning in result.warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"
