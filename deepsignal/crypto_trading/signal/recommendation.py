"""Crypto buy/sell recommendations — holdings PnL, scoring, and quality gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_market_data import DEFAULT_CRYPTO_MARKETS
from deepsignal.crypto_trading.crypto_universe import (
    CryptoUniverseConfig,
    market_display_name,
    resolve_crypto_markets,
    save_crypto_universe_snapshot,
)
from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig
from deepsignal.crypto_trading.crypto_recommendation_quality import (
    CryptoRecommendationQualityConfig,
    apply_crypto_buy_quality_gates,
    build_sell_quality_gates,
)
from deepsignal.crypto_trading.crypto_signal_scorer import (
    load_crypto_macro_context,
    score_crypto_market,
)
from deepsignal.crypto_trading.crypto_sell_pricing import compute_sell_limit_price
from deepsignal.crypto_trading.crypto_sell_triggers import (
    SellTrigger,
    classify_crypto_sell_trigger,
    sell_trigger_priority,
)
from deepsignal.crypto_trading.crypto_execution_quality import (
    apply_execution_quality_to_buy_amount,
    effective_min_order_krw,
    should_block_entry_by_execution_quality,
)
from deepsignal.crypto_trading.upbit_broker import MIN_ORDER_KRW, CryptoHolding, UpbitBroker, UpbitTicker
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto

MARKET_DISPLAY_KO = {
    "KRW-BTC": "비트코인",
    "KRW-ETH": "이더리움",
    "KRW-XRP": "리플",
}


def _truthy_env(name: str, default: str = "false") -> bool:
    import os

    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _live_auto_crypto_buy_requires_ml_gate() -> bool:
    """실거래 무승인 BUY에서는 ML 게이트 fail-open을 기본 차단한다."""
    if _truthy_env("DEEPSIGNAL_ALLOW_CRYPTO_ML_FAIL_OPEN"):
        return False
    auto_on = _truthy_env("CRYPTO_AUTO_EXECUTE_WITHOUT_APPROVAL") or _truthy_env(
        "DEEPSIGNAL_CRYPTO_AUTO_EXECUTE"
    )
    paper = _truthy_env("CRYPTO_PAPER_MODE")
    dry_run = _truthy_env("UPBIT_DRY_RUN")
    require = _truthy_env("CRYPTO_REQUIRE_ML_GATE_FOR_LIVE_BUY", "true")
    return bool(auto_on and not paper and not dry_run and require)


def _ml_result_failed_open(ml_result: Any) -> bool:
    status = str(getattr(ml_result, "status", "") or "").lower()
    mode = str(getattr(ml_result, "ensemble_mode", "") or "").lower()
    return status in {
        "disabled",
        "skipped",
        "no_model",
        "degenerate_failopen",
        "error",
    } or mode == "off"


@dataclass
class CryptoRecommendation:
    market: str
    display_name: str
    side: str
    krw_amount: float
    current_price: float
    reason: str
    signed_change_rate: float = 0.0
    acc_trade_price_24h: float = 0.0
    volume: float = 0.0
    avg_buy_price: float = 0.0
    pnl_pct: float = 0.0
    sell_trigger: str | None = None
    take_profit_pct: float = 0.0
    stop_loss_pct: float = 0.0
    technical_score: float | None = None
    macro_score: float | None = None
    final_score: float | None = None
    macro_regime: str = ""
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    quality_gates: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "display_name": self.display_name,
            "side": self.side,
            "krw_amount": self.krw_amount,
            "current_price": self.current_price,
            "reason": self.reason,
            "signed_change_rate": self.signed_change_rate,
            "acc_trade_price_24h": self.acc_trade_price_24h,
            "volume": self.volume,
            "avg_buy_price": self.avg_buy_price,
            "pnl_pct": self.pnl_pct,
            "sell_trigger": self.sell_trigger,
            "take_profit_pct": self.take_profit_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "technical_score": self.technical_score,
            "macro_score": self.macro_score,
            "final_score": self.final_score,
            "macro_regime": self.macro_regime,
            "score_breakdown": dict(self.score_breakdown),
            "quality_gates": dict(self.quality_gates),
        }


def _format_score_reason(breakdown: dict[str, Any], gates: dict[str, str]) -> str:
    disp = breakdown.get("display") if isinstance(breakdown.get("display"), dict) else {}
    parts = [
        f"tech {disp.get('technical', 'n/a')}",
        f"macro {disp.get('macro', 'n/a')}",
        f"final {disp.get('final', 'n/a')}",
        f"regime {disp.get('macro_regime', 'n/a')}",
    ]
    gate_bits = [
        f"{k}:{v}"
        for k, v in sorted(gates.items())
        if k in ("validation", "liquidity", "concentration")
    ]
    if gate_bits:
        parts.append("gates " + ",".join(gate_bits))
    return "; ".join(parts)


def _holding_to_sell_rec(
    h: CryptoHolding,
    *,
    trigger: SellTrigger,
    take_profit_pct: float,
    stop_loss_pct: float,
    macro_context: dict[str, Any],
) -> CryptoRecommendation:
    name = MARKET_DISPLAY_KO.get(h.market, h.market)
    sell_limit = compute_sell_limit_price(
        h, trigger, take_profit_pct=take_profit_pct, stop_loss_pct=stop_loss_pct
    )
    if trigger == "take_profit":
        reason = (
            f"수익률 {h.pnl_pct:+.2f}%로 익절 조건 도달 — 지정가 {sell_limit:,.0f}원 "
            f"(목표 {take_profit_pct:+.2f}%)"
        )
    elif trigger == "near_take_profit":
        reason = (
            f"수익률 {h.pnl_pct:+.2f}% — 익절 지정가 {sell_limit:,.0f}원 "
            f"(목표 {take_profit_pct:+.2f}%)"
        )
    elif trigger == "overweight_reduce":
        reason = (
            f"다종목 보유 중 집중도 과다 — {name} 수익률 {h.pnl_pct:+.2f}%, "
            f"현재가 근처 지정가 {sell_limit:,.0f}원 매도 검토"
        )
    elif trigger == "stop_loss":
        reason = f"손절 기준 도달 (수익률 {h.pnl_pct:+.2f}%)"
    else:
        reason = (
            f"수익률 {h.pnl_pct:+.2f}% — 손절 기준 {stop_loss_pct:+.2f}%에 근접; "
            "일부 또는 전량 매도 검토"
        )
    gates, breakdown, _blocked = build_sell_quality_gates(
        h, trigger=trigger, macro_context=macro_context
    )
    reason += f" ({_format_score_reason(breakdown, gates)})"
    macro = macro_context.get("macro_score")
    try:
        macro_f = float(macro) if macro is not None else None
    except (TypeError, ValueError):
        macro_f = None

    return CryptoRecommendation(
        market=h.market,
        display_name=name,
        side="sell",
        krw_amount=h.valuation_krw,
        current_price=sell_limit,
        reason=reason,
        volume=h.total_quantity,
        avg_buy_price=h.avg_buy_price,
        pnl_pct=h.pnl_pct,
        sell_trigger=trigger,
        take_profit_pct=float(take_profit_pct),
        stop_loss_pct=float(stop_loss_pct),
        signed_change_rate=0.0,
        acc_trade_price_24h=0.0,
        macro_score=macro_f,
        macro_regime=str(macro_context.get("market_regime") or ""),
        score_breakdown=breakdown,
        quality_gates=gates,
    )


def _sell_rec_from_execution_exit(
    holding: CryptoHolding,
    exit_dec: Any,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    macro_context: dict[str, Any],
) -> CryptoRecommendation:
    frac = max(0.0, min(1.0, float(exit_dec.volume_fraction or 0.0)))
    vol = max(0.0, float(holding.available) * frac)
    name = MARKET_DISPLAY_KO.get(holding.market, market_display_name(holding.market))
    trigger = str(exit_dec.reason)  # SellTrigger-compatible
    gates, breakdown, _blocked = build_sell_quality_gates(
        holding, trigger=trigger, macro_context=macro_context  # type: ignore[arg-type]
    )
    breakdown = {
        **breakdown,
        "execution_exit": exit_dec.to_dict(),
        "sell_volume_fraction": frac,
        "win_probability": exit_dec.win_probability,
    }
    return CryptoRecommendation(
        market=holding.market,
        display_name=name,
        side="sell",
        krw_amount=vol * float(exit_dec.limit_price),
        current_price=float(exit_dec.limit_price),
        reason=exit_dec.message,
        volume=vol,
        avg_buy_price=holding.avg_buy_price,
        pnl_pct=holding.pnl_pct,
        sell_trigger=str(exit_dec.reason),
        take_profit_pct=float(take_profit_pct),
        stop_loss_pct=float(stop_loss_pct),
        macro_regime=str(macro_context.get("market_regime") or ""),
        score_breakdown=breakdown,
        quality_gates=gates,
    )


def build_sell_recommendation(
    broker: UpbitBroker,
    *,
    take_profit_pct: float = _CRYPTO.take_profit_pct,
    stop_loss_pct: float = _CRYPTO.stop_loss_pct,
    take_profit_buffer_pct: float = _CRYPTO.take_profit_buffer_pct,
    stop_loss_buffer_pct: float = _CRYPTO.stop_loss_buffer_pct,
    overweight_reduce_trigger_pct: float = _CRYPTO.overweight_reduce_trigger_pct,
    macro_db_path: str | Path | None = None,
    runner_state: dict[str, Any] | None = None,
    output_dir: str | Path = "outputs",
) -> CryptoRecommendation | None:
    """SELL if holding hits take-profit, stop-loss, or buffer-near thresholds."""
    from deepsignal.crypto_trading.crypto_overtrading_guards import (
        OvertradingGuardConfig,
        near_take_profit_allowed,
        sell_blocked_by_min_hold,
    )
    from deepsignal.crypto_trading.crypto_execution_engine import (
        execution_engine_enabled,
        scan_dynamic_exit_holdings,
    )
    from deepsignal.crypto_trading.crypto_gate_config import sell_rule_fallback_only

    guard_cfg = OvertradingGuardConfig()
    macro_context = load_crypto_macro_context(macro_db_path)

    if execution_engine_enabled() and runner_state is not None:
        dyn = scan_dynamic_exit_holdings(
            broker,
            runner_state=runner_state,
            output_dir=output_dir,
        )
        if dyn is not None:
            for h in broker.get_crypto_holdings():
                if h.market.upper() != dyn.market.upper():
                    continue
                if h.valuation_krw < MIN_ORDER_KRW and dyn.reason != "partial_take_profit":
                    continue
                blocked, _ = sell_blocked_by_min_hold(
                    runner_state,
                    market=h.market,
                    sell_trigger=dyn.reason,
                    cfg=guard_cfg,
                )
                if blocked:
                    break
                return _sell_rec_from_execution_exit(
                    h,
                    dyn,
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct,
                    macro_context=macro_context,
                )
    candidates: list[tuple[CryptoHolding, SellTrigger, float]] = []
    holdings = broker.get_crypto_holdings()
    for h in holdings:
        if h.valuation_krw < MIN_ORDER_KRW:
            continue
        trigger = classify_crypto_sell_trigger(
            h.pnl_pct,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_buffer_pct=take_profit_buffer_pct,
            stop_loss_buffer_pct=stop_loss_buffer_pct,
        )
        if trigger is None:
            continue
        if execution_engine_enabled() and sell_rule_fallback_only():
            if trigger not in ("take_profit", "stop_loss"):
                continue
        if trigger == "near_take_profit" and not near_take_profit_allowed(h.pnl_pct, cfg=guard_cfg):
            continue
        if runner_state is not None:
            blocked, _ = sell_blocked_by_min_hold(
                runner_state,
                market=h.market,
                sell_trigger=trigger,
                cfg=guard_cfg,
            )
            if blocked:
                continue
        sort_metric = h.pnl_pct if trigger in ("take_profit", "near_take_profit") else -abs(h.pnl_pct)
        candidates.append((h, trigger, sort_metric))

    # Multi-holding concentration trim only (single-coin scalping skips 100% weight).
    if not candidates and len(holdings) > 1:
        total_hold = sum(max(0.0, float(h.valuation_krw or 0.0)) for h in holdings)
        if total_hold > 0:
            most = max(holdings, key=lambda h: float(h.valuation_krw or 0.0))
            pct = float(most.valuation_krw or 0.0) / total_hold
            if pct >= float(overweight_reduce_trigger_pct) and float(most.valuation_krw or 0.0) >= MIN_ORDER_KRW:
                if float(most.pnl_pct or 0.0) >= float(guard_cfg.near_take_profit_min_pnl_pct):
                    if runner_state is not None:
                        blocked, _ = sell_blocked_by_min_hold(
                            runner_state,
                            market=most.market,
                            sell_trigger="overweight_reduce",
                            cfg=guard_cfg,
                        )
                        if blocked:
                            return None
                    return _holding_to_sell_rec(
                        most,
                        trigger="overweight_reduce",
                        take_profit_pct=take_profit_pct,
                        stop_loss_pct=stop_loss_pct,
                        macro_context=macro_context,
                    )

    if not candidates:
        return None

    best = max(
        candidates,
        key=lambda row: (sell_trigger_priority(row[1]), row[2]),
    )
    return _holding_to_sell_rec(
        best[0],
        trigger=best[1],
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        macro_context=macro_context,
    )


def build_crypto_recommendation(
    broker: UpbitBroker,
    *,
    take_profit_pct: float = _CRYPTO.take_profit_pct,
    stop_loss_pct: float = _CRYPTO.stop_loss_pct,
    markets: tuple[str, ...] | None = None,
    universe_config: CryptoUniverseConfig | None = None,
    max_order_value: float = MIN_ORDER_KRW,
    min_order_value: float | None = None,
    exclude_markets: tuple[str, ...] | None = None,
    prefer_non_holding_buy: bool = _CRYPTO.prefer_non_holding_buy,
    buy_quality: CryptoBuyQualityConfig | None = None,
    quality_config: CryptoRecommendationQualityConfig | None = None,
    macro_db_path: str | Path | None = None,
    output_dir: str | Path = "outputs",
    runner_state: dict[str, Any] | None = None,
) -> CryptoRecommendation | None:
    floor_krw = float(min_order_value if min_order_value is not None else effective_min_order_krw())
    krw_amount = max(floor_krw, float(max_order_value))
    quality_cfg = buy_quality if buy_quality is not None else CryptoBuyQualityConfig()
    gate_cfg = quality_config or CryptoRecommendationQualityConfig(
        output_dir=str(output_dir),
        min_volume_ratio=float(quality_cfg.min_volume_ratio),
    )
    macro_context = load_crypto_macro_context(macro_db_path)
    holdings = broker.get_crypto_holdings()
    hold_markets = tuple(h.market for h in holdings)
    hold_map = {h.market: h for h in holdings}
    excluded = {m.upper() for m in (exclude_markets or ())}
    scan_mode = "fixed"
    if markets is not None:
        scan_markets = markets
        display_names: dict[str, str] = {m: market_display_name(m) for m in scan_markets}
        universe_meta = None
        ticker_map = {}
        for m in scan_markets:
            try:
                ticker_map[m] = broker.get_ticker(m)
            except Exception:
                continue
    else:
        from deepsignal.crypto_trading.crypto_live_universe import resolve_crypto_markets_live_first
        from deepsignal.crypto_trading.crypto_universe import resolve_crypto_markets
        ucfg = universe_config or CryptoUniverseConfig()
        live_meta, scan_mode = resolve_crypto_markets_live_first(
            broker,
            config=ucfg,
            holdings_markets=hold_markets,
            output_dir=output_dir,
        )
        if live_meta is not None:
            universe_meta = live_meta
            scan_markets = universe_meta.markets
            display_names = universe_meta.display_names
            from deepsignal.crypto_trading.crypto_live_universe import tickers_from_live_state
            from deepsignal.crypto_trading.crypto_universe import get_upbit_krw_market_set

            valid_upbit = get_upbit_krw_market_set(broker, output_dir=output_dir)
            scan_markets = tuple(m for m in scan_markets if m in valid_upbit)
            ticker_map = tickers_from_live_state(
                output_dir,
                max_markets=max(int(ucfg.max_buy_scan_markets) * 3, 50),
                valid_upbit_markets=valid_upbit,
            )
            ticker_map = {m: ticker_map[m] for m in scan_markets if m in ticker_map}
            # live_state 합성 티커(acc_trade_price_24h=고정값)는 거래량 비율 계산 오류 유발.
            # Upbit 배치 티커로 실제 acc_trade_price_24h / signed_change_rate를 업데이트.
            try:
                from deepsignal.crypto_trading.crypto_universe import fetch_tickers_batched
                real_tickers = fetch_tickers_batched(
                    broker,
                    list(ticker_map.keys()),
                    batch_size=100,
                    valid_markets=valid_upbit,
                )
                ticker_map.update(real_tickers)
            except Exception:
                pass
            for m in scan_markets:
                if m not in ticker_map:
                    try:
                        ticker_map[m] = broker.get_ticker(m)
                    except Exception:
                        pass
        else:
            universe_meta = resolve_crypto_markets(
                broker,
                config=ucfg,
                holdings_markets=hold_markets,
            )
            scan_markets = universe_meta.markets
            display_names = universe_meta.display_names
            try:
                from deepsignal.crypto_trading.crypto_universe import (
                    fetch_tickers_batched,
                    get_upbit_krw_market_set,
                )

                valid_upbit = get_upbit_krw_market_set(broker, output_dir=output_dir)
                scan_markets = tuple(m for m in scan_markets if m in valid_upbit)
                ticker_map = fetch_tickers_batched(
                    broker,
                    list(scan_markets),
                    batch_size=int(ucfg.ticker_batch_size),
                    valid_markets=valid_upbit,
                )
            except Exception:
                ticker_map = {}
                for m in scan_markets:
                    try:
                        ticker_map[m] = broker.get_ticker(m)
                    except Exception:
                        continue
        try:
            if universe_meta is not None:
                save_crypto_universe_snapshot(output_dir, universe_meta)
        except OSError:
            pass
    tickers = [ticker_map[m] for m in scan_markets if m in ticker_map]
    if not tickers:
        return None

    from deepsignal.crypto_trading.crypto_ml_ensemble import EnsembleResult

    ranked: list[tuple[UpbitTicker, float, str, float, dict[str, Any], dict[str, str], EnsembleResult]] = []
    try:
        available_krw = float(broker.get_krw_available())
    except Exception:
        available_krw = 0.0
    total_portfolio_krw = available_krw + sum(max(0.0, float(h.valuation_krw or 0.0)) for h in holdings)

    from deepsignal.crypto_trading.crypto_overtrading_guards import OvertradingGuardConfig, check_buy_allowed

    ot_cfg = OvertradingGuardConfig()

    from deepsignal.crypto_trading.crypto_gate_config import (
        effective_gate_mode,
        log_gate_decision,
    )
    from deepsignal.crypto_trading.crypto_ml_ensemble import (
        CryptoMlEnsemble,
        ensemble_enabled,
        fmt_ml_prob,
    )
    from deepsignal.crypto_trading.crypto_ml_gate import ml_buy_gate_enabled

    gate_mode = effective_gate_mode(output_dir)
    ml_ens = CryptoMlEnsemble(output_dir=output_dir)
    if ml_buy_gate_enabled() or ensemble_enabled():
        ml_ens._lgbm.refresh_live_state()

    # ── 실시간 피처 로드 (단타 스코어링용) ──────────────────
    _rt_feature_map: dict[str, dict[str, float]] = {}
    try:
        import json as _json
        from pathlib import Path as _Path
        from deepsignal.market_data.feature_engine.engine import FeatureEngine as _FE

        _ls_path = _Path(output_dir) / "binance_stream" / "live_state.json"
        if _ls_path.exists():
            _payload = _json.loads(_ls_path.read_text())
            _eng = _FE(output_dir=str(_Path(output_dir) / "binance_stream"))
            _eng.ingest_live_state(_payload)
            for _sym in (_payload.get("symbols") or []):
                try:
                    _rt_feature_map[str(_sym).upper()] = _eng.feature_dict(str(_sym))
                except Exception:
                    pass
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).debug("[rt_features] 로드 실패: %s", _e)

    _static_excluded = {m.upper() for m in (getattr(_CRYPTO, "static_excluded_markets", ()) or ())}
    for t in tickers:
        if t.market.upper() in excluded:
            continue
        if t.market.upper() in _static_excluded:
            continue
        # 추격매수 캡: 기본은 보수적(8%)이나 공격성 다이얼이 올리면 급등주도 허용
        _chg_cap = float(_CRYPTO.session_max_signed_change_rate)
        try:
            import os as _os_cc
            _ov_cc = _os_cc.environ.get("CRYPTO_MAX_CHANGE_RATE", "").strip()
            if _ov_cc:
                _chg_cap = max(_chg_cap, float(_ov_cc))
        except ValueError:
            pass
        if abs(float(t.signed_change_rate or 0.0)) > _chg_cap:
            continue
        if float(t.acc_trade_price_24h or 0.0) < float(_CRYPTO.session_min_acc_trade_price_24h):
            continue
        if runner_state is not None:
            ok, _ = check_buy_allowed(
                runner_state,
                market=t.market,
                order_krw=krw_amount,
                total_portfolio_krw=total_portfolio_krw,
                cfg=ot_cfg,
            )
            if not ok:
                continue
        name = display_names.get(t.market, market_display_name(t.market))
        # Upbit KRW-BTC → Binance BTCUSDT 매핑으로 실시간 피처 조회
        _binance_key = (t.market.replace("KRW-", "") + "USDT").upper()
        _rt_feats = _rt_feature_map.get(_binance_key)
        ms = score_crypto_market(
            broker, t, display_name=name, macro_context=macro_context,
            buy_quality=quality_cfg, realtime_features=_rt_feats,
        )
        allowed, gates, breakdown, blocked = apply_crypto_buy_quality_gates(
            ms,
            ticker=t,
            macro_context=macro_context,
            order_krw=krw_amount,
            current_position_krw=float(getattr(hold_map.get(t.market), "valuation_krw", 0.0) or 0.0),
            total_portfolio_krw=total_portfolio_krw,
            config=gate_cfg,
            output_dir=output_dir,
        )
        if not allowed:
            continue
        fs = ms.final_score if ms.final_score is not None else ms.technical_score
        ml_r = ml_ens.predict(t.market, final_score=fs)
        if _live_auto_crypto_buy_requires_ml_gate() and _ml_result_failed_open(ml_r):
            prob_log = ml_r.blended_p if ml_r.blended_p is not None else ml_r.lgbm_p
            log_gate_decision(
                market=t.market,
                mode=gate_mode,
                prob=prob_log,
                final_score=fs,
                extra=f"blocked_ml_fail_open:{ml_r.status}",
            )
            continue
        if (ml_buy_gate_enabled() or ensemble_enabled()) and not ml_r.allowed:
            prob_log = ml_r.blended_p if ml_r.blended_p is not None else ml_r.lgbm_p
            log_gate_decision(
                market=t.market,
                mode=gate_mode,
                prob=prob_log,
                final_score=fs,
                extra="blocked",
            )
            continue
        gates = dict(gates or {})
        gates["ml_gate"] = ml_r.status
        gates["ensemble_mode"] = ml_r.ensemble_mode
        if ml_r.lgbm_p is not None:
            gates["win_probability"] = fmt_ml_prob(ml_r.lgbm_p)
        breakdown = dict(breakdown or {})
        breakdown["ml_ensemble"] = ml_r.to_dict()
        breakdown["scan_mode"] = scan_mode
        if ml_r.lgbm_p is not None:
            breakdown["win_probability"] = ml_r.lgbm_p
        momentum_bonus = float(t.signed_change_rate or 0.0) * 100.0 * 8.0
        ml_bonus = (float(ml_r.lgbm_p or 0) - 0.5) * 40.0 if ml_r.lgbm_p is not None else 0.0
        # 호재 부스트: 긍정 뉴스(상장·파트너십·업그레이드+높은 감성) → 랭킹 우선
        news_rank_bonus = 0.0
        news_size_mult = 1.0
        try:
            import os as _o_nb
            if _o_nb.environ.get("CRYPTO_LLM_NEWS_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
                from deepsignal.ai.crypto_news_sentiment import news_boost_for_market
                news_rank_bonus, news_size_mult = news_boost_for_market(t.market)
        except Exception:
            news_rank_bonus, news_size_mult = 0.0, 1.0
        rank_score = (float(fs or 0.0) + momentum_bonus + ml_bonus + news_rank_bonus) * ms.size_multiplier
        breakdown["news_size_mult"] = news_size_mult
        if news_rank_bonus > 0:
            breakdown["news_boost"] = news_rank_bonus
        ranked.append((t, rank_score, ms.quality_reason, ms.size_multiplier, breakdown, gates, ml_r))

    if not ranked:
        return None

    if prefer_non_holding_buy:
        non_holding = [row for row in ranked if row[0].market not in hold_map]
        if non_holding:
            ranked = non_holding
    positive = [row for row in ranked if row[0].signed_change_rate > 0]
    pool = positive if positive else ranked
    pool_sorted = sorted(pool, key=lambda x: x[1], reverse=True)

    for best_t, _rank, q_note, mult, breakdown, gates, ml_final in pool_sorted[:12]:
        best = best_t
        name = display_names.get(best.market, market_display_name(best.market))
        disp = breakdown.get("display") if isinstance(breakdown.get("display"), dict) else {}
        tech_pre = breakdown.get("technical_score")
        final_pre = breakdown.get("final_score")
        fs_pre = float(final_pre or tech_pre or 0)
        if _live_auto_crypto_buy_requires_ml_gate() and _ml_result_failed_open(ml_final):
            prob_log = ml_final.blended_p if ml_final.blended_p is not None else ml_final.lgbm_p
            log_gate_decision(
                market=best.market,
                mode=gate_mode,
                prob=prob_log,
                final_score=fs_pre,
                extra=f"blocked_ml_fail_open:{ml_final.status}",
            )
            continue
        if (ml_buy_gate_enabled() or ensemble_enabled()) and not ml_final.allowed:
            prob_log = ml_final.blended_p if ml_final.blended_p is not None else ml_final.lgbm_p
            log_gate_decision(
                market=best.market,
                mode=gate_mode,
                prob=prob_log,
                final_score=fs_pre,
                extra="blocked",
            )
            continue
        reason = "단기 상승 + 점수·유동성 통과" if best.signed_change_rate > 0 else "거래대금·점수 기준 선정(상승률 약세)"
        reason += f" (final {disp.get('final', 'n/a')}, regime {disp.get('macro_regime', 'n/a')})"
        if ml_final.lgbm_p is not None:
            reason += f"; P(win)={fmt_ml_prob(ml_final.lgbm_p)}"
        if ml_final.seq_p is not None:
            reason += f"; SEQ={fmt_ml_prob(ml_final.seq_p)}"
        candidate_krw = krw_amount
        if mult < 1.0:
            candidate_krw = max(floor_krw, candidate_krw * mult)
            reason += f"; 변동성 축소({mult:.0%})"
        # 호재 사이즈업 (강한 긍정 뉴스면 주문 약간 키움, 상한 ×1.3)
        _nsm = float(breakdown.get("news_size_mult", 1.0) or 1.0)
        if _nsm > 1.0:
            candidate_krw = candidate_krw * _nsm
            reason += f"; 호재 사이즈업({_nsm:.0%})"
        if q_note and q_note != "quality_ok":
            reason += f"; {q_note}"

        adjusted_krw, eq_report = apply_execution_quality_to_buy_amount(
            broker,
            market=best.market,
            order_krw=candidate_krw,
            take_profit_pct=float(take_profit_pct),
            stop_loss_pct=float(stop_loss_pct),
        )
        if should_block_entry_by_execution_quality(eq_report):
            continue
        candidate_krw = adjusted_krw
        gates = dict(gates or {})
        gates["execution_quality"] = "pass"
        gates["execution_quality_rr"] = f"{eq_report.net_rr_after_fees:.2f}"
        breakdown = dict(breakdown or {})
        breakdown["ml_ensemble"] = ml_final.to_dict()
        if ml_final.lgbm_p is not None:
            breakdown["win_probability"] = ml_final.lgbm_p
            gates["win_probability"] = fmt_ml_prob(ml_final.lgbm_p)
        snap = ml_ens._lgbm.feature_snapshot_for_market(best.market)
        if snap:
            breakdown["features_snapshot"] = snap

        tech = breakdown.get("technical_score")
        macro = breakdown.get("macro_score")
        final = breakdown.get("final_score")
        prob_log = ml_final.blended_p if ml_final.blended_p is not None else ml_final.lgbm_p
        log_gate_decision(
            market=best.market,
            mode=gate_mode,
            prob=prob_log,
            final_score=fs_pre,
        )

        return CryptoRecommendation(
            market=best.market,
            display_name=name,
            side="buy",
            krw_amount=candidate_krw,
            current_price=best.trade_price,
            reason=reason + "; 체결품질 통과",
            signed_change_rate=best.signed_change_rate,
            acc_trade_price_24h=best.acc_trade_price_24h,
            technical_score=float(tech) if tech is not None else None,
            macro_score=float(macro) if macro is not None else None,
            final_score=float(final) if final is not None else None,
            macro_regime=str(breakdown.get("macro_regime") or macro_context.get("market_regime") or ""),
            score_breakdown={**breakdown, "execution_quality": eq_report.to_dict()},
            quality_gates=gates,
        )

    return None


def build_daily_crypto_recommendation(
    broker: UpbitBroker,
    *,
    take_profit_pct: float = _CRYPTO.take_profit_pct,
    stop_loss_pct: float = _CRYPTO.stop_loss_pct,
    take_profit_buffer_pct: float = _CRYPTO.take_profit_buffer_pct,
    stop_loss_buffer_pct: float = _CRYPTO.stop_loss_buffer_pct,
    max_order_value: float = MIN_ORDER_KRW,
    markets: tuple[str, ...] | None = None,
    universe_config: CryptoUniverseConfig | None = None,
    exclude_markets: tuple[str, ...] | None = None,
    prefer_non_holding_buy: bool = _CRYPTO.prefer_non_holding_buy,
    buy_quality: CryptoBuyQualityConfig | None = None,
    quality_config: CryptoRecommendationQualityConfig | None = None,
    macro_db_path: str | Path | None = None,
    output_dir: str | Path = "outputs",
    runner_state: dict[str, Any] | None = None,
) -> CryptoRecommendation | None:
    """SELL timing first, then BUY."""
    sell = build_sell_recommendation(
        broker,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_buffer_pct=take_profit_buffer_pct,
        stop_loss_buffer_pct=stop_loss_buffer_pct,
        macro_db_path=macro_db_path,
        runner_state=runner_state,
        output_dir=output_dir,
    )
    if sell is not None:
        return sell
    return build_crypto_recommendation(
        broker,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        markets=markets,
        universe_config=universe_config,
        max_order_value=max_order_value,
        exclude_markets=exclude_markets,
        prefer_non_holding_buy=prefer_non_holding_buy,
        buy_quality=buy_quality,
        quality_config=quality_config,
        macro_db_path=macro_db_path,
        output_dir=output_dir,
        runner_state=runner_state,
    )
