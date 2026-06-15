"""No-recommendation diagnostics for crypto daily plan (BUY/SELL candidates)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from deepsignal.crypto_trading.crypto_universe import CryptoUniverseConfig, resolve_crypto_markets
from deepsignal.crypto_trading.crypto_order_plan import (
    CRYPTO_PLAN_JSON,
    CRYPTO_PLAN_MD,
    CryptoOrderPlan,
)
from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig
from deepsignal.crypto_trading.crypto_recommendation import MARKET_DISPLAY_KO
from deepsignal.crypto_trading.crypto_recommendation_quality import (
    CryptoRecommendationQualityConfig,
    apply_crypto_buy_quality_gates,
)
from deepsignal.crypto_trading.crypto_universe import market_display_name
from deepsignal.crypto_trading.crypto_signal_scorer import load_crypto_macro_context, score_crypto_market
from deepsignal.crypto_trading.crypto_sell_triggers import classify_crypto_sell_trigger
from deepsignal.crypto_trading.broker.interface import (
    MIN_ORDER_KRW,
    CryptoBroker,
    CryptoHolding,
    CryptoTicker,
)
from deepsignal.live_trading.time_utils import now_kst_iso, stamp_daily_ai_payload
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto

AtrAction = Literal["pass", "reduce", "block", "n/a"]


@dataclass
class CryptoBuyCandidateDiagnostic:
    market: str
    display_name: str
    current_price: float
    signed_change_rate: float
    acc_trade_price_24h: float
    score: float
    rsi: float | None
    rsi_pass: bool
    volume_ratio: float | None
    volume_pass: bool
    atr_pct: float | None
    atr_action: AtrAction
    quality_ok: bool
    size_multiplier: float
    technical_score: float | None = None
    macro_score: float | None = None
    final_score: float | None = None
    macro_regime: str = ""
    validation_gate: str = ""
    liquidity_gate: str = ""
    gate_passed: bool = False
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CryptoSellCandidateDiagnostic:
    market: str
    display_name: str
    quantity: float
    avg_buy_price: float
    current_price: float
    pnl_pct: float
    take_profit_pct: float
    stop_loss_pct: float
    take_profit_buffer_pct: float
    stop_loss_buffer_pct: float
    valuation_krw: float
    min_order_krw_pass: bool
    sell_trigger: str | None
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CryptoRecommendationDiagnostics:
    generated_at: str
    take_profit_pct: float
    stop_loss_pct: float
    take_profit_buffer_pct: float
    stop_loss_buffer_pct: float
    max_order_value: float
    holdings_summary: list[dict[str, Any]]
    buy_candidates: list[CryptoBuyCandidateDiagnostic]
    sell_candidates: list[CryptoSellCandidateDiagnostic]
    final_no_recommendation_reason: str
    final_summary_bullets: list[str] = field(default_factory=list)
    ticker_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "take_profit_pct": self.take_profit_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_buffer_pct": self.take_profit_buffer_pct,
            "stop_loss_buffer_pct": self.stop_loss_buffer_pct,
            "max_order_value": self.max_order_value,
            "holdings_summary": self.holdings_summary,
            "buy_candidates": [b.to_dict() for b in self.buy_candidates],
            "sell_candidates": [s.to_dict() for s in self.sell_candidates],
            "final_no_recommendation_reason": self.final_no_recommendation_reason,
            "final_summary_bullets": self.final_summary_bullets,
            "ticker_errors": self.ticker_errors,
        }


def _holding_summary(h: CryptoHolding) -> dict[str, Any]:
    return {
        "market": h.market,
        "display_name": MARKET_DISPLAY_KO.get(h.market, h.market),
        "quantity": h.available,
        "avg_buy_price": h.avg_buy_price,
        "current_price": h.current_price,
        "pnl_pct": h.pnl_pct,
        "valuation_krw": h.valuation_krw,
    }


def diagnose_buy_candidate(
    broker: CryptoBroker,
    ticker: UpbitTicker,
    *,
    buy_quality: CryptoBuyQualityConfig | None = None,
    order_krw: float = MIN_ORDER_KRW,
    macro_db_path: str | Path | None = None,
    output_dir: str | Path = "outputs",
    quality_config: CryptoRecommendationQualityConfig | None = None,
    macro_context: dict[str, Any] | None = None,
    total_portfolio_krw: float = 0.0,
    current_position_krw: float = 0.0,
) -> CryptoBuyCandidateDiagnostic:
    cfg = buy_quality if buy_quality is not None else CryptoBuyQualityConfig()
    gate_cfg = quality_config or CryptoRecommendationQualityConfig(
        output_dir=str(output_dir),
        min_volume_ratio=float(cfg.min_volume_ratio),
    )
    macro_ctx = macro_context if macro_context is not None else load_crypto_macro_context(macro_db_path)
    market = ticker.market
    display = market_display_name(market)
    blocked: list[str] = []

    if not cfg.enabled:
        return CryptoBuyCandidateDiagnostic(
            market=market,
            display_name=display,
            current_price=ticker.trade_price,
            signed_change_rate=ticker.signed_change_rate,
            acc_trade_price_24h=ticker.acc_trade_price_24h,
            score=0.0,
            rsi=None,
            rsi_pass=True,
            volume_ratio=None,
            volume_pass=True,
            atr_pct=None,
            atr_action="n/a",
            quality_ok=True,
            size_multiplier=1.0,
            gate_passed=True,
            macro_regime=str(macro_ctx.get("market_regime") or ""),
            blocked_reasons=[],
        )

    ms = score_crypto_market(
        broker, ticker, display_name=display, macro_context=macro_ctx, buy_quality=cfg
    )
    ok, reason, mult, raw = ms.quality_ok, ms.quality_reason, ms.size_multiplier, ms.quality_diag
    score = float(ms.final_score if ms.final_score is not None else ms.technical_score or 0.0)
    rsi = raw.get("rsi_14")
    if isinstance(rsi, (int, float)):
        rsi_f = float(rsi)
    else:
        rsi_f = None
    vol_ratio = raw.get("volume_ratio")
    if isinstance(vol_ratio, (int, float)):
        vol_f = float(vol_ratio)
    else:
        vol_f = None
    atr = raw.get("atr_pct")
    if isinstance(atr, (int, float)):
        atr_f = float(atr)
    else:
        atr_f = None

    _max_rsi = float(cfg.max_rsi)
    try:
        import os as _os_rsi
        _ov_rsi = _os_rsi.environ.get("CRYPTO_MAX_RSI", "").strip()
        if _ov_rsi:
            _max_rsi = max(_max_rsi, float(_ov_rsi))
    except ValueError:
        pass
    rsi_pass = rsi_f is None or rsi_f <= _max_rsi
    volume_pass = vol_f is None or vol_f >= float(cfg.min_volume_ratio)

    if not rsi_pass:
        blocked.append(f"RSI 과열 ({rsi_f:.1f} > {_max_rsi})")
    if not volume_pass:
        blocked.append(f"거래량 부족 (ratio {vol_f:.2f} < {cfg.min_volume_ratio})")

    if not ok and reason and reason not in blocked:
        blocked.append(reason)

    if not ok:
        atr_action: AtrAction = "block"
    elif atr_f is not None and atr_f > float(cfg.max_atr_pct):
        atr_action = "reduce"
        note = raw.get("volatility_note")
        if note:
            blocked.append(str(note))
    else:
        atr_action = "pass"

    allowed, gates, _bd, gate_blocked = apply_crypto_buy_quality_gates(
        ms,
        ticker=ticker,
        macro_context=macro_ctx,
        order_krw=float(order_krw),
        current_position_krw=float(current_position_krw),
        total_portfolio_krw=float(total_portfolio_krw),
        config=gate_cfg,
        output_dir=output_dir,
    )
    val_gate = str(gates.get("validation") or "")
    liq_gate = str(gates.get("liquidity") or "")
    for r in gate_blocked:
        if r not in blocked:
            blocked.append(r)
    if val_gate == "blocked":
        blocked.append(f"validation_gate:{val_gate}")
    if liq_gate == "blocked":
        blocked.append(f"liquidity_gate:{liq_gate}")

    return CryptoBuyCandidateDiagnostic(
        market=market,
        display_name=display,
        current_price=ticker.trade_price,
        signed_change_rate=ticker.signed_change_rate,
        acc_trade_price_24h=ticker.acc_trade_price_24h,
        score=score,
        rsi=rsi_f,
        rsi_pass=rsi_pass,
        volume_ratio=vol_f,
        volume_pass=volume_pass,
        atr_pct=atr_f,
        atr_action=atr_action,
        quality_ok=ok,
        size_multiplier=float(mult),
        technical_score=ms.technical_score,
        macro_score=ms.macro_score,
        final_score=ms.final_score,
        macro_regime=ms.macro_regime,
        validation_gate=val_gate,
        liquidity_gate=liq_gate,
        gate_passed=allowed,
        blocked_reasons=blocked,
    )


def diagnose_sell_candidate(
    h: CryptoHolding,
    *,
    take_profit_pct: float = _CRYPTO.take_profit_pct,
    stop_loss_pct: float = _CRYPTO.stop_loss_pct,
    take_profit_buffer_pct: float = _CRYPTO.take_profit_buffer_pct,
    stop_loss_buffer_pct: float = _CRYPTO.stop_loss_buffer_pct,
) -> CryptoSellCandidateDiagnostic:
    display = MARKET_DISPLAY_KO.get(h.market, h.market)
    min_pass = h.valuation_krw >= MIN_ORDER_KRW
    blocked: list[str] = []
    tp = float(take_profit_pct)
    sl = float(stop_loss_pct)
    tp_buf = float(take_profit_buffer_pct)
    sl_buf = float(stop_loss_buffer_pct)

    trigger = classify_crypto_sell_trigger(
        h.pnl_pct,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        take_profit_buffer_pct=tp_buf,
        stop_loss_buffer_pct=sl_buf,
    )

    if trigger == "near_take_profit":
        blocked.append(
            f"익절 근접 (수익률 {h.pnl_pct:+.2f}%, 기준 {tp:+.2f}%, buffer {tp_buf:.2f}%p)"
        )
    elif trigger == "near_stop_loss":
        blocked.append(
            f"손절 근접 (수익률 {h.pnl_pct:+.2f}%, 기준 {sl:+.2f}%, buffer {sl_buf:.2f}%p)"
        )
    elif trigger == "take_profit":
        blocked.append(f"익절 조건 충족 (수익률 {h.pnl_pct:+.2f}% >= {tp:+.2f}%)")
    elif trigger == "stop_loss":
        blocked.append(f"손절 조건 충족 (수익률 {h.pnl_pct:+.2f}% <= {sl:+.2f}%)")
    else:
        gap_tp = tp - h.pnl_pct
        gap_sl = h.pnl_pct - sl
        blocked.append(
            f"수익률 {h.pnl_pct:+.2f}% — 익절까지 {gap_tp:.2f}%p 부족 "
            f"(buffer {tp_buf:.2f}%p 적용 시 {tp - tp_buf:+.2f}% 이상 매도 제안)"
        )
        if gap_sl > sl_buf:
            blocked.append(f"손절까지 {gap_sl:.2f}%p 여유")

    if not min_pass:
        blocked.append(f"평가금액 {h.valuation_krw:,.0f}원 < 최소주문 {MIN_ORDER_KRW:,.0f}원")

    if trigger and not min_pass:
        blocked.append("최소주문금액 미달로 매도 불가")

    return CryptoSellCandidateDiagnostic(
        market=h.market,
        display_name=display,
        quantity=h.available,
        avg_buy_price=h.avg_buy_price,
        current_price=h.current_price,
        pnl_pct=h.pnl_pct,
        take_profit_pct=tp,
        stop_loss_pct=sl,
        take_profit_buffer_pct=tp_buf,
        stop_loss_buffer_pct=sl_buf,
        valuation_krw=h.valuation_krw,
        min_order_krw_pass=min_pass,
        sell_trigger=trigger if min_pass else None,
        blocked_reasons=blocked,
    )


def _summarize_execution_blocks(
    broker: CryptoBroker,
    pool: list["CryptoBuyCandidateDiagnostic"],
    *,
    order_krw: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    limit: int = 3,
) -> list[str]:
    from deepsignal.crypto_trading.crypto_execution_quality import (
        evaluate_pre_trade,
        should_block_entry_by_execution_quality,
    )

    bullets: list[str] = []
    ranked = sorted(pool, key=lambda b: float(b.final_score or b.score or 0.0), reverse=True)
    for cand in ranked[:limit]:
        report = evaluate_pre_trade(
            broker,
            market=cand.market,
            side="buy",
            order_krw=float(order_krw),
            take_profit_pct=float(take_profit_pct),
            stop_loss_pct=float(stop_loss_pct),
        )
        if not should_block_entry_by_execution_quality(report):
            continue
        hard = [
            r
            for r in report.reasons
            if any(k in r for k in ("미달", "실패", "과다", "R:R", "순이익", "손절", "스프레드"))
        ]
        detail = "; ".join(hard[:2]) if hard else "체결품질 미통과"
        bullets.append(f"{cand.display_name}({cand.market}): {detail}")
    return bullets


def _summarize_no_recommendation(
    *,
    sell_candidates: list[CryptoSellCandidateDiagnostic],
    buy_candidates: list[CryptoBuyCandidateDiagnostic],
    holdings_count: int,
    ticker_errors: list[str],
    take_profit_pct: float,
    take_profit_buffer_pct: float,
    stop_loss_pct: float = _CRYPTO.stop_loss_pct,
    broker: CryptoBroker | None = None,
    order_krw: float = 0.0,
) -> tuple[str, list[str]]:
    bullets: list[str] = []

    actionable_sell = [s for s in sell_candidates if s.sell_trigger and s.min_order_krw_pass]
    near_tp = [s for s in sell_candidates if s.sell_trigger == "near_take_profit" and s.min_order_krw_pass]
    near_tp_blocked = [
        s for s in sell_candidates if s.sell_trigger == "near_take_profit" and not s.min_order_krw_pass
    ]

    if holdings_count == 0:
        bullets.append("보유 코인 없음 (매도 후보 없음)")
    elif actionable_sell:
        bullets.append("매도 조건 충족 후보 있으나 선택되지 않음 (내부 로직 확인)")
    elif near_tp_blocked:
        parts = ", ".join(f"{s.display_name} {s.pnl_pct:+.2f}%" for s in near_tp_blocked)
        bullets.append(
            f"{parts} — 익절 기준 {take_profit_pct:+.2f}% 근접이나 최소주문금액 미달로 매도 보류"
        )
    else:
        held = [s for s in sell_candidates if s.min_order_krw_pass]
        if held:
            parts = ", ".join(f"{s.display_name} {s.pnl_pct:+.2f}%" for s in held)
            tp_near_line = (
                f"{parts} — 익절 기준 {take_profit_pct:+.2f}%에 근접 "
                f"(buffer {take_profit_buffer_pct:.2f}%p: "
                f"{take_profit_pct - take_profit_buffer_pct:+.2f}% 이상 시 매도 제안)"
            )
            bullets.append(tp_near_line)
        else:
            bullets.append("보유 코인 매도 조건 미충족 (또는 최소주문 미달)")

    if near_tp and not actionable_sell:
        bullets.append("익절 근접 조건으로 매도 후보 (선정 로직 확인 필요)")

    quality_ok_buys = [b for b in buy_candidates if b.quality_ok]
    if not buy_candidates:
        if ticker_errors:
            bullets.append("매수 후보 시세 조회 실패 (Upbit 429 등 — 재시도 후 일부 마켓만 실패 가능)")
            for err in ticker_errors[:3]:
                bullets.append(err)
        else:
            bullets.append("매수 후보 시세 조회 실패 또는 마켓 없음")
    elif not quality_ok_buys:
        vol_fails = [
            b
            for b in buy_candidates
            if not b.volume_pass and b.volume_ratio is not None
        ]
        if vol_fails:
            parts = ", ".join(
                f"{b.display_name} ratio {b.volume_ratio:.2f}" for b in vol_fails[:5]
            )
            bullets.append(f"신규 매수 후보 거래량 필터 미통과 ({parts})")
        else:
            bullets.append("신규 매수 후보 품질 필터 통과 실패 (RSI/ATR 등)")
    elif not [b for b in buy_candidates if b.gate_passed]:
        low_final = [
            b
            for b in buy_candidates
            if b.final_score is not None and b.validation_gate == "blocked"
        ]
        if low_final:
            parts = ", ".join(
                f"{b.display_name} final {b.final_score:.1f}" for b in low_final[:5]
            )
            bullets.append(f"validation gate 미통과 (final score) — {parts}")
        else:
            liq_fail = [b for b in buy_candidates if b.liquidity_gate == "blocked"]
            if liq_fail:
                parts = ", ".join(b.display_name for b in liq_fail[:5])
                bullets.append(f"liquidity gate 미통과 — {parts}")
            else:
                bullets.append("점수·게이트 통과 후보 없음 (macro risk-off 등)")
    else:
        positive = [b for b in buy_candidates if b.gate_passed and b.signed_change_rate > 0]
        pool = positive if positive else [b for b in buy_candidates if b.gate_passed]
        if not pool:
            bullets.append("게이트 통과 후보 없음")
        elif broker is not None and float(order_krw) > 0:
            exec_blocks = _summarize_execution_blocks(
                broker,
                pool,
                order_krw=float(order_krw),
                take_profit_pct=float(take_profit_pct),
                stop_loss_pct=float(stop_loss_pct),
            )
            if exec_blocks:
                bullets.append(
                    f"점수·게이트 통과 {len(pool)}건 — 체결품질(R:R·스프레드)로 최종 탈락"
                )
                bullets.extend(exec_blocks[:3])
            else:
                bullets.append(
                    "점수·게이트 통과 후보 있음 — 세션·과매매·집중도 등 선정 경로에서 소진"
                )
        else:
            bullets.append("매수 후보 풀은 있으나 최종 선정 실패 (확인 필요)")

    if ticker_errors and buy_candidates:
        bullets.append(f"시세 조회 오류 {len(ticker_errors)}건 (다른 마켓은 평가됨)")

    reason = "; ".join(bullets)
    return reason, bullets


def _collect_buy_candidate_diagnostics(
    broker: CryptoBroker,
    markets: tuple[str, ...] | None,
    *,
    buy_quality: CryptoBuyQualityConfig | None,
    max_order_value: float,
    macro_db_path: str | Path | None,
    output_dir: str | Path,
    universe_config: CryptoUniverseConfig | None = None,
) -> tuple[list[CryptoBuyCandidateDiagnostic], list[str]]:
    buy_candidates: list[CryptoBuyCandidateDiagnostic] = []
    ticker_errors: list[str] = []
    macro_ctx = load_crypto_macro_context(macro_db_path)
    order_krw = max(MIN_ORDER_KRW, float(max_order_value))
    from deepsignal.crypto_trading.crypto_position_sizing import portfolio_totals

    total_portfolio_krw, _available_krw, _hold_val = portfolio_totals(broker)
    hold = tuple(h.market for h in broker.get_crypto_holdings())
    hold_map = {h.market: h for h in broker.get_crypto_holdings()}
    if markets is not None:
        scan_markets = markets
    else:
        meta = resolve_crypto_markets(broker, config=universe_config, holdings_markets=hold)
        scan_markets = meta.markets
    try:
        from deepsignal.crypto_trading.crypto_universe import fetch_tickers_batched

        ticker_map = fetch_tickers_batched(
            broker,
            list(scan_markets),
            batch_size=int((universe_config or CryptoUniverseConfig()).ticker_batch_size),
        )
    except Exception as batch_err:
        ticker_map = {}
        ticker_errors.append(f"batch ticker: {type(batch_err).__name__}: {batch_err}")
    for m in scan_markets:
        ticker: UpbitTicker | None = ticker_map.get(m) if ticker_map else None
        if ticker is None:
            try:
                ticker = broker.get_ticker(m)
            except Exception as e:
                ticker_errors.append(f"{m}: {type(e).__name__}: {e}")
                continue
        buy_candidates.append(
            diagnose_buy_candidate(
                broker,
                ticker,
                buy_quality=buy_quality,
                order_krw=order_krw,
                macro_context=macro_ctx,
                output_dir=output_dir,
                total_portfolio_krw=total_portfolio_krw,
                current_position_krw=float(
                    getattr(hold_map.get(ticker.market), "valuation_krw", 0.0) or 0.0
                ),
            )
        )
    return buy_candidates, ticker_errors


def build_crypto_recommendation_diagnostics(
    broker: CryptoBroker,
    *,
    markets: tuple[str, ...] | None = None,
    universe_config: CryptoUniverseConfig | None = None,
    take_profit_pct: float = _CRYPTO.take_profit_pct,
    stop_loss_pct: float = _CRYPTO.stop_loss_pct,
    take_profit_buffer_pct: float = _CRYPTO.take_profit_buffer_pct,
    stop_loss_buffer_pct: float = _CRYPTO.stop_loss_buffer_pct,
    max_order_value: float = MIN_ORDER_KRW,
    buy_quality: CryptoBuyQualityConfig | None = None,
    macro_db_path: str | Path | None = None,
    output_dir: str | Path = "outputs",
) -> CryptoRecommendationDiagnostics:
    holdings = broker.get_crypto_holdings()
    holdings_summary = [_holding_summary(h) for h in holdings]
    sell_candidates = [
        diagnose_sell_candidate(
            h,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_buffer_pct=take_profit_buffer_pct,
            stop_loss_buffer_pct=stop_loss_buffer_pct,
        )
        for h in holdings
    ]

    buy_candidates, ticker_errors = _collect_buy_candidate_diagnostics(
        broker,
        markets,
        buy_quality=buy_quality,
        max_order_value=max_order_value,
        macro_db_path=macro_db_path,
        output_dir=output_dir,
        universe_config=universe_config,
    )

    reason, bullets = _summarize_no_recommendation(
        sell_candidates=sell_candidates,
        buy_candidates=buy_candidates,
        holdings_count=len(holdings),
        ticker_errors=ticker_errors,
        take_profit_pct=float(take_profit_pct),
        take_profit_buffer_pct=float(take_profit_buffer_pct),
        stop_loss_pct=float(stop_loss_pct),
        broker=broker,
        order_krw=max(MIN_ORDER_KRW, float(max_order_value)),
    )

    return CryptoRecommendationDiagnostics(
        generated_at=now_kst_iso(),
        take_profit_pct=float(take_profit_pct),
        stop_loss_pct=float(stop_loss_pct),
        take_profit_buffer_pct=float(take_profit_buffer_pct),
        stop_loss_buffer_pct=float(stop_loss_buffer_pct),
        max_order_value=float(max_order_value),
        holdings_summary=holdings_summary,
        buy_candidates=buy_candidates,
        sell_candidates=sell_candidates,
        final_no_recommendation_reason=reason,
        final_summary_bullets=bullets,
        ticker_errors=ticker_errors,
    )


def format_diagnostics_console(diag: CryptoRecommendationDiagnostics) -> str:
    lines = [
        "[DeepSignal] No crypto recommendation — diagnostics",
        f"Generated: {diag.generated_at}",
        "",
        "=== Holdings summary ===",
    ]
    if not diag.holdings_summary:
        lines.append("  (no holdings)")
    else:
        for h in diag.holdings_summary:
            lines.append(
                f"  {h['display_name']} ({h['market']}): qty={h['quantity']}, "
                f"pnl={h['pnl_pct']:+.2f}%, val={h['valuation_krw']:,.0f}원"
            )

    lines.extend(["", "=== BUY candidate diagnostics ==="])
    for b in diag.buy_candidates:
        rsi_s = f"{b.rsi:.1f}" if b.rsi is not None else "n/a"
        vol_s = f"{b.volume_ratio:.2f}" if b.volume_ratio is not None else "n/a"
        atr_s = f"{b.atr_pct:.2f}%" if b.atr_pct is not None else "n/a"
        final_s = f"{b.final_score:+.1f}" if b.final_score is not None else "n/a"
        lines.append(
            f"  {b.display_name} ({b.market}): price={b.current_price:,.0f}, "
            f"chg={b.signed_change_rate*100:+.2f}%, final={final_s}, gate={'PASS' if b.gate_passed else 'FAIL'}"
        )
        lines.append(
            f"    RSI {rsi_s} {'PASS' if b.rsi_pass else 'FAIL'} | "
            f"volume_ratio {vol_s} {'PASS' if b.volume_pass else 'FAIL'} | "
            f"ATR {atr_s} [{b.atr_action.upper()}] | "
            f"validation={b.validation_gate or 'n/a'} liquidity={b.liquidity_gate or 'n/a'}"
        )
        if b.blocked_reasons:
            for r in b.blocked_reasons:
                lines.append(f"    - {r}")

    lines.extend(["", "=== SELL candidate diagnostics ==="])
    if not diag.sell_candidates:
        lines.append("  (no holdings)")
    for s in diag.sell_candidates:
        trig = s.sell_trigger or "none"
        lines.append(
            f"  {s.display_name} ({s.market}): pnl={s.pnl_pct:+.2f}%, "
            f"trigger={trig}, min_order={'PASS' if s.min_order_krw_pass else 'FAIL'}"
        )
        if s.blocked_reasons:
            for r in s.blocked_reasons:
                lines.append(f"    - {r}")

    lines.extend(["", "=== Final ===", diag.final_no_recommendation_reason])
    return "\n".join(lines)


def format_diagnostics_markdown(diag: CryptoRecommendationDiagnostics) -> str:
    lines = [
        "# DeepSignal Crypto Daily Trade Plan",
        "",
        "- Status: **CRYPTO_PLAN_NO_RECOMMENDATION**",
        f"- Generated: {diag.generated_at}",
        f"- Take profit: {diag.take_profit_pct:+.2f}% | Stop loss: {diag.stop_loss_pct:+.2f}%",
        "",
        "## Holdings summary",
        "",
    ]
    if not diag.holdings_summary:
        lines.append("_No holdings._")
    else:
        lines.append("| Market | Name | Qty | Avg buy | Price | PnL% | Valuation KRW |")
        lines.append("|--------|------|-----|---------|-------|------|---------------|")
        for h in diag.holdings_summary:
            lines.append(
                f"| {h['market']} | {h['display_name']} | {h['quantity']} | "
                f"{h['avg_buy_price']:,.0f} | {h['current_price']:,.0f} | "
                f"{h['pnl_pct']:+.2f} | {h['valuation_krw']:,.0f} |"
            )

    lines.extend(["", "## BUY candidate diagnostics", ""])
    if not diag.buy_candidates:
        lines.append("_No buy candidates (ticker fetch failed or empty markets)._")
    else:
        lines.append(
            "| Market | Price | Chg% | 24h vol KRW | Score | RSI | RSI | Vol ratio | Vol | ATR% | ATR | Blocked |"
        )
        lines.append(
            "|--------|-------|------|-------------|-------|-----|-----|-----------|-----|------|-----|---------|"
        )
        for b in diag.buy_candidates:
            rsi = f"{b.rsi:.1f}" if b.rsi is not None else "n/a"
            vol = f"{b.volume_ratio:.2f}" if b.volume_ratio is not None else "n/a"
            atr = f"{b.atr_pct:.2f}" if b.atr_pct is not None else "n/a"
            blocked = "; ".join(b.blocked_reasons) if b.blocked_reasons else "—"
            lines.append(
                f"| {b.market} | {b.current_price:,.0f} | {b.signed_change_rate*100:+.2f} | "
                f"{b.acc_trade_price_24h:,.0f} | {b.score:.4f} | {rsi} | "
                f"{'PASS' if b.rsi_pass else 'FAIL'} | {vol} | "
                f"{'PASS' if b.volume_pass else 'FAIL'} | {atr} | {b.atr_action.upper()} | {blocked} |"
            )

    lines.extend(["", "## SELL candidate diagnostics", ""])
    if not diag.sell_candidates:
        lines.append("_No holdings._")
    else:
        lines.append(
            "| Market | PnL% | TP% | SL% | Valuation | Min order | Trigger | Blocked |"
        )
        lines.append("|--------|------|-----|-----|-----------|-----------|---------|---------|")
        for s in diag.sell_candidates:
            blocked = "; ".join(s.blocked_reasons) if s.blocked_reasons else "—"
            lines.append(
                f"| {s.market} | {s.pnl_pct:+.2f} | {s.take_profit_pct:+.2f} | "
                f"{s.stop_loss_pct:+.2f} | {s.valuation_krw:,.0f} | "
                f"{'PASS' if s.min_order_krw_pass else 'FAIL'} | {s.sell_trigger or '—'} | {blocked} |"
            )

    lines.extend(
        [
            "",
            "## Final no recommendation reason",
            "",
            diag.final_no_recommendation_reason,
            "",
        ]
    )
    if diag.final_summary_bullets:
        lines.append("### Summary")
        for b in diag.final_summary_bullets:
            lines.append(f"- {b}")
    if diag.ticker_errors:
        lines.append("")
        lines.append("### Ticker errors")
        for e in diag.ticker_errors:
            lines.append(f"- {e}")
    lines.append("")
    return "\n".join(lines)


def build_no_recommendation_telegram_message(diag: CryptoRecommendationDiagnostics) -> str:
    lines = [
        "[DeepSignal 코인]",
        "현재 매수·매도 추천이 없습니다.",
        "사유:",
    ]
    for b in diag.final_summary_bullets:
        lines.append(f"- {b}")
    if not diag.final_summary_bullets:
        lines.append(f"- {diag.final_no_recommendation_reason}")
    return "\n".join(lines)


def save_crypto_no_recommendation_artifacts(
    output_dir: str | Path,
    diagnostics: CryptoRecommendationDiagnostics,
) -> tuple[Path, Path]:
    """Write CRYPTO_ORDER_PLAN.json and CRYPTO_DAILY_TRADE_PLAN.md (no trade)."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)

    plan = CryptoOrderPlan(
        broker="upbit",
        market="",
        side="",
        status="CRYPTO_PLAN_NO_RECOMMENDATION",
        reason=diagnostics.final_no_recommendation_reason,
        created_at=diagnostics.generated_at,
        warnings=list(diagnostics.final_summary_bullets),
    )
    payload = stamp_daily_ai_payload(plan.to_dict())
    payload["diagnostics"] = diagnostics.to_dict()

    json_path = root / CRYPTO_PLAN_JSON
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_path = root / CRYPTO_PLAN_MD
    md_path.write_text(format_diagnostics_markdown(diagnostics), encoding="utf-8")
    return json_path, md_path
