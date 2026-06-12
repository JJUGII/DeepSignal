"""DeepSignal CLI 진입점."""

from __future__ import annotations

import argparse
import logging


def _verify_imports() -> None:
    """주요 패키지 로드 스모크."""
    import deepsignal.ai.model_pipeline  # noqa: F401
    import deepsignal.analyzer.sentiment.sentiment_analyzer  # noqa: F401
    import deepsignal.analyzer.technical.technical_analyzer  # noqa: F401
    import deepsignal.backtest.backtest_engine  # noqa: F401
    import deepsignal.collector.economic.economic_collector  # noqa: F401
    import deepsignal.collector.market.market_collector  # noqa: F401
    import deepsignal.collector.news.news_collector  # noqa: F401
    try:
        import deepsignal.dashboard.dashboard_app  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name != "_tkinter":
            raise
    import deepsignal.live_trading.broker_interface  # noqa: F401
    import deepsignal.live_trading.dry_run_broker  # noqa: F401
    import deepsignal.live_trading.kis_broker  # noqa: F401
    import deepsignal.live_trading.kis_config  # noqa: F401
    import deepsignal.live_trading.kis_order_status  # noqa: F401
    import deepsignal.live_trading.live_account_sync  # noqa: F401
    import deepsignal.live_trading.reconcile  # noqa: F401
    import deepsignal.live_trading.order_guard  # noqa: F401
    import deepsignal.live_trading.fill_tracker  # noqa: F401
    import deepsignal.live_trading.trading_session  # noqa: F401
    import deepsignal.live_trading.live_execution_guard  # noqa: F401
    import deepsignal.live_trading.live_order_executor  # noqa: F401
    import deepsignal.live_trading.live_order_plan  # noqa: F401
    import deepsignal.live_trading.ai_recommendation  # noqa: F401
    import deepsignal.live_trading.ai_recommendation.validation_engine  # noqa: F401
    import deepsignal.notifiers.notification_service  # noqa: F401
    import deepsignal.paper_trading.paper_trading_engine  # noqa: F401
    import deepsignal.pipelines.daily_pipeline  # noqa: F401
    import deepsignal.portfolio.portfolio_engine  # noqa: F401
    import deepsignal.reporting.report_service  # noqa: F401
    import deepsignal.risk.risk_manager  # noqa: F401
    import deepsignal.scoring.macro_scorer  # noqa: F401
    import deepsignal.scoring.signal_scorer  # noqa: F401
    import deepsignal.strategy.base_strategy  # noqa: F401
    import deepsignal.strategy.sample_strategy  # noqa: F401
    import deepsignal.storage.database  # noqa: F401


def cmd_init() -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.storage.database import init_database

    settings = load_settings()
    init_database(settings.db_path)
    _verify_imports()
    print("DeepSignal initialized successfully")


def cmd_collect_news() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from deepsignal.config.settings import load_settings
    from deepsignal.pipelines.daily_pipeline import collect_news_to_db
    from deepsignal.storage.database import init_database

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    collect_news_to_db(path_str, settings)


def cmd_collect_market() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from deepsignal.config.settings import load_settings
    from deepsignal.pipelines.daily_pipeline import collect_market_to_db
    from deepsignal.storage.database import init_database

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    collect_market_to_db(path_str, settings)


def cmd_collect_macro() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from deepsignal.config.settings import load_settings
    from deepsignal.pipelines.daily_pipeline import collect_macro_to_db
    from deepsignal.storage.database import init_database

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    collect_macro_to_db(path_str, settings)


def cmd_analyze_macro() -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.scoring.macro_scorer import MacroScorer
    from deepsignal.storage.database import fetch_latest_economic_indicators, init_database

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    rows = fetch_latest_economic_indicators(path_str)
    result = MacroScorer().calculate_macro_score(rows)

    print("DeepSignal macro analysis finished")
    print()
    ms = "(no data)" if result.macro_score is None else f"{result.macro_score:.2f}"
    print(f"Macro Score: {ms}")
    print(f"Market Regime: {result.market_regime}")
    print(f"Confidence: {result.confidence:.2f}")
    print()
    print("Reason:")
    for part in result.reason.splitlines():
        p = part.strip()
        if not p:
            continue
        print(p if p.startswith("-") else f"- {p}")


def cmd_analyze_portfolio() -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.portfolio.portfolio_engine import PortfolioEngine
    from deepsignal.scoring.macro_scorer import MacroScorer
    from deepsignal.storage.database import (
        fetch_latest_economic_indicators,
        fetch_latest_paper_snapshot,
        fetch_latest_signals,
        init_database,
    )

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    signals = fetch_latest_signals(path_str, limit=100)
    macro = MacroScorer().calculate_macro_score(fetch_latest_economic_indicators(path_str))
    snap_row = fetch_latest_paper_snapshot(path_str)
    if snap_row is not None and snap_row.get("equity") is not None:
        total_cash = float(snap_row["equity"])
    elif snap_row is not None and snap_row.get("cash") is not None:
        total_cash = float(snap_row["cash"])
    else:
        total_cash = 10_000.0

    snap = PortfolioEngine().build_portfolio(signals, total_cash, macro)
    buf_pct = float(snap.raw.get("cash_buffer_fraction") or 0.0) * 100.0

    print("DeepSignal portfolio analysis finished")
    print()
    print(f"Market Regime: {snap.market_regime}")
    print(f"Cash Buffer: {buf_pct:.1f}%")
    print()
    print("Allocations:")
    if not snap.allocations:
        print("(no qualifying BUY_CANDIDATE signals)")
    else:
        for a in snap.allocations:
            print(f"{a.symbol:<6} {a.target_weight * 100:.1f}%")


def cmd_analyze_news(symbol: str) -> None:
    from deepsignal.analyzer.sentiment.sentiment_analyzer import SentimentAnalyzer
    from deepsignal.config.settings import load_settings
    from deepsignal.storage.database import fetch_recent_news_items, init_database

    sym = symbol.strip().upper()
    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    rows = fetch_recent_news_items(path_str, symbol=sym, limit=100)
    analyzer = SentimentAnalyzer()
    result = analyzer.analyze_news_items(sym, rows)

    print("DeepSignal news sentiment analysis finished")
    print(f"Symbol: {result.symbol}")
    print(f"News Count: {result.news_count}")
    print(f"Positive: {result.positive_count}")
    print(f"Negative: {result.negative_count}")
    print(f"Neutral: {result.neutral_count}")
    ns = "-" if result.news_score is None else f"{result.news_score:.2f}"
    cf = "-" if result.confidence is None else f"{result.confidence:.2f}"
    print(f"News Score: {ns}")
    print(f"Confidence: {cf}")
    print(f"Reason: {result.reason}")


def cmd_analyze_technical(symbol: str) -> None:
    from deepsignal.analyzer.technical.technical_analyzer import TechnicalAnalyzer
    from deepsignal.config.settings import load_settings
    from deepsignal.storage.database import init_database

    sym = symbol.strip()
    settings = load_settings()
    db_path = init_database(settings.db_path)
    path_str = str(db_path)

    analyzer = TechnicalAnalyzer()
    results = analyzer.analyze_symbol_from_db(path_str, sym, source="yfinance", limit=120)
    if not results:
        print(
            f"No market price data found for {sym.upper()}. "
            "Run: python main.py collect-market"
        )
        return

    print("DeepSignal technical analysis finished")
    print(f"Symbol: {sym.upper()}")
    print(f"Rows analyzed: {len(results)}")
    print()
    print(f"{'Date':<12} {'Close':>8} {'EMA12':>8} {'EMA26':>8} {'RSI14':>8} {'Trend':>6}")
    tail = results[-5:]

    def fmt(v: float | None) -> str:
        if v is None:
            return f"{'-':>8}"
        return f"{v:8.2f}"

    for t in tail:
        tr = "-" if t.trend_score is None else f"{t.trend_score:6.1f}"
        print(
            f"{t.trade_date:<12} {fmt(t.close)} {fmt(t.ema_12)} {fmt(t.ema_26)} {fmt(t.rsi_14)} {tr:>6}"
        )


def cmd_score_symbol(symbol: str) -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.pipelines.daily_pipeline import score_symbol_to_db
    from deepsignal.storage.database import init_database

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    score_symbol_to_db(path_str, symbol)


def cmd_show_analysis_conditions(args: argparse.Namespace) -> int:
    """숫자 분석 조건 단일 출처를 JSON/Markdown으로 출력 (주문·네트워크 없음)."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

    data = DEFAULT_ANALYSIS_CONDITIONS.to_dict()
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["note"] = (
        "기관형·문헌 기반 기본 임계값. CLI 옵션으로 risk-check 등 일부만 덮어쓸 수 있음."
    )

    out_dir = Path(getattr(args, "output_dir", "outputs") or "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"analysis_conditions_{stamp}.json"
    md_path = out_dir / "ANALYSIS_CONDITIONS.md"

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# DeepSignal 분석 조건 (숫자 임계값)",
        "",
        f"- 생성: {data['generated_at']}",
        f"- JSON: `{json_path.name}`",
        "",
        "## 점수·신호",
        f"- final 가중: technical {data['score']['technical_weight']}, "
        f"news {data['score']['news_weight']}, macro {data['score']['macro_weight']}",
        f"- BUY 후보: final_score ≥ {data['score']['buy_candidate_min']}",
        f"- SELL 후보: final_score ≤ {data['score']['sell_candidate_max']}",
        f"- AI min_final_score 기본: {data['score']['min_final_score_default']}",
        "",
        "## 리스크 (주식 KIS)",
        f"- 손절: {data['risk']['stop_loss_pct']:.2%}",
        f"- 손실 경고: {data['risk']['warn_loss_pct']:.2%}",
        f"- 익절: {data['risk']['take_profit_pct']:.2%}",
        f"- 고점 대비 리뷰: {data['risk']['drawdown_from_peak_review_pct']:.2%} "
        "(position.raw.peak_price 필요)",
        "",
        "## 거시 (VIX/DXY/TNX)",
        f"- risk_on: macro_score ≥ {data['macro']['regime_risk_on_min']}",
        f"- risk_off: macro_score ≤ {data['macro']['regime_risk_off_max']}",
        "",
        "## 코인",
        f"- 익절/손절: {data['crypto']['take_profit_pct']}% / {data['crypto']['stop_loss_pct']}%",
        f"- RSI 매수 거부: > {data['crypto']['max_rsi']}",
        "",
        "전체 필드는 JSON을 참고하세요.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


def cmd_backtest_symbol(symbol: str, *, include_news: bool = False) -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.pipelines.daily_pipeline import backtest_symbol_to_db
    from deepsignal.storage.database import init_database

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    backtest_symbol_to_db(path_str, symbol, include_news=include_news)


def cmd_paper_step(symbol: str) -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.pipelines.daily_pipeline import paper_step_to_db
    from deepsignal.storage.database import init_database

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    paper_step_to_db(path_str, symbol)


def cmd_optimize_weights(args: argparse.Namespace) -> int:
    """[신호] GSQS 가중치 자동 최적화 실행."""
    from pathlib import Path as _Path
    from deepsignal.crypto_trading.signal.weight_optimizer import (
        WeightOptimizer, DEFAULT_WEIGHTS, MIN_SAMPLES,
    )

    output_dir = _Path(getattr(args, "output_dir", "outputs") or "outputs")
    horizon = int(getattr(args, "horizon", 5) or 5)
    force = bool(getattr(args, "force", False))
    status_only = bool(getattr(args, "status", False))

    opt = WeightOptimizer(output_dir, horizon_minutes=horizon)
    st = opt.status()

    n = st["n_complete_signals"]
    print(f"\n=== GSQS 가중치 최적화 현황 ===")
    print(f"완성 신호: {n} / {MIN_SAMPLES}건  ({st['progress_pct']:.1f}%)")
    if st["next_run_at"]:
        print(f"최적화까지 남은 건: {st['next_run_at']}건")
    if st["last_optimized_at"]:
        print(f"마지막 최적화: {st['last_optimized_at']}")
        print(f"  개선: {(st['last_improvement'] or 0)*100:+.1f}%  "
              f"적용됨: {'예' if st['last_applied'] else '아니오 (퇴보)'}")
    print(f"\n현재 적용 가중치:")
    for k, v in st["current_weights"].items():
        default = DEFAULT_WEIGHTS.get(k, v)
        diff = v - default
        arrow = " ↑" if diff > 0.001 else " ↓" if diff < -0.001 else ""
        print(f"  {k:<12}: {v*100:5.1f}%  (기본 {default*100:.0f}%{arrow})")

    if status_only:
        return 0

    if not st["ready_to_optimize"] and not force:
        remaining = MIN_SAMPLES - n
        print(f"\n아직 데이터 부족 — {remaining}건 더 필요 (--force로 강제 실행 가능)")
        return 0

    if force and n < MIN_SAMPLES:
        print(f"\n⚠️  강제 실행 (샘플 {n}건 — 결과 신뢰도 낮음)")

    print(f"\n가중치 최적화 실행 중 (horizon={horizon}분)...")
    result = opt.run()

    if "error" in result:
        print(f"오류: {result['error']}")
        return 1

    print(f"\n=== 최적화 완료 ===")
    print(f"샘플 수: {result['n_samples']}건")
    print(f"기본 승률:  {result['default_win_rate']*100:.1f}%")
    print(f"최적화 후:  {result['expected_win_rate']*100:.1f}%")
    improv = result["improvement"]
    applied = result.get("applied", True)
    print(f"개선:       {improv*100:+.1f}%  →  {'✅ 적용됨' if applied else '⛔ 미적용 (퇴보 방지)'}")
    if applied:
        print(f"\n최적 가중치:")
        for k, v in result["weights"].items():
            default = DEFAULT_WEIGHTS[k]
            arrow = "↑" if v > default else "↓" if v < default else "="
            print(f"  {k:<12}: {v*100:5.1f}%  (기본 {default*100:.0f}% {arrow})")
    return 0


def cmd_crypto_check(args: argparse.Namespace) -> int:
    """[실전-코인-01] Upbit 연결·잔고·현재가 점검 (주문 없음)."""
    from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env
    from deepsignal.crypto_trading.upbit_broker import UpbitBroker
    from deepsignal.crypto_trading.upbit_config import UpbitConfigError, load_upbit_config_from_env, validate_upbit_config

    ensure_crypto_runtime_env()
    broker_name = str(getattr(args, "broker", "upbit") or "upbit").lower()
    if broker_name != "upbit":
        print(f"Unsupported broker: {broker_name}")
        return 1
    use_network = bool(getattr(args, "network", False))
    try:
        cfg = load_upbit_config_from_env(dry_run=not use_network)
    except UpbitConfigError as e:
        print(f"crypto-check failed: {e}")
        return 1
    errs, warns = validate_upbit_config(cfg)
    for w in warns:
        print(f"Warning: {w}")
    if errs:
        for e in errs:
            print(f"Error: {e}")
        return 1
    br = UpbitBroker(cfg)
    print(f"DeepSignal crypto-check OK (dry_run={cfg.dry_run})")
    print(f"Config: {cfg.masked_summary()}")
    if use_network or cfg.dry_run:
        krw = br.get_krw_available()
        print(f"KRW available: {krw:,.0f}")
        from deepsignal.crypto_trading.crypto_holdings import format_holdings_console

        holdings = br.get_crypto_holdings()
        for line in format_holdings_console(holdings):
            print(line)
        from deepsignal.crypto_trading.crypto_universe import CryptoUniverseConfig, resolve_crypto_markets

        meta = resolve_crypto_markets(br, config=CryptoUniverseConfig())
        print(
            f"KRW universe: {meta.total_krw_markets} markets, "
            f"buy_scan={meta.scanned_for_buy} (min 24h vol {meta.min_acc_trade_price_24h:,.0f}원)"
        )
        if meta.markets:
            sample = ", ".join(meta.markets[:10])
            print(f"Scan sample: {sample}{'...' if len(meta.markets) > 10 else ''}")
        for m in meta.markets[:3]:
            t = br.get_ticker(m)
            print(f"{m}: {t.trade_price:,.0f} (change {t.signed_change_rate*100:.2f}%)")
    return 0


def cmd_crypto_daily_plan(args: argparse.Namespace) -> int:
    """[실전-코인-01] 코인 매수 추천·주문 계획 JSON/Markdown 생성."""
    import json

    _apply_aggression_dial()  # 분석에도 투자공격성 다이얼 반영(문턱·게이트)
    from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env
    from deepsignal.crypto_trading.crypto_order_plan import build_plan_from_recommendation, save_crypto_plan
    from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig
    from deepsignal.crypto_trading.crypto_recommendation import build_daily_crypto_recommendation
    from deepsignal.crypto_trading.crypto_universe import (
        crypto_universe_config_from_args,
        parse_extra_markets,
    )
    from deepsignal.crypto_trading.crypto_recommendation_diagnostics import (
        build_crypto_recommendation_diagnostics,
        format_diagnostics_console,
        save_crypto_no_recommendation_artifacts,
    )
    from deepsignal.config.settings import load_settings
    from deepsignal.crypto_trading.upbit_broker import UpbitBroker
    from deepsignal.crypto_trading.upbit_config import UpbitConfigError, load_upbit_config_from_env
    from deepsignal.storage.database import init_database

    if str(getattr(args, "broker", "upbit")).lower() != "upbit":
        print("Only --broker upbit is supported")
        return 1
    ensure_crypto_runtime_env()
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

    _crypto_thr = DEFAULT_ANALYSIS_CONDITIONS.crypto
    max_val = float(getattr(args, "max_order_value", 0.0) if hasattr(args, "max_order_value") else 0.0)
    take_profit = float(getattr(args, "take_profit_pct", _crypto_thr.take_profit_pct) or _crypto_thr.take_profit_pct)
    stop_loss = float(getattr(args, "stop_loss_pct", _crypto_thr.stop_loss_pct) or _crypto_thr.stop_loss_pct)
    tp_buffer = float(getattr(args, "take_profit_buffer_pct", 0.05) or 0.05)
    sl_buffer = float(getattr(args, "stop_loss_buffer_pct", 0.05) or 0.05)
    min_vol_ratio = float(getattr(args, "min_volume_ratio", _crypto_thr.min_volume_ratio) or _crypto_thr.min_volume_ratio)
    use_network = bool(getattr(args, "network", False))
    debug_holdings = bool(getattr(args, "debug_holdings", False))
    debug_quality = bool(getattr(args, "debug_quality", False))
    try:
        cfg = load_upbit_config_from_env(dry_run=not use_network)
    except UpbitConfigError as e:
        print(f"crypto-daily-plan failed: {e}")
        return 1
    br = UpbitBroker(cfg)
    buy_quality = CryptoBuyQualityConfig(min_volume_ratio=min_vol_ratio)
    extra_markets = parse_extra_markets(getattr(args, "crypto_markets", ""))
    universe_cfg = crypto_universe_config_from_args(args, extra_markets=extra_markets)
    explicit_markets = extra_markets if extra_markets else None
    holdings = br.get_crypto_holdings()
    if debug_holdings:
        from deepsignal.crypto_trading.crypto_holdings import format_holdings_console, holding_to_dict

        print("--- debug holdings ---")
        for line in format_holdings_console(holdings):
            print(line)
        print(json.dumps([holding_to_dict(h) for h in holdings], ensure_ascii=False, indent=2))
    macro_db = str(init_database(load_settings().db_path))
    rec = build_daily_crypto_recommendation(
        br,
        take_profit_pct=take_profit,
        stop_loss_pct=stop_loss,
        take_profit_buffer_pct=tp_buffer,
        stop_loss_buffer_pct=sl_buffer,
        max_order_value=max_val,
        buy_quality=buy_quality,
        markets=explicit_markets,
        universe_config=universe_cfg,
        macro_db_path=macro_db,
        output_dir=out_dir,
    )
    if rec is None:
        diagnostics = build_crypto_recommendation_diagnostics(
            br,
            take_profit_pct=take_profit,
            stop_loss_pct=stop_loss,
            take_profit_buffer_pct=tp_buffer,
            stop_loss_buffer_pct=sl_buffer,
            max_order_value=max_val,
            buy_quality=buy_quality,
            universe_config=universe_cfg,
            macro_db_path=macro_db,
            output_dir=out_dir,
        )
        jpath, mpath = save_crypto_no_recommendation_artifacts(out_dir, diagnostics)
        print("No crypto recommendation")
        print(format_diagnostics_console(diagnostics))
        if debug_quality:
            print(json.dumps(diagnostics.to_dict(), ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "status": "CRYPTO_PLAN_NO_RECOMMENDATION",
                    "recommendation": None,
                    "plan_json": jpath.as_posix(),
                    "plan_md": mpath.as_posix(),
                    "diagnostics": diagnostics.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    plan = build_plan_from_recommendation(rec)
    jpath, mpath = save_crypto_plan(out_dir, plan)
    from deepsignal.crypto_trading.crypto_recommendation_outcomes import record_crypto_recommendation

    outcome_id = record_crypto_recommendation(plan, outcomes_db=out_dir, rec=rec)
    print(
        json.dumps(
            {
                "recommendation": rec.to_dict(),
                "plan_json": jpath.as_posix(),
                "plan_md": mpath.as_posix(),
                "outcome_id": outcome_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_crypto_telegram_approval(args: argparse.Namespace) -> int:
    """[실전-코인-01] Telegram 승인 요청·(선택) 폴링·승인 시 주문."""
    import json
    from pathlib import Path

    from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env
    from deepsignal.crypto_trading.crypto_order_plan import CRYPTO_PLAN_JSON, load_crypto_plan
    from deepsignal.crypto_trading.crypto_telegram_flow import (
        create_crypto_approval_request,
        load_crypto_telegram_config_from_env,
        poll_crypto_telegram_until_done,
    )
    from deepsignal.crypto_trading.upbit_broker import UpbitBroker
    from deepsignal.crypto_trading.upbit_config import load_upbit_config_from_env

    ensure_crypto_runtime_env()
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    plan_path = Path(out_dir) / CRYPTO_PLAN_JSON
    if not plan_path.is_file():
        print(f"Missing plan: {plan_path}. Run crypto-daily-plan first.")
        return 1
    plan = load_crypto_plan(plan_path)
    tg = load_crypto_telegram_config_from_env(output_dir=out_dir)
    tg.wait_fill_seconds = float(getattr(args, "wait_fill_seconds", 0) or 0)
    tg.fill_poll_interval = float(getattr(args, "fill_poll_interval", 3) or 3)
    execute = bool(getattr(args, "execute", False))
    from deepsignal.crypto_trading.crypto_paper_mode import crypto_paper_mode_enabled

    if execute and crypto_paper_mode_enabled():
        print(
            "crypto-telegram-approval: CRYPTO_PAPER_MODE=true blocks --execute. "
            "Set CRYPTO_PAPER_MODE=false for live orders.",
            flush=True,
        )
        return 1
    cfg = load_upbit_config_from_env(dry_run=not execute)
    br = UpbitBroker(cfg)
    tg.send = bool(getattr(args, "send", False))
    tg.poll = bool(getattr(args, "poll", False))
    req = create_crypto_approval_request(plan, cfg=tg, plan_path=plan_path)
    print(json.dumps(req.to_dict(), ensure_ascii=False, indent=2))
    if tg.poll and tg.bot_token:
        out = poll_crypto_telegram_until_done(tg, br)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if (
            tg.wait_fill_seconds > 0
            and isinstance(out, dict)
            and out.get("status") == "APPROVED"
            and out.get("fill_follow_up") is None
        ):
            res = out.get("result") if isinstance(out.get("result"), dict) else {}
            uuid = res.get("uuid")
            if uuid:
                from deepsignal.crypto_trading.crypto_telegram_flow import follow_up_order_fill
                from deepsignal.crypto_trading.upbit_broker import UpbitOrderResult

                fill_out = follow_up_order_fill(
                    tg,
                    br,
                    plan,
                    UpbitOrderResult(
                        market=plan.market,
                        side="bid",
                        order_type="limit",
                        price=plan.limit_price,
                        volume=0.0,
                        krw_amount=plan.krw_amount,
                        status=str(res.get("status", "wait")),
                        uuid=str(uuid),
                        dry_run=bool(res.get("dry_run", False)),
                    ),
                )
                print(json.dumps(fill_out, ensure_ascii=False, indent=2))
    return 0


def cmd_crypto_paper_status(args: argparse.Namespace) -> int:
    """[코인] 페이퍼 모드 기간·성과 요약."""
    from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env
    from deepsignal.crypto_trading.crypto_paper_state import format_paper_status_report

    ensure_crypto_runtime_env()
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    print(format_paper_status_report(out_dir))
    return 0


def cmd_trading_halt(args: argparse.Namespace) -> int:
    """[킬스위치] 전역 거래 중단 — TRADING_HALT 생성. 모든 러너가 신규매수 차단(청산은 계속)."""
    from deepsignal.risk.trading_halt import engage_halt

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    reason = str(getattr(args, "reason", "") or "manual halt")
    path = engage_halt(out_dir, reason, source="manual")
    print(f"TRADING HALT engaged: {path}")
    print(f"reason: {reason}")
    print("모든 러너가 다음 tick부터 신규 매수를 중단합니다 (TP/SL 청산은 계속).")
    print("해제: python main.py trading-resume")
    return 0


def cmd_trading_resume(args: argparse.Namespace) -> int:
    """[킬스위치] 거래 중단 해제 — TRADING_HALT 제거."""
    from deepsignal.risk.trading_halt import clear_halt

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    cleared = clear_halt(out_dir)
    print("TRADING HALT 해제됨." if cleared else "활성 halt 없음 (이미 정상 상태).")
    return 0


def cmd_trading_halt_status(args: argparse.Namespace) -> int:
    """[킬스위치] 현재 halt 상태 + 당일 코인 실현손익 확인."""
    from deepsignal.risk.trading_halt import (
        crypto_realized_pnl_krw_today,
        is_trading_halted,
        load_daily_loss_policy_from_env,
    )

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    halted, reason = is_trading_halted(out_dir)
    pol = load_daily_loss_policy_from_env()
    pnl = crypto_realized_pnl_krw_today(out_dir)
    print(f"HALT: {'ACTIVE' if halted else 'inactive'}" + (f" — {reason}" if halted else ""))
    print(
        f"일일 손실 한도: KRW={pol.max_loss_krw:,.0f} / PCT={pol.max_loss_pct:.2f}% "
        f"(0=비활성)"
    )
    print(
        "오늘 코인 실현손익: "
        + ("데이터 없음" if pnl is None else f"{pnl:,.0f}원")
    )
    return 0


def cmd_leverage_trend(args: argparse.Namespace) -> int:
    """[레버리지] 나스닥 2x ETF — 상태/실행/러너. 다중 게이트로 보호."""
    import time as _t
    try:
        from dotenv import load_dotenv as _ld
        _ld(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env"), override=False)
    except Exception:
        pass
    from deepsignal.live_trading.leverage_trend import decide, format_status, execute as _exec
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    mode = getattr(args, "lev_mode", "status")
    if mode == "status":
        print(format_status(decide(out_dir)))
        return 0
    if mode == "run":
        import json as _json
        print(_json.dumps(_exec(out_dir, execute=bool(getattr(args, "execute", False))), ensure_ascii=False, indent=2))
        return 0
    # runner
    interval = max(60, int(getattr(args, "interval_minutes", 30) * 60))
    execute = bool(getattr(args, "execute", False))
    print(f"leverage-trend-runner 시작 (interval {interval//60}분, execute={execute})", flush=True)
    while True:
        try:
            try:
                from deepsignal.risk.aggression import refresh_and_apply as _raa
                _raa()
            except Exception:
                pass
            r = _exec(out_dir, execute=execute)
            print(f"[{r.get('action')}] {r.get('message','')}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[오류] {e}", flush=True)
        _t.sleep(interval)


def cmd_intraday_runner(args: argparse.Namespace) -> int:
    """[고속회전] 장중 인트라데이 루프 — 보유 트레일링스톱 점검 (기본 dry-run)."""
    from deepsignal.live_trading.runner.intraday_runner import run_intraday_tick, run_intraday_loop
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    market = getattr(args, "market", "kr")
    execute = bool(getattr(args, "execute", False))
    if getattr(args, "once", False):
        import json as _json
        res = run_intraday_tick(out_dir, execute=execute, market=market)
        print(_json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    run_intraday_loop(out_dir, execute=execute, market=market)
    return 0


def cmd_market_open_report(args: argparse.Namespace) -> int:
    """[보고] 장 시작 시각 — 코인+국내+해외 오늘자 통합 매매 요약을 텔레그램 발송."""
    from deepsignal.reporting.market_open_report import send_market_open_report
    label = getattr(args, "label", "") or ""
    res = send_market_open_report(label)
    print(res.get("text", ""))
    print(f"\n[텔레그램] {res.get('message')}")
    return 0 if res.get("ok") else 0  # 발송 실패해도 러너 죽지 않게 0


def cmd_kr_scan(args: argparse.Namespace) -> int:
    """[국내-스캔] 전 시장 급등주 스캔(KIS 순위 API) → kr_movers_v1 신호 기록."""
    import os as _o
    try:
        from dotenv import load_dotenv as _ld
        _ld()
    except ImportError:
        pass
    _apply_aggression_dial()
    if getattr(args, "force", False):
        _o.environ["KR_SCANNER_ENABLED"] = "true"
    from deepsignal.live_trading.kr_market_scanner import run_kr_scan
    import json as _j
    res = run_kr_scan()
    print(_j.dumps(res, ensure_ascii=False, indent=1))
    return 0


def cmd_crypto_news_refresh(args: argparse.Namespace) -> int:
    """[코인-LLM] 코인 뉴스 감성/악재를 LLM으로 분석해 캐시에 기록.

    스코어링은 news_score로, 실행엔진은 악재 차단 게이트로 이 캐시를 읽는다.
    기본 OFF: CRYPTO_LLM_NEWS_ENABLED=true + OPENAI_API_KEY 필요.
    """
    import os as _o
    try:
        from dotenv import load_dotenv as _ld
        _ld()
    except ImportError:
        pass
    from deepsignal.ai.crypto_news_sentiment import refresh_crypto_news_sentiment
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    # 분석 대상 코인: --markets 지정 또는 라이브 유니버스 상위
    markets_arg = str(getattr(args, "markets", "") or "").strip()
    if markets_arg:
        markets = [m.strip().upper() for m in markets_arg.split(",") if m.strip()]
    else:
        try:
            from deepsignal.crypto_trading.upbit_config import load_upbit_config_from_env
            from deepsignal.crypto_trading.upbit_broker import UpbitBroker
            from deepsignal.crypto_trading.crypto_universe import (
                get_upbit_krw_market_set, fetch_tickers_batched, select_markets_for_buy_scan,
            )
            b = UpbitBroker(load_upbit_config_from_env())
            valid = list(get_upbit_krw_market_set(b, output_dir=out_dir))
            tm = fetch_tickers_batched(b, valid, batch_size=100, valid_markets=set(valid))
            top = select_markets_for_buy_scan(tm, max_markets=int(getattr(args, "max_markets", 40)))
            markets = list(top) if top else [t.market for t in list(tm.values())[:40]]
        except Exception as e:
            print(f"유니버스 조회 실패, 코어만: {e}")
            markets = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]
    res = refresh_crypto_news_sentiment(markets, output_dir=out_dir,
                                        max_markets=int(getattr(args, "max_markets", 40)))
    import json as _j
    print("[코인 뉴스 감성 갱신]", _j.dumps(res, ensure_ascii=False))
    if not res.get("enabled"):
        print("※ 비활성 상태 — .env에 CRYPTO_LLM_NEWS_ENABLED=true 와 OPENAI_API_KEY 설정 필요")
    return 0


def cmd_aggression_report(args: argparse.Namespace) -> int:
    """[보고] 공격성 단계별·추격거래별 성과 집계 → 리포트 저장(+선택 텔레그램)."""
    from deepsignal.risk.aggression_report import write_report, render_markdown, build_aggression_report

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    md_path, json_path = write_report(out_dir)
    report = build_aggression_report(out_dir)
    text = render_markdown(report)
    print(text)
    print(f"[저장] {md_path}\n[저장] {json_path}")
    if getattr(args, "telegram", False):
        try:
            from dotenv import load_dotenv as _ld
            _ld()
            import os as _o
            tok = _o.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
            chat = _o.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
            if tok and chat:
                from deepsignal.live_trading.telegram.approval import telegram_api_post
                telegram_api_post("sendMessage",
                                  {"chat_id": chat, "text": text}, bot_token=tok)
                print("[텔레그램] 발송 완료")
        except Exception as e:
            print(f"[텔레그램] 발송 실패(무시): {e}")
    return 0


def cmd_regime_trend_status(args: argparse.Namespace) -> int:
    """[추세추종] S&P500 200일선 추세 신호 + 권고 행동 (유일한 robust 엣지)."""
    from deepsignal.live_trading.regime_trend import decide_regime_trend, format_status

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    decision = decide_regime_trend(out_dir)
    print(format_status(decision))
    if getattr(args, "json", False):
        import json as _json
        print(_json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_regime_trend_run(args: argparse.Namespace) -> int:
    """[추세추종] 신호에 따라 ETF 진입/청산 실행 (기본 dry-run, --execute로 실주문)."""
    from deepsignal.live_trading.regime_trend import execute_regime_trend

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    res = execute_regime_trend(out_dir, execute=bool(getattr(args, "execute", False)))
    print(f"[{res.action}] {'실행됨' if res.executed else ('dry-run' if res.dry_run else '미실행')}")
    print(res.message)
    if res.order:
        print(f"주문: {res.order}")
    return 0 if (res.executed or res.dry_run) else 1


def cmd_regime_trend_runner(args: argparse.Namespace) -> int:
    """[추세추종] 상시 러너 — EDGE_GATE deploy + 시장시간이 맞으면 자동 진입/청산.

    시그널이 나오고 엣지가 배포되면(EDGE_GATE) 사람 개입 없이 자동 집행한다.
    매 tick에서 execute_regime_trend를 호출하며, 장외/미배포/halt면 무동작(no-op).
    """
    import signal as _signal
    import time as _time

    from deepsignal.live_trading.regime_trend import execute_regime_trend
    from deepsignal.live_trading.time_utils import now_kst_iso

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    interval = max(30, int(float(getattr(args, "interval_minutes", 5) or 5) * 60))
    execute = bool(getattr(args, "execute", False))
    _stop = {"v": False}
    for _sig in (_signal.SIGTERM, _signal.SIGINT):
        _signal.signal(_sig, lambda *a: _stop.update(v=True))
    print(f"regime-trend-runner 시작 (interval {interval//60}분, execute={execute})", flush=True)
    while not _stop["v"]:
        try:
            # 공격성 다이얼 갱신 (9~10단계: 추세추종 신규 배분 0 — 단타에 현금 양보)
            try:
                from deepsignal.risk.aggression import refresh_and_apply as _raa
                _raa()
            except Exception:
                pass
            res = execute_regime_trend(out_dir, execute=execute)
            if res.action != "HOLD" or res.executed:
                print(f"[{now_kst_iso()}] {res.action} executed={res.executed} dry={res.dry_run} | {res.message}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[tick 오류] {e!r}", flush=True)
        for _ in range(interval):
            if _stop["v"]:
                break
            _time.sleep(1)
    print("regime-trend-runner 종료", flush=True)
    return 0


def _apply_aggression_dial() -> None:
    """투자공격성 다이얼(.env DEEPSIGNAL_AGGRESSION)을 실제 env 게이트로 적용. 실패해도 무시."""
    import os as _o
    try:
        from dotenv import load_dotenv as _ld
        _ld(_o.path.join(_o.path.dirname(_o.path.abspath(__file__)), ".env"), override=False)
        from deepsignal.risk.aggression import apply_aggression
        p = apply_aggression()
        print(f"[공격성] {p.level}단계({p.band_kr}) 적용 — 레버리지 {p.leverage_max}x · 익절 {p.take_profit_mode}", flush=True)
    except Exception as _e:  # noqa: BLE001
        print(f"[공격성] 적용 생략: {_e}", flush=True)


def cmd_crypto_auto_runner(args: argparse.Namespace) -> int:
    """[실전-코인-01] 24h 코인 auto-runner (WebSocket 이벤트 드리븐)."""
    from deepsignal.crypto_trading.crypto_auto_runner import CryptoAutoRunnerConfig
    from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env
    from deepsignal.crypto_trading.crypto_paper_mode import crypto_paper_mode_enabled
    from deepsignal.crypto_trading.upbit_broker import UpbitBroker
    from deepsignal.crypto_trading.upbit_config import UpbitConfigError, load_upbit_config_from_env

    if str(getattr(args, "broker", "upbit")).lower() != "upbit":
        return 1
    ensure_crypto_runtime_env()
    _apply_aggression_dial()  # 투자공격성 다이얼 → env 게이트 반영
    execute = bool(getattr(args, "execute", False))
    if execute and crypto_paper_mode_enabled():
        print(
            "crypto-auto-runner: CRYPTO_PAPER_MODE=true blocks --execute. "
            "Set CRYPTO_PAPER_MODE=false in .env for live Upbit orders.",
            flush=True,
        )
        return 1
    try:
        cfg_upbit = load_upbit_config_from_env(dry_run=not execute)
    except UpbitConfigError as exc:
        print(f"crypto-auto-runner failed: {exc}", flush=True)
        return 1
    if cfg_upbit.is_demo:
        print(
            "\n[DEMO MODE] No Upbit API keys configured — running with mock data.\n"
            "  Set UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY in .env to enable real trading.\n",
            flush=True,
        )
    br = UpbitBroker(cfg_upbit)
    from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

    _crypto_thr = DEFAULT_ANALYSIS_CONDITIONS.crypto
    runner = CryptoAutoRunnerConfig(
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
        max_order_value=float(getattr(args, "max_order_value", 0) or 0),
        interval_minutes=float(getattr(args, "interval_minutes", 1.0) or 1.0),
        max_orders_per_day=int(getattr(args, "max_orders_per_day", 0) or 0),
        take_profit_pct=float(getattr(args, "take_profit_pct", _crypto_thr.take_profit_pct) or _crypto_thr.take_profit_pct),
        stop_loss_pct=float(getattr(args, "stop_loss_pct", _crypto_thr.stop_loss_pct) or _crypto_thr.stop_loss_pct),
        take_profit_buffer_pct=float(
            getattr(args, "take_profit_buffer_pct", _crypto_thr.take_profit_buffer_pct) or _crypto_thr.take_profit_buffer_pct
        ),
        stop_loss_buffer_pct=float(
            getattr(args, "stop_loss_buffer_pct", _crypto_thr.stop_loss_buffer_pct) or _crypto_thr.stop_loss_buffer_pct
        ),
        min_volume_ratio=float(getattr(args, "min_volume_ratio", _crypto_thr.min_volume_ratio) or _crypto_thr.min_volume_ratio),
        send_telegram=not bool(getattr(args, "no_send", False)),
        poll_telegram=bool(getattr(args, "poll", False)),
        wait_fill_seconds=float(getattr(args, "wait_fill_seconds", 0) or 0),
        fill_poll_interval=float(getattr(args, "fill_poll_interval", 3) or 3),
        network=execute,
        menu_poll_seconds=float(getattr(args, "menu_poll_seconds", 5.0) or 5.0),
        crypto_universe=str(getattr(args, "crypto_universe", _crypto_thr.market_universe) or _crypto_thr.market_universe),
        max_buy_scan_markets=int(
            getattr(args, "max_scan_markets", _crypto_thr.max_buy_scan_markets) or _crypto_thr.max_buy_scan_markets
        ),
        prefer_non_holding_buy=bool(getattr(args, "prefer_non_holding_buy", _crypto_thr.prefer_non_holding_buy)),
        rebuy_cooldown_minutes=int(
            getattr(args, "rebuy_cooldown_minutes", _crypto_thr.rebuy_cooldown_minutes) or _crypto_thr.rebuy_cooldown_minutes
        ),
        max_distinct_buy_markets_per_day=int(
            getattr(
                args,
                "max_distinct_buy_markets_per_day",
                _crypto_thr.max_distinct_buy_markets_per_day,
            )
            or _crypto_thr.max_distinct_buy_markets_per_day
        ),
        max_buy_krw_per_day=float(getattr(args, "max_buy_krw_per_day", _crypto_thr.max_buy_krw_per_day) or 0.0),
    )
    from deepsignal.crypto_trading.crypto_ws_runner import run_crypto_ws_runner_loop

    # 실행 방법(START.bat / web-ui / launchd) 무관하게 PID 파일 자동 생성
    import os as _os
    import sys as _sys
    from pathlib import Path as _Path
    _pid_file = _Path(runner.output_dir) / "WEBUI_RUNNER_PID.json"
    try:
        import json as _json
        from datetime import datetime as _dt
        _pid_file.parent.mkdir(parents=True, exist_ok=True)
        _pid_file.write_text(_json.dumps({
            "pid": _os.getpid(),
            "args": _sys.argv,
            "started_at": _dt.now().isoformat(),
        }))
    except Exception:
        pass

    try:
        run_crypto_ws_runner_loop(br, runner, execute=execute)
    finally:
        try:
            _pid_file.unlink(missing_ok=True)
        except Exception:
            pass

    return 0


def cmd_install_crypto_launchd(args: argparse.Namespace) -> int:
    """macOS LaunchAgent 등록 — crypto-auto-runner 로그인 시 자동 시작."""
    import platform

    from deepsignal.crypto_trading.crypto_launchd_installer import (
        format_install_console,
        install_crypto_launchd,
        launchd_config_from_namespace,
        project_root,
    )

    if platform.system() != "Darwin":
        print("install-crypto-launchd is macOS only.")
        return 1
    root = project_root(getattr(args, "project_dir", None))
    cfg = launchd_config_from_namespace(args)
    try:
        result = install_crypto_launchd(
            cfg,
            project_dir=root,
            load_now=not bool(getattr(args, "no_load", False)),
            sanitize_path=not bool(getattr(args, "no_sanitize_path", False)),
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"install-crypto-launchd failed: {exc}")
        return 1
    print(format_install_console(result))
    if not bool(getattr(args, "no_load", False)) and not result.get("loaded"):
        return 1
    return 0


def cmd_uninstall_crypto_launchd(args: argparse.Namespace) -> int:
    """macOS crypto LaunchAgent 제거."""
    import platform

    from deepsignal.crypto_trading.crypto_launchd_installer import uninstall_crypto_launchd

    if platform.system() != "Darwin":
        print("uninstall-crypto-launchd is macOS only.")
        return 1
    result = uninstall_crypto_launchd(
        unload=not bool(getattr(args, "keep_loaded", False)),
        remove_plist=not bool(getattr(args, "keep_plist", False)),
    )
    print(
        "\n".join(
            [
                "DeepSignal crypto launchd uninstall finished",
                f"Unload: {result.get('unload_message')}",
                f"Plist removed: {result.get('plist_removed')}",
            ]
        )
    )
    return 0


def cmd_crypto_tune_thresholds(args: argparse.Namespace) -> int:
    """코인 outcome DB 기반 take_profit / stop_loss / min_volume_ratio 자동 튜닝."""
    import json

    from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import (
        crypto_outcomes_db_path,
        run_tune_crypto_thresholds_from_outcomes,
    )

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    odb = crypto_outcomes_db_path(out_dir)
    if not odb.is_file():
        print(f"Missing {odb}. Run crypto-daily-plan / crypto-auto-runner first.")
        return 1
    tuned = run_tune_crypto_thresholds_from_outcomes(
        odb,
        output_dir=out_dir,
        lookback_days=int(getattr(args, "lookback_days", 60) or 60),
        min_sell_samples=int(getattr(args, "min_sell_samples", 3) or 3),
        min_buy_samples=int(getattr(args, "min_buy_samples", 5) or 5),
    )
    print(json.dumps(tuned.to_dict(), ensure_ascii=False, indent=2))
    print("\nApplied: outputs/CRYPTO_ACTIVE_THRESHOLDS.json")
    print("Report: outputs/CRYPTO_THRESHOLD_TUNING.md")
    return 0


def cmd_crypto_telegram_menu(args: argparse.Namespace) -> int:
    """Telegram 메뉴 봇 폴링 (자산 / 추천 분석). --poll-once: 단일 getUpdates 처리."""
    import json

    from deepsignal.config.settings import load_settings
    from deepsignal.crypto_trading.crypto_auto_runner import CryptoAutoRunnerConfig
    from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env
    from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import apply_active_thresholds_to_runner
    from deepsignal.crypto_trading.crypto_telegram_flow import load_crypto_telegram_config_from_env
    from deepsignal.crypto_trading.crypto_telegram_menu import (
        poll_telegram_updates_once,
        telegram_send_menu_message,
    )
    from deepsignal.crypto_trading.upbit_broker import UpbitBroker
    from deepsignal.crypto_trading.upbit_config import UpbitConfigError, load_upbit_config_from_env
    from deepsignal.storage.database import init_database

    ensure_crypto_runtime_env()
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    use_network = bool(getattr(args, "network", False))
    poll_once = bool(getattr(args, "poll_once", False))
    try:
        cfg_upbit = load_upbit_config_from_env(dry_run=not use_network)
    except UpbitConfigError as e:
        print(f"crypto-telegram-menu failed: {e}")
        return 1
    br = UpbitBroker(cfg_upbit)
    runner = CryptoAutoRunnerConfig(output_dir=out_dir, network=use_network)
    apply_active_thresholds_to_runner(runner, out_dir)
    tg = load_crypto_telegram_config_from_env(output_dir=out_dir)
    if bool(getattr(args, "send_menu", False)):
        telegram_send_menu_message(tg)
    if not poll_once:
        print("Use --poll-once to process pending Telegram updates once.")
        return 0
    macro_db = str(init_database(load_settings().db_path))
    summary = poll_telegram_updates_once(
        tg,
        br,
        runner_cfg=runner,
        db_path=macro_db,
        network=use_network,
        process_approvals=True,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_crypto_launchd_status(args: argparse.Namespace) -> int:
    """macOS crypto LaunchAgent 설치·실행 상태 확인."""
    import platform

    from deepsignal.crypto_trading.crypto_launchd_installer import (
        crypto_launchd_status,
        format_status_console,
    )

    if platform.system() != "Darwin":
        print("crypto-launchd-status is macOS only.")
        return 1
    print(format_status_console(crypto_launchd_status(project_dir=getattr(args, "project_dir", None))))
    return 0


def cmd_crypto_validate_ml(args: argparse.Namespace) -> int:
    """[ML] In-memory validation: replay features, TSCV, overfit report, P×N sweep."""
    from deepsignal.ml.crypto_validate_ml import ValidateMlConfig, run_full_validation

    symbols = str(getattr(args, "symbols", "") or "").strip()
    sym_list = [s.strip() for s in symbols.replace(" ", ",").split(",") if s.strip()]
    stream_dir = str(getattr(args, "stream_dir", "outputs/binance_stream") or "outputs/binance_stream")
    bars_dir = str(getattr(args, "bars_dir", "") or f"{stream_dir}/bars")
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    fee = float(getattr(args, "fee", 0.0005) or 0.0005)
    cfg = ValidateMlConfig(
        horizon_minutes=int(getattr(args, "horizon", 5) or 5),
        fee_rate=fee,
        buy_threshold=float(getattr(args, "threshold", 0.55) or 0.55),
        n_splits=int(getattr(args, "splits", 5) or 5),
        gap=int(getattr(args, "gap", 10) or 10),
        slippage_spread_frac=float(getattr(args, "slippage", 0.5) or 0.5),
        min_warmup_bars=int(getattr(args, "min_warmup", 61) or 61),
    )
    try:
        result = run_full_validation(
            bars_dir=bars_dir,
            stream_dir=stream_dir,
            output_dir=out_dir,
            symbols=sym_list,
            days=int(getattr(args, "days", 60) or 60),
            cfg=cfg,
            run_sweep=not bool(getattr(args, "no_sweep", False)),
        )
    except Exception as exc:
        print(f"crypto-validate-ml failed: {exc}")
        return 1

    print(f"Samples: {result['dataset']['n_samples']}")
    print(f"Report: {result['reports']['validation_md']}")
    if result["reports"].get("threshold_md"):
        print(f"Threshold sweep: {result['reports']['threshold_md']}")
    print(f"\n⚠️  {result['data_source_warning']}")
    for fold in result["folds"]:
        print(
            f"fold {fold['fold']}: val_auc={fold['val_auc']:.2f} "
            f"val_sharpe={fold['val_sharpe']:.1f} {fold['status']}"
        )
    return 0


def cmd_crypto_ml_suggest_config(args: argparse.Namespace) -> int:
    """Read CRYPTO_ML_THRESHOLD_REPORT.md and print suggested .env (no auto-apply)."""
    from pathlib import Path

    from deepsignal.ml.crypto_ml_config_suggest import (
        format_suggestion_report,
        parse_threshold_report,
    )

    out = Path(str(getattr(args, "output_dir", "outputs") or "outputs"))
    thresh_path = out / "CRYPTO_ML_THRESHOLD_REPORT.md"
    suggestion = parse_threshold_report(thresh_path)
    report = format_suggestion_report(
        suggestion,
        validation_path=out / "CRYPTO_ML_VALIDATION_REPORT.md",
    )
    dest = out / "CRYPTO_ML_ENV_SUGGESTION.md"
    dest.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nWrote: {dest.as_posix()}")
    return 0


def cmd_crypto_train_lgbm(args: argparse.Namespace) -> int:
    """Train LightGBM P(win) model from binance_stream 1m bars."""
    import json

    from deepsignal.ml.crypto_scalp_dataset import load_dataset_from_bars_dir
    from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig
    from deepsignal.ml.crypto_scalp_lgbm import LgbmTrainConfig, train_lgbm_classifier

    horizon = int(getattr(args, "horizon", 5) or 5)
    cost = float(getattr(args, "cost_pct", 0.2) or 0.2)
    sym_raw = str(getattr(args, "symbols", "") or "").strip()
    symbols = [s.strip().upper() for s in sym_raw.split(",") if s.strip()] or None
    bars_dir = str(getattr(args, "bars_dir", "outputs/binance_stream/bars") or "outputs/binance_stream/bars")
    model_dir = str(getattr(args, "model_dir", "outputs/models") or "outputs/models")

    print(f"Label: y=1 if +{horizon}m return > {cost}% (fees+slippage hurdle)")
    max_bars = int(getattr(args, "max_bars_per_symbol", 0) or 0)
    ds = load_dataset_from_bars_dir(
        bars_dir,
        symbols=symbols,
        label_cfg=ScalpLabelConfig(horizon_minutes=horizon, cost_pct=cost),
        max_bars_per_symbol=max_bars,
    )
    print(f"Dataset: {ds.n_samples} samples, positive rate {ds.to_dict()['positive_rate']:.3f}")
    if ds.n_samples < int(getattr(args, "min_samples", 200) or 200):
        print("Not enough samples — collect more 1m bars via binance-stream")
        return 1

    cfg = LgbmTrainConfig(
        horizon_minutes=horizon,
        cost_pct=cost,
        n_splits=int(getattr(args, "splits", 5) or 5),
        buy_threshold=float(getattr(args, "threshold", 0.55) or 0.55),
        min_train_samples=int(getattr(args, "min_samples", 200) or 200),
    )
    try:
        _model, report = train_lgbm_classifier(ds, train_cfg=cfg, model_dir=model_dir)
    except Exception as exc:
        print(f"Training failed: {exc}")
        return 1

    print(f"Model saved: {report.model_path}")
    print(f"Buy threshold (recommended): P(win) > {report.buy_threshold}")
    print("\n--- TimeSeriesSplit folds ---")
    for fold in report.folds:
        print(
            f"fold {fold['fold']}: auc={fold['auc']:.3f} "
            f"acc={fold['accuracy']:.3f} prec={fold['precision']:.3f} "
            f"rec={fold['recall']:.3f} n_val={fold['val_size']}"
        )
    print("\n--- Top feature importance ---")
    for row in report.feature_importance[:12]:
        print(f"  {row['feature']}: {row['importance']:.1f}")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2)[:2000] + "...")
    return 0


def cmd_crypto_retrain_lgbm(args: argparse.Namespace) -> int:
    """Retrain LightGBM from crypto_trades (+ bars); deploy gates + optional seq."""
    import json

    from deepsignal.ml.crypto_model_retrain import run_crypto_lgbm_retrain

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    result = run_crypto_lgbm_retrain(
        output_dir=out_dir,
        bars_dir=getattr(args, "bars_dir", None) or None,
        horizon_minutes=int(getattr(args, "horizon", 5) or 5),
        cost_pct=float(getattr(args, "cost_pct", 0.2) or 0.2),
        min_samples=int(getattr(args, "min_samples", 200) or 200),
        dry_run=bool(getattr(args, "dry_run", False)),
        also_seq=bool(getattr(args, "also_seq", False)),
        warm_start=not bool(getattr(args, "full_retrain", False)),
        full_retrain=bool(getattr(args, "full_retrain", False)),
        trade_lookback_days=int(getattr(args, "trade_lookback_days", 14) or 14),
        min_trades_deploy=int(getattr(args, "min_trades_deploy", 30) or 30),
        min_val_auc=float(getattr(args, "min_val_auc", 0.52) or 0.52),
        notify_telegram=not bool(getattr(args, "no_telegram", False)),
        seq_kind=str(getattr(args, "seq_model", "lstm") or "lstm"),
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    print(result.reason)
    if "insufficient" in result.reason:
        return 1
    return 0


def cmd_crypto_retrain_history(args: argparse.Namespace) -> int:
    """Print recent retrain_history.jsonl rows."""
    from deepsignal.ml.crypto_retrain_history import format_retrain_history_table, load_retrain_history

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    days = int(getattr(args, "days", 30) or 30)
    rows = load_retrain_history(out_dir, last_days=days)
    print(format_retrain_history_table(rows))
    return 0


def cmd_crypto_train_seq(args: argparse.Namespace) -> int:
    """Train LSTM or Transformer sequence model."""
    import json

    from deepsignal.ml.crypto_scalp_labels import ScalpLabelConfig
    from deepsignal.ml.crypto_scalp_seq_models import SeqTrainConfig, load_sequence_dataset_from_bars_dir, train_sequence_classifier, torch_available

    kind = str(getattr(args, "model", "lstm") or "lstm").lower()
    if kind not in ("lstm", "transformer"):
        print("model must be lstm or transformer")
        return 1
    if not torch_available():
        print("PyTorch required: pip install torch")
        return 1
    bars_dir = str(getattr(args, "bars_dir", "outputs/binance_stream/bars") or "outputs/binance_stream/bars")
    horizon = int(getattr(args, "horizon", 5) or 5)
    ds = load_sequence_dataset_from_bars_dir(
        bars_dir,
        seq_len=int(getattr(args, "seq_len", 30) or 30),
        label_cfg=ScalpLabelConfig(horizon_minutes=horizon, cost_pct=float(getattr(args, "cost_pct", 0.2) or 0.2)),
    )
    cfg = SeqTrainConfig(
        model_kind=kind,  # type: ignore[arg-type]
        seq_len=int(getattr(args, "seq_len", 30) or 30),
        horizon_minutes=horizon,
        min_train_samples=int(getattr(args, "min_samples", 300) or 300),
    )
    _model, report = train_sequence_classifier(
        ds,
        train_cfg=cfg,
        model_dir=str(getattr(args, "model_dir", "outputs/models") or "outputs/models"),
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_install_crypto_retrain_launchd(args: argparse.Namespace) -> int:
    import platform
    import json

    from deepsignal.ml.crypto_retrain_launchd_installer import CryptoRetrainLaunchdConfig, install_crypto_retrain_launchd

    if platform.system() != "Darwin":
        print("macOS only")
        return 1
    cfg = CryptoRetrainLaunchdConfig(
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
        horizon_minutes=int(getattr(args, "horizon", 5) or 5),
        hour=int(getattr(args, "hour", 3) or 3),
        minute=int(getattr(args, "minute", 10) or 10),
    )
    result = install_crypto_retrain_launchd(cfg, project_dir=getattr(args, "project_dir", None))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_install_binance_stream_launchd(args: argparse.Namespace) -> int:
    import platform

    from deepsignal.market_data.binance_stream_launchd_installer import (
        BinanceStreamLaunchdConfig,
        install_binance_stream_launchd,
    )

    if platform.system() != "Darwin":
        print("install-binance-stream-launchd is macOS only.")
        return 1
    cfg = BinanceStreamLaunchdConfig(
        top_n=int(getattr(args, "top", 30) or 30),
        output_dir=str(getattr(args, "output_dir", "outputs/binance_stream") or "outputs/binance_stream"),
        depth_levels=int(getattr(args, "depth_levels", 20) or 20),
    )
    try:
        result = install_binance_stream_launchd(
            cfg,
            project_dir=getattr(args, "project_dir", None),
            load_now=not bool(getattr(args, "no_load", False)),
            sanitize_path=not bool(getattr(args, "no_sanitize_path", False)),
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"install failed: {exc}")
        return 1
    import json as _json

    print(_json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("loaded") or bool(getattr(args, "no_load", False)) else 1


def cmd_install_kis_stream_launchd(args: argparse.Namespace) -> int:
    import platform

    from deepsignal.market_data.kis_stream_launchd_installer import (
        KisStreamLaunchdConfig,
        install_kis_stream_launchd,
    )

    if platform.system() != "Darwin":
        print("install-kis-stream-launchd is macOS only.")
        return 1
    cfg = KisStreamLaunchdConfig(
        paper=bool(getattr(args, "paper", False)),
        universe_size=int(getattr(args, "universe_size", 30) or 30),
    )
    try:
        result = install_kis_stream_launchd(
            cfg,
            project_dir=getattr(args, "project_dir", None),
            load_now=not bool(getattr(args, "no_load", False)),
            sanitize_path=not bool(getattr(args, "no_sanitize_path", False)),
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"install failed: {exc}")
        return 1
    import json as _json
    print(_json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("loaded") or bool(getattr(args, "no_load", False)) else 1


def cmd_uninstall_kis_stream_launchd(args: argparse.Namespace) -> int:
    import platform
    from deepsignal.market_data.kis_stream_launchd_installer import uninstall_kis_stream_launchd
    if platform.system() != "Darwin":
        return 1
    result = uninstall_kis_stream_launchd(
        unload=not bool(getattr(args, "keep_loaded", False)),
        remove_plist=not bool(getattr(args, "keep_plist", False)),
    )
    print(result)
    return 0


def cmd_kis_stream_launchd_status(args: argparse.Namespace) -> int:
    import json
    from deepsignal.market_data.kis_stream_launchd_installer import kis_stream_launchd_status
    print(json.dumps(kis_stream_launchd_status(), ensure_ascii=False, indent=2))
    return 0


def cmd_install_kis_overseas_launchd(args: argparse.Namespace) -> int:
    import platform

    from deepsignal.market_data.kis_overseas_launchd_installer import (
        KisOverseasLaunchdConfig,
        install_kis_overseas_launchd,
    )

    if platform.system() != "Darwin":
        print("install-overseas-stream-launchd is macOS only.")
        return 1
    cfg = KisOverseasLaunchdConfig(
        paper=bool(getattr(args, "paper", False)),
    )
    try:
        result = install_kis_overseas_launchd(
            cfg,
            project_dir=getattr(args, "project_dir", None),
            load_now=not bool(getattr(args, "no_load", False)),
            sanitize_path=not bool(getattr(args, "no_sanitize_path", False)),
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"install failed: {exc}")
        return 1
    import json as _json
    print(_json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("loaded") or bool(getattr(args, "no_load", False)) else 1


def cmd_uninstall_kis_overseas_launchd(args: argparse.Namespace) -> int:
    import platform
    from deepsignal.market_data.kis_overseas_launchd_installer import uninstall_kis_overseas_launchd
    if platform.system() != "Darwin":
        return 1
    result = uninstall_kis_overseas_launchd(
        unload=not bool(getattr(args, "keep_loaded", False)),
        remove_plist=not bool(getattr(args, "keep_plist", False)),
    )
    print(result)
    return 0


def cmd_kis_overseas_launchd_status(args: argparse.Namespace) -> int:
    import json
    from deepsignal.market_data.kis_overseas_launchd_installer import kis_overseas_launchd_status
    print(json.dumps(kis_overseas_launchd_status(), ensure_ascii=False, indent=2))
    return 0


def cmd_uninstall_binance_stream_launchd(args: argparse.Namespace) -> int:
    import platform

    from deepsignal.market_data.binance_stream_launchd_installer import uninstall_binance_stream_launchd

    if platform.system() != "Darwin":
        return 1
    result = uninstall_binance_stream_launchd(
        unload=not bool(getattr(args, "keep_loaded", False)),
        remove_plist=not bool(getattr(args, "keep_plist", False)),
    )
    print(result)
    return 0


def cmd_binance_stream_launchd_status(args: argparse.Namespace) -> int:
    import json

    from deepsignal.market_data.binance_stream_launchd_installer import binance_stream_launchd_status

    print(json.dumps(binance_stream_launchd_status(), ensure_ascii=False, indent=2))
    return 0


def cmd_crypto_predict_lgbm(args: argparse.Namespace) -> int:
    """P(win) inference from live_state features + trained LightGBM."""
    import json
    from pathlib import Path

    import numpy as np

    from deepsignal.market_data.feature_engine import FeatureEngine
    from deepsignal.ml.crypto_scalp_lgbm import load_lgbm_model, predict_proba

    horizon = int(getattr(args, "horizon", 5) or 5)
    model_path = Path(
        getattr(args, "model", None)
        or Path(getattr(args, "model_dir", "outputs/models")) / f"crypto_scalp_lgbm_{horizon}m.txt"
    )
    if not model_path.is_file():
        print(f"Model not found: {model_path}")
        return 1
    state_path = Path(
        getattr(args, "live_state", None)
        or Path(getattr(args, "output_dir", "outputs/binance_stream")) / "live_state.json"
    )
    threshold = float(getattr(args, "threshold", 0.55) or 0.55)
    eng = FeatureEngine()
    eng.ingest_live_state(json.loads(state_path.read_text(encoding="utf-8")))
    model = load_lgbm_model(model_path)
    symbols = [str(s).upper() for s in eng.compute_all().keys()]
    rows: list[tuple[str, float]] = []
    for sym in symbols:
        vec = eng.compute(sym)
        p = float(predict_proba(model, vec.reshape(1, -1))[0])
        rows.append((sym, p))
    rows.sort(key=lambda x: x[1], reverse=True)
    print(f"Model: {model_path}")
    print(f"Threshold: {threshold} (BUY candidate if P(win) > threshold)")
    for sym, p in rows[: int(getattr(args, "top", 15) or 15)]:
        flag = "BUY?" if p >= threshold else "skip"
        print(f"  {sym}: P(win)={p:.3f} [{flag}]")
    return 0


def cmd_fetch_fear_greed(args: argparse.Namespace) -> int:
    """Fetch Alternative.me Fear & Greed index into outputs/fear_greed_cache.json."""
    from pathlib import Path

    from deepsignal.market_data.feature_engine.fear_greed import default_cache_path, update_fear_greed_cache

    out = Path(str(getattr(args, "output_dir", "outputs") or "outputs"))
    path = default_cache_path(out)
    try:
        data = update_fear_greed_cache(path, force=bool(getattr(args, "force", False)))
    except Exception as exc:
        print(f"fetch-fear-greed failed: {exc}")
        return 1
    print(f"Fear & Greed: {data.get('value')} ({data.get('value_classification')}) date={data.get('date')}")
    print(f"Cache: {path}")
    return 0


def cmd_binance_features(args: argparse.Namespace) -> int:
    """Compute per-coin feature numpy vectors from binance_stream live_state."""
    import json
    from pathlib import Path

    from deepsignal.market_data.feature_engine import FEATURE_NAMES, FeatureEngine

    state_path = Path(
        getattr(args, "live_state", None)
        or Path(getattr(args, "output_dir", "outputs/binance_stream")) / "live_state.json"
    )
    if not state_path.is_file():
        print(f"live_state not found: {state_path}")
        return 1
    fg_path = getattr(args, "fear_greed", None)
    vectors = FeatureEngine.from_live_state_path(
        state_path,
        btc_symbol=str(getattr(args, "btc_symbol", "BTCUSDT") or "BTCUSDT"),
        fear_greed_path=fg_path,
    )
    out_dir = Path(getattr(args, "output_dir", "outputs/binance_stream") or "outputs/binance_stream")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": state_path.as_posix(),
        "feature_names": list(FEATURE_NAMES),
        "symbols": {},
    }
    for sym, vec in sorted(vectors.items()):
        payload["symbols"][sym] = vec.tolist()
        if bool(getattr(args, "verbose", False)):
            print(f"\n{sym} ({len(vec)})")
            for name, val in zip(FEATURE_NAMES, vec):
                print(f"  {name}: {val:.6f}")
    out_path = out_dir / "feature_vectors.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Features: {len(vectors)} symbols -> {out_path}")
    return 0


def cmd_binance_stream(args: argparse.Namespace) -> int:
    """Binance WebSocket: trades, order book, funding, multi-TF OHLCV."""
    import json
    from pathlib import Path

    from deepsignal.market_data.binance_stream import BinanceStreamConfig, run_binance_stream
    from deepsignal.market_data.feature_engine.fear_greed import default_cache_path, update_fear_greed_cache

    sym_raw = str(getattr(args, "symbols", "") or "").strip()
    symbols = tuple(s.strip().upper() for s in sym_raw.split(",") if s.strip())
    cfg = BinanceStreamConfig(
        output_dir=str(getattr(args, "output_dir", "outputs/binance_stream") or "outputs/binance_stream"),
        top_n=int(getattr(args, "top", 30) or 30),
        symbols=symbols,
        depth_levels=int(getattr(args, "depth_levels", 20) or 20),
        include_funding=not bool(getattr(args, "no_funding", False)),
        state_flush_seconds=float(getattr(args, "flush_seconds", 5.0) or 5.0),
        ob_snapshot_seconds=float(getattr(args, "ob_snapshot_seconds", 10.0) or 10.0),
    )
    try:
        update_fear_greed_cache(default_cache_path(Path(cfg.output_dir).parent), force=False)
    except Exception:
        pass
    duration = float(getattr(args, "duration", 0.0) or 0.0)
    print(
        f"DeepSignal binance-stream starting (top={cfg.top_n}, duration={duration or 'until Ctrl+C'})"
    )
    print(f"Output: {cfg.output_dir}")
    try:
        result = run_binance_stream(cfg, duration_seconds=duration)
    except KeyboardInterrupt:
        print("\nbinance-stream stopped (KeyboardInterrupt)")
        return 0
    except Exception as exc:
        print(f"binance-stream failed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_overseas_stream(args: argparse.Namespace) -> int:
    """KIS WebSocket: 해외주식 체결 실시간 수집 + OHLCV 봉 생성."""
    import asyncio
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        from dotenv import load_dotenv as _ld
        _ld()
    except ImportError:
        pass

    from deepsignal.live_trading.broker.kis_config import KisConfigError, load_kis_config_from_env
    try:
        kis_cfg = load_kis_config_from_env()
    except KisConfigError as e:
        print(f"overseas-stream 설정 오류: {e}")
        return 1

    from deepsignal.market_data.kis_stream.overseas_pipeline import KisOverseasPipeline, OverseasStreamConfig, DEFAULT_OVERSEAS_SYMBOLS

    cfg = OverseasStreamConfig()
    if getattr(args, "paper", False):
        cfg.use_live_ws = False
    elif getattr(args, "live_ws", False):
        cfg.use_live_ws = True

    print(f"DeepSignal overseas-stream 시작 ({'모의' if not cfg.use_live_ws else '실전'} WS, {len(cfg.symbols)}개 심볼)")
    print(f"출력: {cfg.resolved_output_dir}")
    print(f"심볼: {', '.join(f'{e}:{t}' for e,t in cfg.symbols[:5])}...")

    pipeline = KisOverseasPipeline(
        cfg=cfg,
        app_key=kis_cfg.app_key,
        app_secret=kis_cfg.app_secret,
        rest_base_url=kis_cfg.base_url,
    )

    async def _run():
        await pipeline.run()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\noverseas-stream 중단")
    return 0


def cmd_overseas_auto_runner(args: argparse.Namespace) -> int:
    """해외주식(미국장) 무인 자동매매 러너.

    미국 정규장 시간에 주기적으로 분석→plan→매수→TP/SL매도 반복.
    '전체 일시정지' 토글(CRYPTO_AUTO_RUNNER_STATE.json) 적용.
    실주문은 OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL=true + KIS_ENV=live 시에만.
    """
    import os
    import time as _time
    from dotenv import load_dotenv
    from deepsignal.live_trading.overseas_auto_execute import run_overseas_auto_tick, is_us_market_open

    load_dotenv()
    output_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    interval_min = float(os.environ.get("OVERSEAS_RUNNER_INTERVAL_MIN", "5") or 5)
    interval_sec = max(60.0, interval_min * 60.0)

    # 텔레그램 알림 콜백
    def _tg(text: str) -> None:
        try:
            tok = os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
            chat = os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
            if not tok or not chat:
                return
            from deepsignal.live_trading.telegram.approval import telegram_api_post
            telegram_api_post("sendMessage",
                {"chat_id": chat, "text": text, "parse_mode": "Markdown"}, bot_token=tok)
        except Exception:
            pass

    once  = bool(getattr(args, "once", False))
    force = bool(getattr(args, "force", False))
    gate = os.environ.get("OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL", "false")

    if once:
        # 검증용 1회 실행 (--force면 장외에도 plan 생성·dry-run)
        print(f"[once] overseas tick 실행 (force={force}, 게이트={gate}, KIS_ENV={os.environ.get('KIS_ENV','paper')})")
        summary = run_overseas_auto_tick(output_dir, tg_notify=_tg, force_market=force)
        skip = summary.get("skipped")
        if skip:
            print(f"[once] skip: {skip} (장외면 --force 사용)")
        else:
            buys = summary.get("buy", [])
            sells = summary.get("sell", [])
            print(f"[once] 매수 {len(buys)}건 / 매도 {len(sells)}건")
            for b in buys:
                print(f"   매수: {b.get('symbol')} {b.get('quantity')}주 @ ${b.get('limit_price_usd')} "
                      f"status={b.get('status')} dry_run={b.get('dry_run')}")
            for s in sells:
                print(f"   매도: {s.get('symbol')} {s.get('quantity')}주 status={s.get('status')} dry_run={s.get('dry_run')}")
        import json as _json
        print("[once] 요약:", _json.dumps(summary, ensure_ascii=False)[:500])
        return 0

    print(f"DeepSignal overseas-auto-runner 시작 (주기 {interval_min:.0f}분, output={output_dir})")
    print(f"  게이트: OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL={gate}, KIS_ENV={os.environ.get('KIS_ENV','paper')}")
    try:
        while True:
            try:
                try:
                    from deepsignal.risk.aggression import refresh_and_apply as _raa
                    _raa()
                except Exception:
                    pass
                summary = run_overseas_auto_tick(output_dir, tg_notify=_tg)
                skip = summary.get("skipped")
                if skip:
                    # 장외/일시정지: 조용히 대기
                    pass
                else:
                    nb = len(summary.get("buy", []))
                    ns = len(summary.get("sell", []))
                    if nb or ns:
                        print(f"[tick] 매수 {nb}건 / 매도 {ns}건")
            except Exception as exc:
                print(f"[tick] 오류 (비치명적): {exc}")
            _time.sleep(interval_sec)
    except KeyboardInterrupt:
        print("\noverseas-auto-runner 중단")
    return 0


def cmd_kis_stream(args: argparse.Namespace) -> int:
    """KIS WebSocket: 국내주식 체결·호가 실시간 수집 + OHLCV 봉 생성."""
    import asyncio
    import json
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        from dotenv import load_dotenv as _ld
        _ld()
    except ImportError:
        pass

    from deepsignal.live_trading.broker.kis_config import KisConfigError, load_kis_config_from_env

    # KIS 설정 로드
    try:
        kis_cfg = load_kis_config_from_env()
    except KisConfigError as e:
        print(f"kis-stream 설정 오류: {e}")
        return 1

    from deepsignal.market_data.kis_stream.config import load_kis_stream_config_from_env
    from deepsignal.market_data.kis_stream.pipeline import KisRealtimePipeline

    sym_raw = str(getattr(args, "symbols", "") or "").strip()
    symbols = [s.strip() for s in sym_raw.split(",") if s.strip()] if sym_raw else None

    output_dir = str(getattr(args, "output_dir", "") or "")
    cfg = load_kis_stream_config_from_env(symbols=symbols, output_dir=output_dir)

    # --paper / --live 플래그로 KIS_ENV 오버라이드
    if getattr(args, "paper", False):
        cfg.use_live_ws = False
    elif getattr(args, "live_ws", False):
        cfg.use_live_ws = True

    duration = float(getattr(args, "duration", 0.0) or 0.0)

    print(
        f"DeepSignal kis-stream 시작 "
        f"({'모의' if not cfg.use_live_ws else '실전'} WS, "
        f"{len(cfg.symbols)}개 심볼)"
    )
    print(f"출력: {cfg.resolved_output_dir}")
    print(f"심볼: {', '.join(cfg.symbols[:5])}{'...' if len(cfg.symbols) > 5 else ''}")

    pipeline = KisRealtimePipeline(
        cfg=cfg,
        app_key=kis_cfg.app_key,
        app_secret=kis_cfg.app_secret,
        rest_base_url=kis_cfg.base_url,
    )

    async def _run() -> None:
        if duration > 0:
            try:
                await asyncio.wait_for(pipeline.run(), timeout=duration)
            except asyncio.TimeoutError:
                print(f"\nkis-stream 종료 (duration={duration}s)")
        else:
            await pipeline.run()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pipeline.stop()
        print("\nkis-stream 중단 (KeyboardInterrupt)")
    except Exception as exc:
        print(f"kis-stream 오류: {exc}")
        return 1

    status = pipeline.get_status()
    stats = status.get("stats", {})
    print(
        f"\n[집계] trades={stats.get('trades', 0)}, "
        f"bars={stats.get('bars_closed', 0)}, "
        f"orderbooks={stats.get('orderbooks', 0)}, "
        f"errors={stats.get('errors', 0)}"
    )
    return 0


def cmd_kis_check(args: argparse.Namespace) -> int:
    """KIS 환경 변수 검증. `--network` 시 OAuth 토큰 발급만 시도 (주문 없음)."""
    from deepsignal.live_trading.kis_broker import KISBroker
    from deepsignal.live_trading.kis_config import KisConfigError, load_kis_config_from_env, validate_kis_config

    try:
        cfg = load_kis_config_from_env()
    except KisConfigError as e:
        print(f"DeepSignal kis-check failed: {e}")
        return 1

    errs, warns = validate_kis_config(cfg)
    for w in warns:
        print(f"Warning: {w}")
    if errs:
        for e in errs:
            print(f"Error: {e}")
        print("DeepSignal kis-check failed (validation)")
        return 1

    if bool(getattr(args, "network", False)):
        br = KISBroker(cfg, safe_mode=True)
        br.get_access_token()
        print("DeepSignal kis-check OK (token issued; no order API)")
        return 0

    print("DeepSignal kis-check OK (config only; use --network to test OAuth)")
    return 0


def cmd_live_approve(args: argparse.Namespace) -> int:
    """승인된 live order plan 검증·시뮬·[실전-4] 단발 실매수(KIS·가드)."""
    from dataclasses import asdict
    from datetime import datetime
    from pathlib import Path

    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.dry_run_broker import DryRunBroker
    from deepsignal.live_trading.kis_broker import KISBroker
    from deepsignal.live_trading.kis_config import KisConfigError, load_kis_config_from_env, validate_kis_config
    from deepsignal.live_trading.live_execution_guard import LiveExecutionPolicy
    from deepsignal.live_trading.live_order_executor import (
        execute_live_order_plan,
        load_live_order_plan,
        write_live_approval_audit_log,
    )
    from deepsignal.storage.database import init_database

    plan_path = str(args.plan)
    approved = bool(getattr(args, "approved", False))
    dry_run = bool(getattr(args, "dry_run", True))
    execute_flag = bool(getattr(args, "execute", False))
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    broker_name = str(getattr(args, "broker", "dry-run") or "dry-run").strip().lower()

    allow_raw = getattr(args, "allow_symbol", None)
    allow_syms = list(allow_raw) if allow_raw else None
    live_policy = LiveExecutionPolicy(
        max_total_order_value=float(getattr(args, "max_total_order_value", 100_000.0)),
        max_single_order_value=float(getattr(args, "max_single_order_value", 50_000.0)),
        max_orders=int(getattr(args, "max_orders", 1)),
        allow_live_env=bool(getattr(args, "allow_live_env", False)),
        allow_symbols=allow_syms,
    )

    approval_audit_filename: str | None = None
    settings = load_settings()
    db_path_str = str(init_database(settings.db_path))

    def _write_early_audit(
        *,
        broker_label: str,
        status: str,
        errors: list[str],
        warnings: list[str],
    ) -> Path:
        audit_early: dict[str, object] = {
            "plan_path": plan_path,
            "plan_status": None,
            "approved": approved,
            "dry_run": dry_run,
            "execute": execute_flag,
            "broker": broker_label,
            "status": status,
            "orders": [],
            "results": [],
            "warnings": warnings + errors,
            "success": False,
            "errors": errors,
            "실제_주문_없음": True,
            "실제_주문_발생_가능성": False,
            "actual_order_attempted": False,
            "actual_order_count": 0,
            "live_guard_passed": False,
            "preflight_passed": False,
            "final_confirm_matched": False,
            "live_policy": asdict(live_policy),
            "kis_env": None,
            "blocked_reason": None,
        }
        return write_live_approval_audit_log(
            audit_early,
            output_dir=out_dir,
            audit_filename=approval_audit_filename,
        )

    broker: DryRunBroker | KISBroker
    if broker_name == "kis":
        try:
            cfg = load_kis_config_from_env()
        except KisConfigError as e:
            ap = _write_early_audit(
                broker_label="KISBroker",
                status="KIS_CONFIG_ERROR",
                errors=[str(e)],
                warnings=[],
            )
            print("DeepSignal live approval finished (not completed)")
            print(f"Plan: {plan_path}")
            print(f"Status: KIS_CONFIG_ERROR")
            print(str(e))
            print(f"Audit Log: {ap.as_posix()}")
            return 1
        verr, vwarn = validate_kis_config(cfg)
        for w in vwarn:
            print(f"Warning: {w}")
        if verr:
            ap = _write_early_audit(
                broker_label="KISBroker",
                status="KIS_CONFIG_VALIDATION_FAILED",
                errors=verr,
                warnings=list(vwarn),
            )
            print("DeepSignal live approval finished (not completed)")
            print(f"Plan: {plan_path}")
            print("Status: KIS_CONFIG_VALIDATION_FAILED")
            for e in verr:
                print(e)
            print(f"Audit Log: {ap.as_posix()}")
            return 1
        broker = KISBroker(cfg, safe_mode=True)
    else:
        broker = DryRunBroker()

    if execute_flag and broker_name == "kis":
        print("*** DeepSignal: REAL MONEY order path requested (--execute) ***")
        print("*** BUY / LIMIT / domestic 6-digit / KIS_ENV=live / guards required ***")
        ts = datetime.now()
        approval_audit_filename = f"live_approval_audit_{ts.strftime('%Y%m%d')}_{ts.strftime('%H%M%S')}.json"
        audit_path_preview = Path(out_dir) / approval_audit_filename
        try:
            pl = load_live_order_plan(plan_path)
            print("--- Pre-execution summary (plan file, read-only) ---")
            tot_ev = 0.0
            for o in pl.orders:
                tot_ev += float(o.estimated_order_value)
                print(
                    f"  Order: symbol={o.symbol} qty={o.estimated_qty} "
                    f"limit(est.)={o.estimated_price} est.value={o.estimated_order_value}"
                )
            print(f"  Total estimated order value: {tot_ev}")
            print(f"  Audit log path: {audit_path_preview.as_posix()}")
        except Exception as e:
            print(f"Warning: could not print pre-execution plan summary: {e}")

    require_runbook = bool(getattr(args, "require_pre_trade_runbook", False))
    runbook_path = str(getattr(args, "pre_trade_runbook", "") or "").strip() or None
    runbook_max_age = int(getattr(args, "pre_trade_runbook_max_age_minutes", 10) or 10)

    exec_result = execute_live_order_plan(
        plan_path,
        broker,
        approved=approved,
        execute=execute_flag,
        dry_run=dry_run,
        final_confirm=str(getattr(args, "final_confirm", "") or ""),
        live_policy=live_policy,
        db_path=db_path_str if execute_flag and broker_name == "kis" else None,
        output_dir=out_dir,
        stale_snapshot_minutes=int(getattr(args, "stale_snapshot_minutes", 10) or 10),
        require_pre_trade_runbook=require_runbook and execute_flag and broker_name == "kis",
        pre_trade_runbook_path=runbook_path,
        pre_trade_runbook_max_age_minutes=runbook_max_age,
    )

    warns = list(exec_result.get("plan_warnings") or [])
    errs = list(exec_result.get("errors") or [])
    audit: dict[str, object] = {
        "plan_path": exec_result["plan_path"],
        "plan_status": exec_result.get("plan_status"),
        "approved": approved,
        "dry_run": dry_run,
        "execute": execute_flag,
        "broker": exec_result["broker"],
        "status": exec_result["status"],
        "orders": exec_result.get("orders") or [],
        "results": exec_result.get("results") or [],
        "warnings": warns + errs,
        "success": bool(exec_result.get("success")),
        "errors": errs,
    }
    for key in (
        "실제_주문_없음",
        "실제_주문_발생_가능성",
        "final_confirm_matched",
        "live_guard_passed",
        "preflight_passed",
        "live_policy",
        "kis_env",
        "actual_order_attempted",
        "actual_order_count",
        "blocked_reason",
        "guard_result",
        "duplicate_risk_detected",
        "stale_snapshot",
        "reconcile_mismatch",
        "recent_orders_found",
        "partial_fill_risk",
        "trading_session",
        "trading_session_open",
        "trading_session_reason",
        "require_pre_trade_runbook",
        "pre_trade_runbook_guard",
        "pre_trade_runbook_passed",
        "pre_trade_runbook_path",
        "pre_trade_runbook_age_seconds",
    ):
        if key in exec_result:
            audit[key] = exec_result[key]
    if "live_policy" not in audit or audit["live_policy"] is None:
        audit["live_policy"] = asdict(live_policy)
    if "실제_주문_없음" not in audit:
        audit["실제_주문_없음"] = True

    apath = write_live_approval_audit_log(
        audit,
        output_dir=out_dir,
        audit_filename=approval_audit_filename,
    )

    if execute_flag and broker_name == "kis" and exec_result.get("actual_order_attempted"):
        from deepsignal.live_trading.order_guard import persist_execute_results_to_history

        persist_execute_results_to_history(
            db_path_str,
            broker="kis",
            audit_path=apath.as_posix(),
            orders=list(exec_result.get("orders") or []),
            results=list(exec_result.get("results") or []),
        )

    st = exec_result.get("status")
    ok_done = bool(exec_result.get("success")) and st in (
        "DRY_RUN_COMPLETED",
        "KIS_SAFE_MODE_COMPLETED",
        "KIS_LIVE_ORDER_COMPLETED",
    )
    if ok_done:
        if st == "DRY_RUN_COMPLETED":
            print("DeepSignal live approval dry-run finished")
        elif st == "KIS_SAFE_MODE_COMPLETED":
            print("DeepSignal live approval KIS safe-mode finished")
        else:
            print("DeepSignal live approval KIS live order finished (submitted)")
        if exec_result.get("pre_trade_runbook_passed"):
            print("Pre-trade runbook: OK")
            rb_path = exec_result.get("pre_trade_runbook_path")
            if rb_path:
                print(f"Runbook report: {rb_path}")
        print(f"Plan: {plan_path}")
        print(f"Status: {st}")
        print("Orders:")
        for row in exec_result.get("results") or []:
            sym = row.get("symbol", "")
            side = row.get("side", "")
            qty = row.get("quantity", 0)
            sp = row.get("submitted_price")
            lim = f"limit={float(sp):.2f}" if isinstance(sp, (int, float)) and sp is not None else "limit=-"
            rst = row.get("status", "")
            print(f"{rst} {side} {sym} qty={qty} {lim}")
        if exec_result.get("actual_order_attempted"):
            tot = 0.0
            for row in exec_result.get("orders") or []:
                try:
                    tot += float(row.get("estimated_value") or 0)
                except (TypeError, ValueError):
                    pass
            print(f"Estimated total order value (plan): {tot:.2f}")
        print(f"Audit Log: {apath.as_posix()}")
    else:
        blocked = exec_result.get("status") == "LIVE_EXECUTION_BLOCKED_BY_RUNBOOK"
        if blocked:
            print("DeepSignal live approval finished (blocked)")
        else:
            print("DeepSignal live approval finished (not completed)")
        print(f"Plan: {plan_path}")
        print(f"Status: {exec_result.get('status')}")
        if blocked:
            reason = exec_result.get("blocked_reason") or (
                "recent PRE_TRADE_READY runbook not found or expired"
            )
            print(f"Reason: {reason}")
        for e in errs:
            print(e)
        print(f"Audit Log: {apath.as_posix()}")

    return 0 if ok_done else 1


def cmd_trading_session_check(args: argparse.Namespace) -> int:
    """[실전-9] 주문 가능 시간(정규장·주말·휴일) 조회. HTTP 없음."""
    from datetime import datetime

    from deepsignal.live_trading.trading_session import (
        TradingSessionPolicy,
        is_trading_session_open,
        load_trading_session_policy_from_env,
        parse_holidays,
    )

    pol = load_trading_session_policy_from_env()
    if str(getattr(args, "market", "") or "").strip():
        pol.market = str(args.market).strip()
    if bool(getattr(args, "allow_after_hours", False)):
        pol.allow_after_hours = True
    extra_holidays: list[str] = []
    for h in getattr(args, "holiday", None) or []:
        extra_holidays.extend(parse_holidays(str(h)))
    if extra_holidays:
        base_h = list(pol.holidays or [])
        pol.holidays = base_h + [x for x in extra_holidays if x not in base_h]

    now_raw = getattr(args, "now", None)
    now_dt: datetime | None = None
    if now_raw:
        now_dt = datetime.fromisoformat(str(now_raw).strip())

    result = is_trading_session_open(now=now_dt, policy=pol)
    print("DeepSignal trading session")
    print(f"Market: {result.market}")
    print(f"Now: {result.now}")
    print(f"Timezone: {result.timezone}")
    print(f"Session: {result.session_open} - {result.session_close}")
    print(f"Status: {'OPEN' if result.is_open else 'CLOSED'}")
    print(f"Reason: {result.reason}")
    for w in result.warnings:
        print(w)
    return 0 if result.is_open else 1


def cmd_live_order_status(args: argparse.Namespace) -> int:
    """[실전-5]~[실전-8] 감사 로그 파싱·KIS 주문/체결 조회·fill DB 저장."""
    from dataclasses import asdict

    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.fill_tracker import (
        build_partial_fill_status,
        extract_fills_from_kis_status_dicts,
        fill_summary_for_display,
        format_fill_summary_console,
        partial_fill_status_from_kis_status,
        persist_fill_records_to_db,
        write_fill_summary_report,
    )
    from deepsignal.live_trading.kis_broker import KISBroker
    from deepsignal.live_trading.kis_config import KisConfigError, load_kis_config_from_env
    from deepsignal.live_trading.kis_order_status import (
        extract_order_ids_from_audit,
        load_audit_log,
        write_order_status_report,
    )
    from deepsignal.storage.database import init_database

    audit_path = str(getattr(args, "audit", "") or "")
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    network = bool(getattr(args, "network", False))
    oid_arg = getattr(args, "order_id", None)
    sym_arg = getattr(args, "symbol", None)
    start_d = getattr(args, "start_date", None)
    end_d = getattr(args, "end_date", None)

    audit = load_audit_log(audit_path)
    ids = extract_order_ids_from_audit(audit)
    if oid_arg:
        s = str(oid_arg).strip()
        if s and s not in ids:
            ids.insert(0, s)

    kis_rows: list[dict[str, object]] | None = None
    fill_summaries: list[dict[str, object]] = []
    fills_saved: dict[str, int] = {"inserted": 0, "skipped": 0}
    db_path: str | None = None
    if network:
        try:
            cfg = load_kis_config_from_env()
        except KisConfigError as e:
            print(f"DeepSignal live order status: KIS config error: {e}")
            return 1
        br = KISBroker(cfg, safe_mode=True)
        settings = load_settings()
        db_path = str(init_database(settings.db_path))
        kis_rows = []
        seen_summary_orders: set[str] = set()
        for oid in ids or ([None] if (sym_arg or start_d or end_d) else []):
            rows = br.get_order_status(
                order_id=str(oid).strip() if oid else None,
                symbol=str(sym_arg).strip() if sym_arg else None,
                start_date=str(start_d).strip() if start_d else None,
                end_date=str(end_d).strip() if end_d else None,
            )
            for st in rows:
                d = asdict(st)
                row_dict: dict[str, object] = {
                    "order_id": d.get("order_id"),
                    "symbol": d.get("symbol"),
                    "side": d.get("side"),
                    "status": d.get("status"),
                    "message": d.get("message"),
                    "quantity": d.get("quantity"),
                    "filled_quantity": d.get("filled_quantity"),
                    "remaining_quantity": d.get("remaining_quantity"),
                    "avg_fill_price": d.get("avg_fill_price"),
                    "raw": d.get("raw"),
                }
                kis_rows.append(row_dict)
                pfs = partial_fill_status_from_kis_status(row_dict)
                if pfs and str(pfs.order_id or "") not in seen_summary_orders:
                    seen_summary_orders.add(str(pfs.order_id or ""))
                    fill_summaries.append(fill_summary_for_display(pfs))
        fill_recs = extract_fills_from_kis_status_dicts(kis_rows or [])
        ins, sk = persist_fill_records_to_db(db_path, fill_recs)
        fills_saved = {"inserted": ins, "skipped": sk}
        if fill_recs and not fill_summaries:
            from deepsignal.storage.database import aggregate_fill_summary

            for oid in {str(r.order_id) for r in fill_recs if r.order_id}:
                agg = aggregate_fill_summary(
                    db_path,
                    broker="kis",
                    order_id=oid,
                    symbol=str(sym_arg).strip() if sym_arg else None,
                )
                fill_summaries.append(
                    fill_summary_for_display(build_partial_fill_status(agg, order_id=oid))
                )

    jp, mp = write_order_status_report(
        audit_path=audit_path,
        audit=audit,
        extracted_order_ids=ids,
        kis_statuses=kis_rows,
        output_dir=out_dir,
        fill_summaries=[dict(x) for x in fill_summaries],
        fills_saved=fills_saved,
    )

    print("DeepSignal live order status")
    print(f"Audit: {audit_path}")
    print("Extracted order ids:")
    if ids:
        for x in ids:
            print(f"- {x}")
    else:
        print("- (none)")
    if network:
        print("KIS query:")
        if kis_rows:
            for r in kis_rows:
                print(f"- status: {r.get('status')} order_id={r.get('order_id')} symbol={r.get('symbol')}")
                print(
                    f"  qty={r.get('quantity')} filled={r.get('filled_quantity')} "
                    f"remaining={r.get('remaining_quantity')} avg_price={r.get('avg_fill_price')}"
                )
        else:
            print("- (no rows)")
        if fill_summaries:
            fj, fm = write_fill_summary_report(
                [dict(x) for x in fill_summaries],
                output_dir=out_dir,
                audit_path=audit_path,
            )
            print("Fill summaries:")
            for fs in fill_summaries:
                print(format_fill_summary_console(fs))
                print("---")
            print(f"Fills DB: inserted={fills_saved.get('inserted')} skipped={fills_saved.get('skipped')}")
            print(f"Fill JSON: {fj.as_posix()}")
            print(f"Fill Markdown: {fm.as_posix()}")
    else:
        print("(KIS query skipped; use --network to call inquire-daily-ccld)")
    print(f"JSON report: {jp.as_posix()}")
    print(f"Markdown: {mp.as_posix()}")
    return 0


def cmd_live_fill_summary(args: argparse.Namespace) -> int:
    """[실전-8] DB 체결 이력 집계·partial fill 요약 (HTTP 없음)."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.fill_tracker import (
        build_partial_fill_status,
        fill_summary_for_display,
        format_fill_summary_console,
        write_fill_summary_report,
    )
    from deepsignal.live_trading.kis_order_status import extract_order_ids_from_audit, load_audit_log
    from deepsignal.storage.database import aggregate_fill_summary, init_database, load_real_fills_by_order

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    oid_arg = getattr(args, "order_id", None)
    audit_path = str(getattr(args, "audit", "") or "")
    sym_arg = getattr(args, "symbol", None)

    order_ids: list[str] = []
    if oid_arg:
        order_ids.append(str(oid_arg).strip())
    if audit_path:
        audit = load_audit_log(audit_path)
        for x in extract_order_ids_from_audit(audit):
            if x not in order_ids:
                order_ids.append(x)
    if not order_ids:
        print("DeepSignal live-fill-summary: --order-id or --audit is required.")
        return 1

    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    summaries: list[dict[str, object]] = []
    for oid in order_ids:
        agg = aggregate_fill_summary(
            db_path,
            broker="kis",
            order_id=oid,
            symbol=str(sym_arg).strip() if sym_arg else None,
        )
        fills = load_real_fills_by_order(db_path, broker="kis", order_id=oid)
        if fills and not agg.get("fill_count"):
            agg["fill_count"] = len(fills)
        pfs = build_partial_fill_status(agg, order_id=oid, symbol=str(sym_arg) if sym_arg else None)
        summaries.append(fill_summary_for_display(pfs))

    print("DeepSignal live fill summary")
    for fs in summaries:
        print(format_fill_summary_console(fs))
        print("---")
    fj, fm = write_fill_summary_report(
        [dict(x) for x in summaries],
        output_dir=out_dir,
        audit_path=audit_path or None,
    )
    print(f"JSON: {fj.as_posix()}")
    print(f"Markdown: {fm.as_posix()}")
    return 0


def cmd_live_sync_account(args: argparse.Namespace) -> int:
    """[실전-5]~[실전-6] KIS 잔고·포지션 스냅샷 파일·선택 DB (--network 필수)."""
    if not bool(getattr(args, "network", False)):
        print("DeepSignal live-sync-account: --network is required for KIS HTTP (inquire-balance).")
        return 1
    if str(getattr(args, "broker", "kis")).strip().lower() != "kis":
        print("DeepSignal live-sync-account: only --broker kis is supported.")
        return 1

    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.kis_broker import KISBroker
    from deepsignal.live_trading.kis_config import KisConfigError, load_kis_config_from_env
    from deepsignal.live_trading.live_account_sync import (
        build_account_snapshot_payload,
        persist_live_account_snapshot_to_db,
        summarize_kis_balance_raw,
        write_kis_account_debug_summary,
        write_live_account_snapshot_paths,
    )
    from deepsignal.storage.database import init_database

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    save_db = bool(getattr(args, "save_db", True))
    try:
        cfg = load_kis_config_from_env()
    except KisConfigError as e:
        print(f"DeepSignal live-sync-account: KIS config error: {e}")
        return 1

    br = KISBroker(cfg, safe_mode=True)
    payload = build_account_snapshot_payload(br)
    if bool(getattr(args, "debug_raw", False)):
        summary = summarize_kis_balance_raw(br.last_balance_response_body)
        print("KIS raw debug (masked):")
        print(f"- top_level_keys: {summary.get('top_level_keys')}")
        print(f"- output1 rows: {(summary.get('output1') or {}).get('row_count')} keys={(summary.get('output1') or {}).get('keys')}")
        print(f"- output2 rows: {(summary.get('output2') or {}).get('row_count')} keys={(summary.get('output2') or {}).get('keys')}")
        dbg = write_kis_account_debug_summary(br.last_balance_response_body, output_dir=out_dir)
        print(f"Debug JSON: {dbg.as_posix()}")
    jp, mp = write_live_account_snapshot_paths(payload, output_dir=out_dir)
    cash = payload.get("cash") or {}
    poss = payload.get("positions") or []

    print("DeepSignal live account snapshot")
    print(f"Cash: {cash.get('cash')} (withdrawable: {cash.get('withdrawable_cash')})")
    print("Positions:")
    for p in poss:
        print(f"{p.get('symbol')} qty={p.get('quantity')} avg={p.get('avg_price')} value={p.get('market_value')}")
    if not poss:
        print("(none)")
    print(f"JSON: {jp.as_posix()}")
    print(f"Markdown: {mp.as_posix()}")

    if save_db:
        settings = load_settings()
        db_path = str(init_database(settings.db_path))
        npos, nsnap, snap_ts = persist_live_account_snapshot_to_db(db_path, payload, broker="kis")
        print(
            f"DB save: enabled → real_positions rows={npos}, "
            f"real_account_snapshots rows={nsnap}, snapshot_time={snap_ts} (path: {db_path})"
        )
    else:
        print("DB save: skipped (--no-save-db)")
    return 0


def cmd_live_order_guard_check(args: argparse.Namespace) -> int:
    """[실전-7] 중복·대기·reconcile·스냅샷 기반 주문 위험 조회 (HTTP 없음)."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.order_guard import (
        check_duplicate_order_risk,
        load_order_guard_inputs,
    )
    from deepsignal.storage.database import init_database

    symbol = str(getattr(args, "symbol", "") or "").strip()
    if not symbol:
        print("DeepSignal live-order-guard-check: --symbol is required.")
        return 1
    broker = str(getattr(args, "broker", "kis") or "kis").strip().lower()
    if broker != "kis":
        print("DeepSignal live-order-guard-check: only --broker kis is supported.")
        return 1
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    qty = int(getattr(args, "quantity", 1) or 1)
    lp_raw = getattr(args, "limit_price", None)
    limit_price = float(lp_raw) if lp_raw is not None else None
    stale = int(getattr(args, "stale_minutes", 10) or 10)

    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    recent, reconcile, snap_time, partials = load_order_guard_inputs(
        db_path,
        broker=broker,
        symbol=symbol,
        output_dir=out_dir,
    )
    result = check_duplicate_order_risk(
        symbol=symbol,
        side="BUY",
        quantity=qty,
        limit_price=limit_price,
        broker=broker,
        recent_orders=recent,
        reconcile_result=reconcile,
        latest_snapshot_time=snap_time,
        stale_snapshot_minutes=stale,
        open_partial_fills=partials,
    )

    print("DeepSignal order guard check")
    if result.blocked:
        print("BLOCKED:")
        for issue in result.issues:
            if issue.severity == "HIGH":
                print(f"- {issue.message}")
    else:
        print("SAFE:")
        print("No duplicate order risk detected.")
    for w in result.warnings:
        print(w)
    return 1 if result.blocked else 0


def cmd_pre_trade_runbook(args: argparse.Namespace) -> int:
    """[실전-10] 실주문 전 운영 runbook (session·sync·reconcile·guard·plan)."""
    from datetime import datetime

    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.kis_config import KisConfigError, load_kis_config_from_env
    from deepsignal.live_trading.runbook import (
        PreTradeRunbookParams,
        format_runbook_console,
        run_pre_trade_runbook,
        write_runbook_report,
    )
    from deepsignal.live_trading.trading_session import parse_holidays
    from deepsignal.storage.database import init_database

    if not bool(getattr(args, "network", False)):
        print("DeepSignal pre-trade-runbook: --network is required.")
        return 1
    if str(getattr(args, "broker", "kis")).strip().lower() != "kis":
        print("DeepSignal pre-trade-runbook: only --broker kis is supported.")
        return 1
    plan_path = str(getattr(args, "plan", "") or "").strip()
    if not plan_path:
        print("DeepSignal pre-trade-runbook: --plan is required.")
        return 1
    symbol = str(getattr(args, "symbol", "") or "").strip()
    if not symbol:
        print("DeepSignal pre-trade-runbook: --symbol is required.")
        return 1

    try:
        load_kis_config_from_env()
    except KisConfigError as e:
        print(f"DeepSignal pre-trade-runbook: KIS config error: {e}")
        return 1

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    allow_raw = getattr(args, "allow_symbol", None)
    allow_syms = list(allow_raw) if allow_raw else None

    session_policy = None
    now_raw = getattr(args, "now", None)
    session_now: datetime | None = None
    if now_raw:
        session_now = datetime.fromisoformat(str(now_raw).strip())
    if getattr(args, "market", None) or getattr(args, "allow_after_hours", False) or getattr(args, "holiday", None):
        from deepsignal.live_trading.trading_session import load_trading_session_policy_from_env

        session_policy = load_trading_session_policy_from_env()
        if str(getattr(args, "market", "") or "").strip():
            session_policy.market = str(args.market).strip()
        if bool(getattr(args, "allow_after_hours", False)):
            session_policy.allow_after_hours = True
        extra_h: list[str] = []
        for h in getattr(args, "holiday", None) or []:
            extra_h.extend(parse_holidays(str(h)))
        if extra_h:
            base_h = list(session_policy.holidays or [])
            session_policy.holidays = base_h + [x for x in extra_h if x not in base_h]

    lp_raw = getattr(args, "limit_price", None)
    params = PreTradeRunbookParams(
        broker="kis",
        output_dir=out_dir,
        db_path=db_path,
        network=True,
        plan_path=plan_path,
        symbol=symbol,
        quantity=int(getattr(args, "quantity", 1) or 1),
        limit_price=float(lp_raw) if lp_raw is not None else None,
        stale_snapshot_minutes=int(getattr(args, "stale_minutes", 10) or 10),
        allow_symbols=allow_syms,
        max_single_order_value=float(getattr(args, "max_single_order_value", 100_000.0)),
        max_total_order_value=float(getattr(args, "max_total_order_value", 200_000.0)),
        session_now=session_now,
        session_policy=session_policy,
        save_db=bool(getattr(args, "save_db", True)),
    )
    result = run_pre_trade_runbook(params)
    jp, mp = write_runbook_report(result, output_dir=out_dir)
    print(format_runbook_console(result))
    print(f"JSON: {jp.as_posix()}")
    print(f"Markdown: {mp.as_posix()}")
    return 0 if result.final_status == "PRE_TRADE_READY" else 1


def cmd_post_trade_runbook(args: argparse.Namespace) -> int:
    """[실전-10/13] 실주문 후 운영 runbook (status·fill·sync·reconcile·risk-check)."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.kis_config import KisConfigError, load_kis_config_from_env
    from deepsignal.live_trading.runbook import (
        PostTradeRunbookParams,
        format_runbook_console,
        run_post_trade_runbook,
        write_runbook_report,
    )
    from deepsignal.live_trading.risk_guard import risk_policy_from_namespace
    from deepsignal.storage.database import init_database

    if not bool(getattr(args, "network", False)):
        print("DeepSignal post-trade-runbook: --network is required.")
        return 1
    if str(getattr(args, "broker", "kis")).strip().lower() != "kis":
        print("DeepSignal post-trade-runbook: only --broker kis is supported.")
        return 1
    audit = str(getattr(args, "audit", "") or "").strip() or None
    oid = str(getattr(args, "order_id", "") or "").strip() or None
    sym = str(getattr(args, "symbol", "") or "").strip() or None
    if not audit and not oid and not sym:
        print("DeepSignal post-trade-runbook: --audit, --order-id, or --symbol is required.")
        return 1

    try:
        load_kis_config_from_env()
    except KisConfigError as e:
        print(f"DeepSignal post-trade-runbook: KIS config error: {e}")
        return 1

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    params = PostTradeRunbookParams(
        broker="kis",
        output_dir=out_dir,
        db_path=db_path,
        network=True,
        audit_path=audit,
        order_id=oid,
        symbol=sym,
        save_db=bool(getattr(args, "save_db", True)),
        with_summary=bool(getattr(args, "with_summary", False) or getattr(args, "full_report", False)),
        generate_html_dashboard=bool(getattr(args, "with_summary", False) or getattr(args, "full_report", False)),
    )
    result = run_post_trade_runbook(params, risk_policy=risk_policy_from_namespace(args))
    jp, mp = write_runbook_report(result, output_dir=out_dir)
    print(format_runbook_console(result))
    print(f"JSON: {jp.as_posix()}")
    print(f"Markdown: {mp.as_posix()}")
    if result.final_status == "POST_TRADE_OK":
        return 0
    if result.final_status == "POST_TRADE_WARNING":
        return 0
    if result.final_status == "POST_TRADE_RISK_ALERT":
        return 1
    return 1


def cmd_risk_check(args: argparse.Namespace) -> int:
    """[실전-12] 실계좌 포지션 손절/익절 경고 (조회·리포트만, SELL 없음)."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.risk_guard import (
        format_risk_console,
        risk_policy_from_namespace,
        run_portfolio_risk_check,
    )
    from deepsignal.storage.database import init_database

    broker = str(getattr(args, "broker", "kis") or "kis").strip().lower()
    if broker != "kis":
        print("DeepSignal risk-check: only --broker kis is supported.")
        return 1

    if bool(getattr(args, "sync_first", False)):
        print(
            "Warning: --sync-first is not implemented yet; "
            "using latest real_positions from DB. Run live-sync-account --network first."
        )

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    policy = risk_policy_from_namespace(args)
    result, _summary, jp, mp = run_portfolio_risk_check(
        db_path,
        broker=broker,
        output_dir=out_dir,
        policy=policy,
        write_report=True,
    )
    print(format_risk_console(result))
    print(f"JSON: {jp.as_posix() if jp else ''}")
    print(f"Markdown: {mp.as_posix() if mp else ''}")
    print("Note: risk-check does not place SELL or market orders.")
    if result.status == "OK":
        return 0
    return 1


def cmd_ops_dashboard(args: argparse.Namespace) -> int:
    """[실전-14] 로컬 DB/outputs 기반 운영 상태 요약 리포트 (조회 전용)."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.ops_dashboard import run_ops_dashboard
    from deepsignal.storage.database import init_database

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    recent_orders = int(getattr(args, "recent_orders", 10) or 10)
    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    result, jp, mp = run_ops_dashboard(
        db_path,
        output_dir=out_dir,
        broker="kis",
        recent_orders=recent_orders,
    )
    print("DeepSignal ops dashboard")
    print(f"Status: {result.status}")
    print(f"Positions: {len(result.positions)}")
    print(f"Risk: {result.risk.get('status') or result.risk.get('risk_status') or '(none)'}")
    print(f"Reconcile: success={result.reconcile.get('success') if result.reconcile else None}")
    print(f"JSON: {jp.as_posix()}")
    print(f"Markdown: {mp.as_posix()}")
    print("Note: ops-dashboard is read-only and does not place SELL or market orders.")
    return 0


def cmd_sell_plan(args: argparse.Namespace) -> int:
    """[실전-15] 운영자 검토용 수동 SELL 계획서 생성 (주문 실행 없음)."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.risk_guard import RiskGuardPolicy
    from deepsignal.live_trading.sell_plan import run_sell_plan
    from deepsignal.storage.database import init_database

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    policy = RiskGuardPolicy(
        stop_loss_pct=float(getattr(args, "stop_loss_pct", -0.07)),
        take_profit_pct=float(getattr(args, "take_profit_pct", 0.15)),
        warn_loss_pct=float(getattr(args, "warn_loss_pct", -0.03)),
    )
    result, jp, mp = run_sell_plan(
        db_path,
        output_dir=out_dir,
        broker="kis",
        policy=policy,
    )
    print("DeepSignal sell plan")
    print(f"Status: {result.status}")
    print(f"Items: {len(result.items)}")
    print(f"JSON: {jp.as_posix()}")
    print(f"Markdown: {mp.as_posix()}")
    print("Note: sell-plan does NOT place SELL orders; manual operator review required.")
    return 0


def cmd_notify_alerts(args: argparse.Namespace) -> int:
    """[실전-16] 위험 상태 alert-only 알림. --send 없으면 네트워크 호출 없음."""
    from deepsignal.live_trading.notification_center import notify_alerts

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    channel = str(getattr(args, "channel", "") or "").strip() or None
    dry_run = not bool(getattr(args, "send", False))
    include_ok = bool(getattr(args, "include_ok", False))
    include_maintenance = bool(getattr(args, "include_maintenance", False))
    messages, results, audit = notify_alerts(
        output_dir=out_dir,
        channel=channel,
        dry_run=dry_run,
        include_ok=include_ok,
        include_maintenance=include_maintenance,
    )
    selected = results[0].channel if results else (channel or "telegram")
    print("DeepSignal notify alerts")
    print(f"Channel: {selected}")
    print(f"Dry-run: {dry_run}")
    print(f"Include maintenance: {include_maintenance}")
    print(f"Messages: {len(messages)}")
    for result in results:
        print(f"Result: channel={result.channel} success={result.success} status={result.status}")
        if result.message:
            print(f"Message: {result.message}")
    print(f"Audit: {audit.as_posix()}")
    print("Note: notify-alerts is alert-only. No orders were placed.")
    if dry_run:
        return 0
    return 0 if all(r.success for r in results) else 1


def cmd_daily_ops_summary(args: argparse.Namespace) -> int:
    """[실전-17] 하루 운영 상태 통합 요약 리포트 (조회/요약 전용)."""
    from deepsignal.live_trading.daily_ops_summary import run_daily_ops_summary

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    target_date = str(getattr(args, "date", "") or "").strip() or None
    include_latest_fallback = bool(getattr(args, "include_latest_fallback", True))
    notify_dry_run = bool(getattr(args, "notify_dry_run", False))
    summary, jp, mp = run_daily_ops_summary(
        output_dir=out_dir,
        target_date=target_date,
        include_latest_fallback=include_latest_fallback,
        notify_dry_run=notify_dry_run,
    )
    print("DeepSignal daily ops summary")
    print(f"Status: {summary.status}")
    print(f"Date: {summary.date}")
    print(f"Next actions: {len(summary.next_actions)}")
    print(f"Warnings: {len(summary.warnings)}")
    print(f"JSON: {jp.as_posix()}")
    print(f"Markdown: {mp.as_posix()}")
    print("Note: daily-ops-summary is read-only and does not place orders.")
    return 0


def cmd_html_dashboard(args: argparse.Namespace) -> int:
    """[실전-18] 로컬 outputs 기반 정적 HTML 대시보드 생성."""
    from pathlib import Path
    import webbrowser

    from deepsignal.live_trading.html_dashboard import write_html_dashboard

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    result = write_html_dashboard(output_dir=out_dir)
    print("DeepSignal HTML dashboard created")
    print(f"Status: {result.status}")
    print(f"HTML: {result.html_path}")
    print("Note: html-dashboard is static local HTML; no web server, network call, or orders.")
    if bool(getattr(args, "open", False)):
        webbrowser.open(Path(result.html_path).resolve().as_uri())
    return 0


def cmd_cleanup_reports(args: argparse.Namespace) -> int:
    """[실전-20] outputs 리포트 보존/정리. 기본 dry-run."""
    import json
    from pathlib import Path

    from deepsignal.live_trading.report_cleanup import cleanup_reports

    dry_run = not bool(getattr(args, "apply", False))
    result = cleanup_reports(
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
        keep_days=int(getattr(args, "keep_days", 14)),
        keep_latest=int(getattr(args, "keep_latest", 20)),
        archive=bool(getattr(args, "archive", False)),
        archive_dir=str(getattr(args, "archive_dir", "") or "") or None,
        remove_appledouble=bool(getattr(args, "remove_appledouble", False)),
        dry_run=dry_run,
    )
    candidates = 0
    if result.audit_path:
        try:
            audit = json.loads(Path(result.audit_path).read_text(encoding="utf-8"))
            candidates = len(audit.get("candidates") or [])
        except (OSError, json.JSONDecodeError):
            candidates = 0
    print("DeepSignal report cleanup")
    print(f"Dry run: {str(result.dry_run).lower()}")
    print(f"Candidates: {candidates}")
    print(f"Kept: {len(result.kept)}")
    print(f"Archived: {len(result.archived)}")
    print(f"Deleted: {len(result.deleted)}")
    print(f"Audit: {result.audit_path or ''}")
    print("Note: cleanup-reports only touches output_dir reports; no network calls or orders.")
    return 0


def cmd_report_index(args: argparse.Namespace) -> int:
    """[실전-21] outputs/archive 정적 리포트 인덱스 생성."""
    from deepsignal.live_trading.report_index import run_report_index

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    archive_dir = str(getattr(args, "archive_dir", "") or "").strip() or None
    max_items = int(getattr(args, "max_items", 200))
    result, html_path, md_path, json_path = run_report_index(
        output_dir=out_dir,
        archive_dir=archive_dir,
        max_items=max_items,
    )
    print("DeepSignal report index created")
    print(f"Reports: {len(result.items)}")
    print(f"HTML: {html_path.as_posix()}")
    print(f"Markdown: {md_path.as_posix()}")
    print(f"JSON: {json_path.as_posix()}")
    print("Note: report-index is static local output; no web server, network calls, or orders.")
    return 0


def cmd_ops_dry_run(args: argparse.Namespace) -> int:
    """[실전-22] 실주문 없는 하루 운영 점검 dry-run."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.ops_dry_run import (
        format_ops_dry_run_console,
        run_ops_dry_run,
        write_ops_dry_run_report,
    )
    from deepsignal.storage.database import init_database

    broker = str(getattr(args, "broker", "kis") or "kis").strip().lower()
    if broker != "kis":
        print("DeepSignal ops-dry-run: only --broker kis is supported.")
        return 1
    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    archive_dir = str(getattr(args, "archive_dir", "") or "").strip() or None
    result = run_ops_dry_run(
        db_path=db_path,
        output_dir=out_dir,
        archive_dir=archive_dir,
        broker=broker,
        network=bool(getattr(args, "network", False)),
        recent_orders=int(getattr(args, "recent_orders", 10) or 10),
    )
    jp, mp = write_ops_dry_run_report(result, output_dir=out_dir)
    print(format_ops_dry_run_console(result))
    print(f"JSON: {jp.as_posix()}")
    print(f"Markdown: {mp.as_posix()}")
    print("Note: ops-dry-run is a read-only operations check and never places orders.")
    return 0 if result.final_status in {"OPS_DRY_RUN_OK", "OPS_DRY_RUN_WARNING", "OPS_DRY_RUN_NO_DATA"} else 1


def cmd_open_dashboard(args: argparse.Namespace) -> int:
    """[실전-23] 로컬 운영 리포트 경로 안내 및 선택 열기."""
    from deepsignal.live_trading.local_viewer import build_local_viewer_result, format_local_viewer_console

    open_names: list[str] = []
    if bool(getattr(args, "open", False)):
        open_names.append("ops_dashboard")
    if bool(getattr(args, "open_index", False)):
        open_names.append("report_index")
    if bool(getattr(args, "open_archive", False)):
        open_names.append("archive_viewer")
    result = build_local_viewer_result(
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
        open_names=open_names,
        open_all=bool(getattr(args, "open_all", False)),
    )
    print(format_local_viewer_console(result))
    return 0


def cmd_report_health_check(args: argparse.Namespace) -> int:
    """[실전-25] 운영 리포트/DB health 진단. 수정·삭제·네트워크 없음."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.report_health import (
        format_report_health_console,
        run_report_health_check,
        write_report_health,
    )

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    db_path = str(getattr(args, "db_path", "") or "").strip() or load_settings().db_path
    result = run_report_health_check(
        output_dir=out_dir,
        db_path=db_path,
        max_age_hours=float(getattr(args, "max_age_hours", 24.0)),
        max_output_files=int(getattr(args, "max_output_files", 500)),
    )
    jp, mp = write_report_health(result, output_dir=out_dir)
    print(format_report_health_console(result, jp, mp))
    return 0 if result.status in {"HEALTH_OK", "HEALTH_WARNING", "HEALTH_NO_DATA"} else 1


def cmd_tune_threshold_from_outcomes(args: argparse.Namespace) -> int:
    """[학습루프-02] recommendation_outcomes 기반 threshold 재계산."""
    from deepsignal.live_trading.ai_recommendation.outcome_threshold_tuning import (
        format_outcome_threshold_console,
        run_tune_threshold_from_outcomes,
    )

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    outcomes_db = str(getattr(args, "outcomes_db", "outputs/recommendation_outcomes.db") or "outputs/recommendation_outcomes.db")
    result, jp, mp, sp = run_tune_threshold_from_outcomes(
        outcomes_db=outcomes_db,
        output_dir=out_dir,
        lookback_days=int(getattr(args, "lookback_days", 60)),
        min_samples=int(getattr(args, "min_samples", 10)),
        target_win_rate=float(getattr(args, "target_win_rate", 0.45)),
        min_avg_return=float(getattr(args, "min_avg_return", 0.0)),
        blend_with_validation=float(getattr(args, "blend_with_validation", 0.5)),
    )
    print(format_outcome_threshold_console(result, jp, mp, sp))
    return 0


def cmd_weekly_maintenance(args: argparse.Namespace) -> int:
    """[실전-26] 주간 운영 점검 dry-run. 삭제·이동·네트워크 없음."""
    from pathlib import Path

    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.weekly_maintenance import (
        format_weekly_maintenance_console,
        run_weekly_maintenance,
        write_weekly_maintenance_report,
    )

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    db_path = str(getattr(args, "db_path", "") or "").strip() or load_settings().db_path
    outcomes_db = getattr(args, "outcomes_db", None)
    if outcomes_db is None and bool(getattr(args, "tune_threshold_from_outcomes", False)):
        outcomes_db = str(Path(out_dir) / "recommendation_outcomes.db")
    result = run_weekly_maintenance(
        output_dir=out_dir,
        archive_dir=getattr(args, "archive_dir", "outputs/archive"),
        db_path=db_path,
        keep_days=int(getattr(args, "keep_days", 14)),
        keep_latest=int(getattr(args, "keep_latest", 20)),
        max_age_hours=float(getattr(args, "max_age_hours", 24.0)),
        max_output_files=int(getattr(args, "max_output_files", 500)),
        tune_threshold_from_outcomes=bool(getattr(args, "tune_threshold_from_outcomes", False)),
        outcomes_db=outcomes_db,
        tune_lookback_days=int(getattr(args, "tune_lookback_days", 60)),
        tune_min_samples=int(getattr(args, "tune_min_samples", 10)),
        tune_blend_with_validation=float(getattr(args, "tune_blend_with_validation", 0.5)),
    )
    jp, mp = write_weekly_maintenance_report(result, output_dir=out_dir)
    print(format_weekly_maintenance_console(result, jp, mp))
    return 0 if result.final_status in {"WEEKLY_MAINTENANCE_OK", "WEEKLY_MAINTENANCE_WARNING"} else 1


def cmd_weekly_report_bundle(args: argparse.Namespace) -> int:
    """[실전-28] 주간 리포트 번들 생성. 복사/인덱스 전용."""
    from pathlib import Path

    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.weekly_report_bundle import (
        create_weekly_report_bundle,
        format_weekly_report_bundle_console,
        open_weekly_bundle,
    )

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    db_path = str(getattr(args, "db_path", "") or "").strip() or load_settings().db_path
    bundle_dir = str(getattr(args, "bundle_dir", "") or (Path(out_dir) / "weekly_bundles"))
    result = create_weekly_report_bundle(
        output_dir=out_dir,
        bundle_dir=bundle_dir,
        create_zip=bool(getattr(args, "zip", False)),
        db_path=db_path,
    )
    print(format_weekly_report_bundle_console(result))
    if bool(getattr(args, "open", False)):
        opened = open_weekly_bundle(result)
        print(f"Opened: {opened}")
    return 0 if result.status in {"WEEKLY_BUNDLE_OK", "WEEKLY_BUNDLE_WARNING"} else 1


def cmd_generate_checklists(args: argparse.Namespace) -> int:
    """[실전-29] 수동 운영 체크리스트 생성. 스케줄러/자동 실행 없음."""
    from deepsignal.live_trading.checklist_generator import format_checklists_console, generate_checklists

    documents = generate_checklists(output_dir=getattr(args, "output_dir", "outputs/checklists"))
    print(format_checklists_console(documents))
    return 0


def cmd_safety_audit(args: argparse.Namespace) -> int:
    """[실전-30] 로컬 읽기 전용 안전 감사. 주문/네트워크/cleanup 없음."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.safety_audit import (
        SAFETY_AUDIT_BLOCKED,
        format_safety_audit_console,
        run_safety_audit,
        write_safety_audit,
    )

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    db_path = str(getattr(args, "db_path", "") or "").strip() or load_settings().db_path
    result = run_safety_audit(
        output_dir=out_dir,
        db_path=db_path,
        strict=bool(getattr(args, "strict", False)),
        freshness_date=getattr(args, "freshness_date", None),
    )
    jp, mp = write_safety_audit(result, output_dir=out_dir)
    print(format_safety_audit_console(result, jp, mp))
    return 1 if result.status == SAFETY_AUDIT_BLOCKED else 0


def cmd_init_context(args: argparse.Namespace) -> int:
    """표준 AI_CONTEXT 파일을 생성한다. 기존 파일 overwrite 없음."""
    from project_context.context_initializer import init_all_projects, init_context

    if bool(getattr(args, "all_projects", False)):
        results = init_all_projects(getattr(args, "project", ".") or ".")
    else:
        results = [init_context(getattr(args, "project", ".") or ".")]

    print("DeepSignal AI_CONTEXT initialization")
    if not results:
        print("No project candidates found.")
        return 0
    for result in results:
        wr = result.write_result
        print(f"Project: {result.project_root}")
        print(f"Context dir: {wr.context_dir}")
        print(f"Created: {len(wr.created_files)}")
        for path in wr.created_files:
            print(f"- created {path}")
        print(f"Skipped existing: {len(wr.skipped_files)}")
        for path in wr.skipped_files:
            print(f"- skipped {path}")
    print("Note: init-context creates missing Markdown only; existing files are never overwritten.")
    return 0


def cmd_archive_viewer(args: argparse.Namespace) -> int:
    """[실전-32] 로컬 archive viewer 생성. 읽기 전용."""
    from deepsignal.live_trading.archive_viewer import format_archive_viewer_console, run_archive_viewer

    result, html_path, json_path = run_archive_viewer(
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
        archive_dir=str(getattr(args, "archive_dir", "outputs/archive") or "outputs/archive"),
        limit=int(getattr(args, "limit", 200) or 200),
        create_csv=not bool(getattr(args, "no_csv", False)),
        create_summary_md=not bool(getattr(args, "no_summary_md", False)),
        trend_days=int(getattr(args, "trend_days", 7) or 7),
    )
    print(
        format_archive_viewer_console(
            result,
            html_path,
            json_path,
            generated_csv=not bool(getattr(args, "no_csv", False)),
            generated_summary_md=not bool(getattr(args, "no_summary_md", False)),
        )
    )
    return 0


def cmd_ai_live_recommend(args: argparse.Namespace) -> int:
    """AI 실계좌 추천 리포트/주문안 생성. 실주문·live-approve 호출 없음."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.ai_recommendation.recommendation_engine import (
        format_ai_recommendation_console,
        run_ai_live_recommendation,
    )
    from deepsignal.live_trading.ai_recommendation.recommendation_model import RecommendationConfig
    from deepsignal.storage.database import init_database

    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    symbols_raw = str(getattr(args, "symbols", "") or "").strip()
    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()] if symbols_raw else None
    cfg = RecommendationConfig(
        broker=str(getattr(args, "broker", "kis") or "kis").strip().lower(),
        symbols=symbols,
        max_recommendations=int(getattr(args, "max_recommendations", 10) or 10),
        capital_limit=getattr(args, "capital_limit", None) or getattr(args, "max_order_value", None),
        allow_sell_candidates=bool(getattr(args, "allow_sell_candidates", False)),
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
    )
    try:
        result, rec_json, plan_json, md_path = run_ai_live_recommendation(
            db_path,
            config=cfg,
            network=bool(getattr(args, "network", False)),
        )
    except Exception as exc:
        print(f"DeepSignal AI live recommendation failed: {exc}")
        print("No live-approve call, no --execute call, and no KIS order-cash POST were made.")
        return 1
    print(format_ai_recommendation_console(result, rec_json, plan_json, md_path))
    return 0


def cmd_generate_test_order_plan(args: argparse.Namespace) -> int:
    """소액 BUY/LIMIT 테스트 주문안 JSON 생성 (실행 없음)."""
    from deepsignal.live_trading.test_order_plan import (
        SmallLiveOrderPlanInput,
        build_test_order_plan_payload,
        format_test_order_plan_console,
        write_test_order_plan,
    )

    try:
        config = SmallLiveOrderPlanInput(
            symbol=str(args.symbol),
            quantity=int(getattr(args, "quantity", 1) or 1),
            limit_price=float(args.limit_price),
            max_order_value=float(getattr(args, "max_order_value", 100_000.0) or 100_000.0),
            output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
            currency=str(getattr(args, "currency", "KRW") or "KRW"),
        )
        path = write_test_order_plan(config)
        payload = build_test_order_plan_payload(config)
    except ValueError as exc:
        print(f"Test order plan blocked: {exc}")
        return 1
    print(format_test_order_plan_console(path, payload))
    return 0


def cmd_telegram_test(args: argparse.Namespace) -> int:
    """Telegram Bot 연결 테스트."""
    from deepsignal.live_trading.telegram_test import format_telegram_test_console, run_telegram_test

    body, json_path = run_telegram_test(
        message=str(getattr(args, "message", "") or "DeepSignal 연결 테스트"),
        send=bool(getattr(args, "send", False)),
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
        timeout_seconds=float(getattr(args, "timeout_seconds", 5.0) or 5.0),
    )
    print(format_telegram_test_console(body, json_path))
    if body.get("env_errors") and bool(getattr(args, "send", False)):
        return 1
    if body.get("status") == "failed":
        return 1
    return 0


def cmd_telegram_approval_request(args: argparse.Namespace) -> int:
    """Telegram 승인 요청 생성/전송."""
    from pathlib import Path

    from deepsignal.live_trading.telegram_approval import create_telegram_approval_request, load_telegram_config_from_env

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    cfg = load_telegram_config_from_env(
        output_dir=out_dir,
        expires_minutes=int(getattr(args, "expires_minutes", 10) or 10),
        max_total_order_value=float(getattr(args, "max_total_order_value", 100_000.0) or 100_000.0),
        max_single_order_value=float(getattr(args, "max_single_order_value", 50_000.0) or 50_000.0),
        max_orders=int(getattr(args, "max_orders", 1) or 1),
        send=bool(getattr(args, "send", False)),
        timeout_seconds=float(getattr(args, "timeout_seconds", 5.0) or 5.0),
        allowed_chat_id=getattr(args, "allowed_chat_id", None),
    )
    if bool(getattr(args, "send", False)):
        if not cfg.bot_token or not cfg.allowed_chat_id:
            print("Telegram env missing: set DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN and DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID")
            return 1
    request, json_path, md_path = create_telegram_approval_request(str(args.plan), cfg)
    print(f"JSON: {json_path.as_posix()}")
    print(f"State: {(Path(out_dir) / 'TELEGRAM_APPROVAL_STATE.json').as_posix()}")
    print(f"Markdown: {md_path.as_posix()}")
    if request.status != "PENDING":
        print(f"Telegram 승인 요청 차단: {request.status}")
        if request.order_count <= 0:
            print("Plan Orders가 0건입니다. daily-ai-trade-plan --debug-plan 으로 확인하세요.")
        return 1
    if bool(getattr(args, "send", False)) and request.telegram_result.get("ok"):
        print("Telegram 승인 요청 전송 완료")
        if bool(getattr(args, "no_auto_execute", False)):
            print(f"다음: Telegram [승인] 후 python main.py execute-last-approved --output-dir {out_dir}")
            return 0
        from deepsignal.config.settings import load_settings
        from deepsignal.live_trading.telegram_auto_execute import poll_telegram_approval_until_done
        from deepsignal.storage.database import init_database

        settings = load_settings()
        db_path = str(init_database(settings.db_path))
        outcome = poll_telegram_approval_until_done(
            out_dir,
            db_path=db_path,
            wait_seconds=float(getattr(args, "wait_seconds", 600.0) or 600.0),
            poll_interval=float(getattr(args, "poll_interval", 2.0) or 2.0),
            allowed_chat_id=getattr(args, "allowed_chat_id", None),
            timeout_seconds=float(getattr(args, "timeout_seconds", 5.0) or 5.0),
            auto_execute=True,
        )
        print(outcome.message)
        if outcome.audit_path:
            print(f"Audit: {outcome.audit_path}")
        if outcome.execution is not None:
            print(f"Execution status: {outcome.execution.status}")
            if outcome.execution.live_approval_audit_path:
                print(f"Live approval audit: {outcome.execution.live_approval_audit_path}")
        if outcome.outcome in {"executed", "already_executed"}:
            return 0
        if outcome.outcome == "approved_manual":
            return 0
        if outcome.outcome in {"execution_failed", "rejected", "expired", "blocked", "pending_timeout"}:
            return 1
        return 1
    elif bool(getattr(args, "send", False)):
        print("Telegram 승인 요청 전송 실패")
        print(f"Telegram result: {request.telegram_result}")
        return 1
    else:
        print("Telegram 승인 요청 dry-run 완료 (--send 로 실제 전송)")
    return 0


def cmd_telegram_approval_status(args: argparse.Namespace) -> int:
    """최신 Telegram 승인 요청 상태 출력."""
    from deepsignal.live_trading.telegram_approval import load_latest_request, render_status

    state = load_latest_request(str(getattr(args, "output_dir", "outputs") or "outputs"))
    print(render_status(state))
    return 0 if state else 1


def cmd_telegram_approval_listen(args: argparse.Namespace) -> int:
    """Telegram 승인 callback 확인 후 (기본) 즉시 실주문 실행."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.telegram_approval import (
        find_action_from_updates,
        load_latest_request,
        load_telegram_config_from_env,
        telegram_get_updates,
    )
    from deepsignal.live_trading.telegram_auto_execute import (
        _process_callback,
        poll_telegram_approval_until_done,
    )
    from deepsignal.storage.database import init_database

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    auto_execute = not bool(getattr(args, "no_auto_execute", False))
    if bool(getattr(args, "watch", False)):
        settings = load_settings()
        db_path = str(init_database(settings.db_path))
        outcome = poll_telegram_approval_until_done(
            out_dir,
            db_path=db_path,
            wait_seconds=float(getattr(args, "wait_seconds", 600.0) or 600.0),
            poll_interval=float(getattr(args, "poll_interval", 2.0) or 2.0),
            allowed_chat_id=getattr(args, "allowed_chat_id", None),
            timeout_seconds=float(getattr(args, "timeout_seconds", 5.0) or 5.0),
            auto_execute=auto_execute,
        )
        print(outcome.message)
        if outcome.outcome == "executed":
            return 0
        if outcome.outcome in {"rejected", "expired", "blocked", "execution_failed"}:
            return 1
        return 0 if outcome.outcome in {"approved_manual", "already_executed"} else 1

    state = load_latest_request(out_dir)
    if not state:
        print("Telegram 승인 요청이 없습니다. 먼저 telegram-approval-request를 실행하세요.")
        return 1
    cfg = load_telegram_config_from_env(
        output_dir=out_dir,
        timeout_seconds=float(getattr(args, "timeout_seconds", 5.0) or 5.0),
        allowed_chat_id=getattr(args, "allowed_chat_id", None),
    )
    updates_payload = telegram_get_updates(
        bot_token=cfg.bot_token,
        timeout_seconds=cfg.timeout_seconds,
        offset=getattr(args, "offset", None),
    )
    updates = list(updates_payload.get("result") or [])
    action, token, chat_id, callback_id = find_action_from_updates(updates, state)
    if not action:
        print("일치하는 Telegram 승인/거부 callback이 없습니다. Telegram에서 버튼을 누른 뒤 다시 실행하세요.")
        return 1

    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    outcome = _process_callback(
        state=state,
        action=action,
        token=token,
        chat_id=chat_id,
        callback_id=callback_id,
        output_dir=out_dir,
        config=cfg,
        db_path=db_path,
        auto_execute=auto_execute,
    )
    print(outcome.message)
    if outcome.audit_path:
        print(f"Audit: {outcome.audit_path}")
    if outcome.execution is not None:
        print(f"Execution status: {outcome.execution.status}")
    if outcome.outcome in {"executed", "approved_manual", "rejected"}:
        return 0 if outcome.outcome != "execution_failed" else 1
    if outcome.outcome == "execution_failed":
        return 1
    if outcome.audit and outcome.audit.get("errors"):
        for error in outcome.audit.get("errors") or []:
            print(f"- {error}")
        return 1
    return 0


def cmd_execute_last_approved(args: argparse.Namespace) -> int:
    """Telegram 승인 callback 확인 후 기존 live execution path로 실행한다."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.approved_execution import execute_last_approved
    from deepsignal.storage.database import init_database

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    wait_seconds = float(getattr(args, "wait_seconds", 60.0) or 60.0)
    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    print("Telegram 승인 callback 확인 중...")
    if wait_seconds > 0:
        print(f"승인 대기: 최대 {int(wait_seconds)}초")
    try:
        result = execute_last_approved(
            output_dir=out_dir,
            db_path=db_path,
            send=bool(getattr(args, "send", False)),
            timeout_seconds=float(getattr(args, "timeout_seconds", 5.0) or 5.0),
            wait_seconds=wait_seconds,
            poll_interval=float(getattr(args, "poll_interval", 2.0) or 2.0),
        )
    except Exception as exc:
        print(f"DeepSignal execute-last-approved blocked: {exc}")
        return 1

    if result.success:
        print("Telegram 승인 확인 완료")
        print("주문 실행 시작")
    elif result.errors and any("중단" in e for e in result.errors):
        print("Telegram 중단 처리됨")
    elif result.errors and any("만료" in e for e in result.errors):
        print("Telegram 승인 만료됨")
    elif result.errors and any("대기" in e for e in result.errors):
        print("Telegram 승인 대기 중입니다")
    else:
        print("DeepSignal execute-last-approved blocked")

    print(f"Request ID: {result.request_id}")
    print(f"Status: {result.status}")
    if result.errors:
        for error in result.errors:
            print(error)
    print(f"Audit JSON: {result.audit_json_path}")
    print(f"Audit Markdown: {result.audit_markdown_path}")
    if result.live_approval_audit_path:
        print(f"Live Approval Audit: {result.live_approval_audit_path}")
    return 0 if result.success else 1


def cmd_execute_approved(args: argparse.Namespace) -> int:
    """지정 request-id의 Telegram 승인 audit을 기존 live execution path로 실행한다."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.approved_execution import execute_approved_by_request_id
    from deepsignal.storage.database import init_database

    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    try:
        result = execute_approved_by_request_id(
            output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
            request_id=str(args.request_id),
            db_path=db_path,
            send=bool(getattr(args, "send", False)),
            timeout_seconds=float(getattr(args, "timeout_seconds", 5.0) or 5.0),
        )
    except Exception as exc:
        print(f"DeepSignal execute-approved blocked: {exc}")
        return 1
    print("DeepSignal execute-approved finished")
    print(f"Request ID: {result.request_id}")
    print(f"Status: {result.status}")
    if result.errors:
        for error in result.errors:
            print(error)
    print(f"Audit JSON: {result.audit_json_path}")
    print(f"Audit Markdown: {result.audit_markdown_path}")
    if result.live_approval_audit_path:
        print(f"Live Approval Audit: {result.live_approval_audit_path}")
    return 0 if result.success else 1


def cmd_daily_ai_trade_plan(args: argparse.Namespace) -> int:
    """[실전-46] 일일 AI 추천/주문안 생성. 실주문 없음."""
    _apply_aggression_dial()  # 분석에도 투자공격성 다이얼 반영(문턱·게이트)
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.daily_ai_trading_workflow import run_daily_ai_trade_plan
    from deepsignal.storage.database import init_database

    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    try:
        max_order_value = getattr(args, "max_order_value", None)
        result = run_daily_ai_trade_plan(
            db_path,
            broker=str(getattr(args, "broker", "kis") or "kis"),
            network=bool(getattr(args, "network", False)),
            output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
            max_order_value=float(max_order_value) if max_order_value is not None else None,
            allow_test_plan_order=bool(getattr(args, "allow_test_plan_order", False)),
            ignore_safety_block_for_test=bool(getattr(args, "ignore_safety_block_for_test", False)),
            debug_plan=bool(getattr(args, "debug_plan", False)),
        )
    except Exception as exc:
        print(f"DeepSignal daily-ai-trade-plan failed: {exc}")
        print("No live-approve call, no execute-last-approved call, and no KIS order-cash POST were made.")
        return 1
    print("DeepSignal AI daily trade plan created")
    print(f"Status: {result.status}")
    print(f"Recommendations: {result.recommendation_count}")
    print(f"Plan Orders: {result.order_count}")
    print(f"Latest Plan: {result.latest_order_plan_json}")
    print(f"Markdown: {result.markdown_path}")
    if bool(getattr(args, "allow_test_plan_order", False)) and bool(getattr(args, "ignore_safety_block_for_test", False)):
        print("Test-plan mode: safety BLOCKED는 주문안 생성만 완화됩니다. 실주문은 장중 trading session guard를 통과해야 합니다.")
    if result.diagnostic_console:
        print(result.diagnostic_console)
    if result.order_count == 0:
        # 0건 원인 구분: soft(HOLD/REDUCE) vs fatal(stale_snapshot, safety_audit 등)
        _FATAL_MARKERS = (
            "safety_audit=", "reconcile=", "partial_fill",
            "stale_account_snapshot", "duplicate_order_risk:", "missing_limit_price",
        )
        diag = result.plan_diagnostics or {}
        recs = diag.get("recommendations") or []

        def _has_fatal(reasons: list) -> bool:
            return any(any(m in r for m in _FATAL_MARKERS) for r in (reasons or []))

        global_fatal = _has_fatal(diag.get("global_operational_blocked_reasons") or [])
        rec_fatal = any(_has_fatal(r.get("blocked_reasons") or []) for r in recs)
        if recs and not global_fatal and not rec_fatal:
            # AI가 HOLD/REDUCE/SELL 판단 → 정상 종료, 오늘 매수 없음
            actions = sorted({r.get("action", "?") for r in recs})
            print(f"오늘 매수 신호 없음 — AI 추천: {', '.join(actions)} (매매 조건 미충족)")
            return 0
        print("Plan Orders가 0건입니다. daily-ai-trade-plan --debug-plan 으로 차단 사유를 확인하세요.")
        return 1
    return 0


def cmd_daily_ai_trade_report(args: argparse.Namespace) -> int:
    """[실전-46] 일일 AI 투자 운영 리포트 생성."""
    from deepsignal.live_trading.daily_ai_trading_workflow import build_daily_ai_trade_report

    result = build_daily_ai_trade_report(
        broker=str(getattr(args, "broker", "kis") or "kis"),
        network=bool(getattr(args, "network", False)),
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
    )
    print("DeepSignal AI daily trade report created")
    print(f"Status: {result.status}")
    print(f"Markdown: {result.markdown_path}")
    print(f"JSON: {result.json_path}")
    return 0


def cmd_daily_ai_auto_runner(args: argparse.Namespace) -> int:
    """일일 AI 자동 운영 루프 (plan → Telegram 승인 → 실행 → report)."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.daily_ai_auto_runner import DailyAIAutoRunnerConfig, run_daily_ai_auto_runner_loop
    from deepsignal.storage.database import init_database

    _apply_aggression_dial()  # 투자공격성 다이얼 → env 게이트 반영
    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    from deepsignal.live_trading.kis_stock_recommendation_config import load_daily_ai_runner_limits_from_env

    cfg = DailyAIAutoRunnerConfig(
        broker=str(getattr(args, "broker", "kis") or "kis"),
        network=bool(getattr(args, "network", False)),
        output_dir=out_dir,
        plan_time=str(getattr(args, "plan_time", "09:05") or "09:05"),
        report_time=str(getattr(args, "report_time", "15:40") or "15:40"),
        max_order_value=float(getattr(args, "max_order_value", 300_000.0) or 300_000.0),
        max_single_order_value=float(getattr(args, "max_single_order_value", 300_000.0) or 300_000.0),
        max_total_order_value=float(getattr(args, "max_total_order_value", 300_000.0) or 300_000.0),
        max_orders=int(getattr(args, "max_orders", 1) or 1),
        expires_minutes=int(getattr(args, "expires_minutes", 420) or 420),
        poll_interval=float(getattr(args, "poll_interval", 3.0) or 3.0),
        loop_sleep_seconds=float(getattr(args, "loop_sleep_seconds", 15.0) or 15.0),
        timeout_seconds=float(getattr(args, "timeout_seconds", 10.0) or 10.0),
        allow_test_plan_order=bool(getattr(args, "allow_test_plan_order", False)),
        ignore_safety_block_for_test=bool(getattr(args, "ignore_safety_block_for_test", False)),
    )
    for key, value in load_daily_ai_runner_limits_from_env().items():
        setattr(cfg, key, value)
    from deepsignal.live_trading.telegram_approval import load_telegram_config_from_env

    tg = load_telegram_config_from_env(output_dir=out_dir)
    if not tg.bot_token or not tg.allowed_chat_id:
        print("Telegram env missing: DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN / DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID")
        return 1
    print("DeepSignal daily-ai-auto-runner started")
    print(f"Plan: {cfg.plan_time}  Report: {cfg.report_time}  Output: {out_dir}")
    try:
        run_daily_ai_auto_runner_loop(cfg, db_path=db_path, max_iterations=getattr(args, "max_iterations", None))
    except KeyboardInterrupt:
        print("daily-ai-auto-runner stopped")
        return 0
    return 0


def cmd_install_launchd(args: argparse.Namespace) -> int:
    """macOS LaunchAgent 등록 — daily-ai-auto-runner 로그인 시 자동 시작."""
    import platform

    from deepsignal.live_trading.launchd_installer import (
        format_install_console,
        install_launchd,
        launchd_config_from_namespace,
        project_root,
        resolve_python_executable,
    )

    if platform.system() != "Darwin":
        print("install-launchd is macOS only.")
        return 1
    root = project_root(getattr(args, "project_dir", None))
    cfg = launchd_config_from_namespace(args)
    try:
        result = install_launchd(
            cfg,
            project_dir=root,
            load_now=not bool(getattr(args, "no_load", False)),
            sanitize_path=not bool(getattr(args, "no_sanitize_path", False)),
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"install-launchd failed: {exc}")
        return 1
    print(format_install_console(result))
    if not bool(getattr(args, "no_load", False)) and not result.get("loaded"):
        return 1
    return 0


def cmd_uninstall_launchd(args: argparse.Namespace) -> int:
    """macOS LaunchAgent 제거."""
    import platform

    from deepsignal.live_trading.launchd_installer import format_status_console, uninstall_launchd

    if platform.system() != "Darwin":
        print("uninstall-launchd is macOS only.")
        return 1
    result = uninstall_launchd(
        unload=not bool(getattr(args, "keep_loaded", False)),
        remove_plist=not bool(getattr(args, "keep_plist", False)),
    )
    print(
        "\n".join(
            [
                "DeepSignal launchd uninstall finished",
                f"Unload: {result.get('unload_message')}",
                f"Plist removed: {result.get('plist_removed')}",
            ]
        )
    )
    return 0


def cmd_launchd_status(args: argparse.Namespace) -> int:
    """macOS LaunchAgent 설치·실행 상태 확인."""
    import platform

    from deepsignal.live_trading.launchd_installer import format_status_console, launchd_status

    if platform.system() != "Darwin":
        print("launchd-status is macOS only.")
        return 1
    print(format_status_console(launchd_status(project_dir=getattr(args, "project_dir", None))))
    return 0


def cmd_launchd_health_check(args: argparse.Namespace) -> int:
    """필수 LaunchAgent running 점검; 실패 시 kickstart·Telegram."""
    import platform

    from deepsignal.live_trading.launchd_health_check import format_health_console, run_launchd_health_check

    if platform.system() != "Darwin":
        print("launchd-health-check is macOS only.")
        return 1

    from_launchd = bool(getattr(args, "from_launchd", False))
    wait: float | None = None
    if bool(getattr(args, "no_wait", False)):
        wait = 0.0
    elif getattr(args, "wait_seconds", None) is not None:
        wait = float(args.wait_seconds)

    kickstart: bool | None = None
    if bool(getattr(args, "no_kickstart", False)):
        kickstart = False
    elif bool(getattr(args, "kickstart_missing", False)) or from_launchd:
        kickstart = True

    send_tg: bool | None = None
    if bool(getattr(args, "send_telegram", False)) or from_launchd:
        send_tg = True

    notify_ok: bool | None = None
    if bool(getattr(args, "notify_on_ok", False)):
        notify_ok = True

    result = run_launchd_health_check(
        project_dir=getattr(args, "project_dir", None),
        wait_seconds=wait,
        kickstart_missing=kickstart,
        send_telegram=send_tg,
        notify_on_ok=notify_ok,
        check_telegram=True if (from_launchd or bool(getattr(args, "check_telegram", False))) else None,
        from_launchd=from_launchd,
    )
    print(format_health_console(result))
    return 0 if result.system_ok else 1


def cmd_install_launchd_health_check(args: argparse.Namespace) -> int:
    import platform

    from deepsignal.live_trading.launchd_health_installer import (
        format_install_console,
        install_launchd_health_check,
    )
    from deepsignal.live_trading.launchd_installer import project_root

    if platform.system() != "Darwin":
        print("install-launchd-health-check is macOS only.")
        return 1
    try:
        result = install_launchd_health_check(
            project_dir=project_root(getattr(args, "project_dir", None)),
            load_now=not bool(getattr(args, "no_load", False)),
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"install-launchd-health-check failed: {exc}")
        return 1
    print(format_install_console(result))
    return 0 if result.get("loaded") or bool(getattr(args, "no_load", False)) else 1


def cmd_uninstall_launchd_health_check(_args: argparse.Namespace) -> int:
    import json
    import platform

    from deepsignal.live_trading.launchd_health_installer import uninstall_launchd_health_check

    if platform.system() != "Darwin":
        print("uninstall-launchd-health-check is macOS only.")
        return 1
    result = uninstall_launchd_health_check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_launchd_health_check_status(_args: argparse.Namespace) -> int:
    import json
    import platform

    from deepsignal.live_trading.launchd_health_installer import launchd_health_check_install_status

    if platform.system() != "Darwin":
        print("launchd-health-check-status is macOS only.")
        return 1
    print(json.dumps(launchd_health_check_install_status(), ensure_ascii=False, indent=2))
    return 0


def cmd_launchd_runner_test(args: argparse.Namespace) -> int:
    """launchd와 동일 경로/argv로 daily-ai-auto-runner 1회 dry-run."""
    import platform

    from deepsignal.live_trading.launchd_installer import (
        format_runner_test_console,
        launchd_config_from_namespace,
        run_launchd_runner_test,
    )

    if platform.system() != "Darwin":
        print("launchd-runner-test is macOS only.")
        return 1
    cfg = launchd_config_from_namespace(args)
    test = run_launchd_runner_test(
        cfg,
        project_dir=getattr(args, "project_dir", None),
        timeout_seconds=float(getattr(args, "timeout_seconds", 45.0) or 45.0),
        max_iterations=int(getattr(args, "max_iterations", 1) or 1),
    )
    print(format_runner_test_console(test))
    if test.get("exception") or test.get("timed_out"):
        return 1
    if not test.get("import_ok"):
        return 1
    if test.get("pandas_import_ok") is False:
        return 1
    py = str(test.get("python_executable") or "")
    if "Cellar/python" in py or "/opt/homebrew/" in py:
        print("launchd-runner-test: python must be .venv/bin/python, not Homebrew")
        return 1
    rc = test.get("returncode")
    return 0 if rc in (0, None) else int(rc)


def cmd_daily_ai_status(args: argparse.Namespace) -> int:
    """[실전-46] 일일 AI 운영 상태와 다음 명령 안내."""
    from deepsignal.live_trading.daily_ai_trading_workflow import build_daily_ai_status

    result = build_daily_ai_status(
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
        freshness_date=getattr(args, "freshness_date", None),
    )
    from deepsignal.live_trading.daily_ai_status_reader import read_daily_ai_workflow_status

    workflow = read_daily_ai_workflow_status(
        str(getattr(args, "output_dir", "outputs") or "outputs"),
        freshness_date=getattr(args, "freshness_date", None),
    )
    print("DeepSignal AI daily status")
    print(f"Status: {result.status}")
    for key, value in result.checks.items():
        print(f"{key}: {value}")
    labels = workflow.freshness.get("labels") if isinstance(workflow.freshness, dict) else {}
    sources = workflow.freshness.get("sources") if isinstance(workflow.freshness, dict) else {}
    raw_freshness = workflow.freshness if isinstance(workflow.freshness, dict) else {}
    if isinstance(labels, dict):
        print("Freshness:")
        for key in ("plan", "latest_order_plan", "approval", "execution", "report"):
            entry = raw_freshness.get(key) if isinstance(raw_freshness.get(key), dict) else {}
            generated_at = entry.get("generated_at") or "-"
            source = sources.get(key, "-") if isinstance(sources, dict) else "-"
            print(f"  {key}: {labels.get(key, '-')} · source={source} · at={generated_at}")
    if workflow.warnings:
        print("Warnings:")
        for warning in workflow.warnings:
            print(f"  - {warning}")
    print(f"Next: {result.next_command}")
    print(f"Markdown: {result.markdown_path}")
    print(f"JSON: {result.json_path}")
    return 0


def cmd_validate_ai_recommendation(args: argparse.Namespace) -> int:
    """AI 추천 정책을 로컬 DB 기반 in-memory portfolio로 검증한다."""
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.ai_recommendation.validation_engine import (
        format_validation_console,
        run_ai_recommendation_validation,
    )
    from deepsignal.live_trading.ai_recommendation.cost_model import CostModel
    from deepsignal.live_trading.ai_recommendation.fx_model import FXConfig, parse_fallback_rates
    from deepsignal.live_trading.ai_recommendation.liquidity_model import LiquidityConfig
    from deepsignal.live_trading.ai_recommendation.portfolio_risk_model import PortfolioRiskConfig
    from deepsignal.live_trading.ai_recommendation.validation_model import ValidationConfig
    from deepsignal.storage.database import init_database

    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    symbols_raw = str(getattr(args, "symbols", "") or "").strip()
    symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()] if symbols_raw else None
    cost_model = CostModel(
        commission_rate=float(getattr(args, "commission_rate", 0.001) or 0.0),
        tax_rate=float(getattr(args, "tax_rate", 0.0) or 0.0),
        slippage_bps=float(getattr(args, "slippage_bps", 5.0) or 0.0),
        min_order_value=float(getattr(args, "min_order_value", 10_000.0) or 0.0),
        max_order_value=getattr(args, "max_order_value", None),
        liquidity_limit_pct=getattr(args, "liquidity_limit_pct", None),
        currency=str(getattr(args, "currency", "KRW") or "KRW"),
        enabled=not bool(getattr(args, "no_costs", False)),
    )
    cfg = ValidationConfig(
        symbols=symbols,
        start_date=getattr(args, "start_date", None),
        end_date=getattr(args, "end_date", None),
        initial_cash=float(getattr(args, "initial_cash", 1_000_000.0) or 1_000_000.0),
        include_sell_reduce=bool(getattr(args, "include_sell_reduce", False)),
        benchmark=bool(getattr(args, "benchmark", True)),
        risk_free_rate=float(getattr(args, "risk_free_rate", 0.0) or 0.0),
        costs_enabled=not bool(getattr(args, "no_costs", False)),
        cost_model=cost_model,
        fx_config=FXConfig(
            base_currency=str(getattr(args, "base_currency", "KRW") or "KRW").upper(),
            default_symbol_currency=str(getattr(args, "default_symbol_currency", "KRW") or "KRW").upper(),
            fx_rates_path=getattr(args, "fx_rates", None),
            symbol_currency_map_path=getattr(args, "symbol_currency_map", None),
            fallback_rates=parse_fallback_rates(getattr(args, "fallback_fx", None)),
        ),
        liquidity_config=LiquidityConfig(
            liquidity_limit_pct=getattr(args, "liquidity_limit_pct", None),
            min_daily_volume=getattr(args, "min_daily_volume", None),
            min_daily_value=getattr(args, "min_daily_value", None),
            volume_lookback_days=int(getattr(args, "volume_lookback_days", 20) or 20),
            use_average_volume=True,
        ),
        portfolio_risk_config=PortfolioRiskConfig(
            max_symbol_weight=float(getattr(args, "max_symbol_weight", 0.35) or 0.35),
            max_sector_weight=float(getattr(args, "max_sector_weight", 0.50) or 0.50),
            high_correlation_threshold=float(getattr(args, "correlation_threshold", 0.80) or 0.80),
            lookback_days=int(getattr(args, "correlation_lookback_days", 60) or 60),
            min_correlation_points=int(getattr(args, "min_correlation_points", 20) or 20),
            sector_map_path=getattr(args, "sector_map", None),
        ),
        min_trade_value=0.0 if bool(getattr(args, "no_costs", False)) else float(getattr(args, "min_order_value", 10_000.0) or 0.0),
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
    )
    result, json_path, md_path, csv_path, risk_csv_path = run_ai_recommendation_validation(db_path, config=cfg)
    print(format_validation_console(result, json_path, md_path, csv_path, risk_csv_path))
    return 0


def cmd_reconcile_live_account(args: argparse.Namespace) -> int:
    """[실전-6] 브로커 조회 vs DB `real_positions` 비교."""
    if not bool(getattr(args, "network", False)):
        print("DeepSignal reconcile-live-account: --network is required for KIS HTTP.")
        return 1
    if str(getattr(args, "broker", "kis")).strip().lower() != "kis":
        print("DeepSignal reconcile-live-account: only --broker kis is supported.")
        return 1

    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.kis_broker import KISBroker
    from deepsignal.live_trading.kis_config import KisConfigError, load_kis_config_from_env
    from deepsignal.live_trading.live_account_sync import summarize_kis_balance_raw, write_kis_account_debug_summary
    from deepsignal.live_trading.reconcile import reconcile_real_account, write_reconcile_report_paths
    from deepsignal.storage.database import init_database, load_latest_real_positions

    out_dir = str(getattr(args, "output_dir", "outputs") or "outputs")
    try:
        cfg = load_kis_config_from_env()
    except KisConfigError as e:
        print(f"DeepSignal reconcile-live-account: KIS config error: {e}")
        return 1

    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    br = KISBroker(cfg, safe_mode=True)
    broker_pos = br.get_positions()
    if bool(getattr(args, "debug_raw", False)):
        summary = summarize_kis_balance_raw(br.last_balance_response_body)
        print("KIS raw debug (masked):")
        print(f"- top_level_keys: {summary.get('top_level_keys')}")
        print(f"- output1 rows: {(summary.get('output1') or {}).get('row_count')} keys={(summary.get('output1') or {}).get('keys')}")
        print(f"- output2 rows: {(summary.get('output2') or {}).get('row_count')} keys={(summary.get('output2') or {}).get('keys')}")
        dbg = write_kis_account_debug_summary(br.last_balance_response_body, output_dir=out_dir)
        print(f"Debug JSON: {dbg.as_posix()}")
    db_pos = load_latest_real_positions(db_path, broker="kis")
    result = reconcile_real_account(broker_pos, db_pos)
    from deepsignal.live_trading.fill_tracker import load_open_partial_fill_statuses

    for pfs in load_open_partial_fill_statuses(db_path, broker="kis"):
        result.warnings.append(
            f"WARNING: open partial fill order_id={pfs.order_id} symbol={pfs.symbol} "
            f"remaining_qty={pfs.remaining_quantity}"
        )
    jp, mp = write_reconcile_report_paths(
        result,
        output_dir=out_dir,
        extra={"db_path": db_path, "broker": "kis"},
    )

    print("DeepSignal reconcile result")
    for w in result.warnings:
        print(w)
    print("Matched:")
    for s in result.matched:
        print(f"- {s}")
    print("Missing in DB:")
    for x in result.missing_in_db:
        print(f"- {x.symbol} broker_qty={x.broker_quantity}")
    print("Missing in broker:")
    for x in result.missing_in_broker:
        print(f"- {x.symbol} db_qty={x.db_quantity}")
    print("Quantity mismatch:")
    for x in result.quantity_mismatch:
        print(f"- {x.symbol} broker={x.broker_quantity} db={x.db_quantity}")
    print(f"success={result.success}")
    print(f"JSON: {jp.as_posix()}")
    print(f"Markdown: {mp.as_posix()}")
    return 0 if result.success else 1


def cmd_live_plan(args: argparse.Namespace) -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.live_trading.live_order_plan import LiveOrderPlanConfig, run_live_plan_cli
    from deepsignal.storage.database import init_database

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    d = LiveOrderPlanConfig()
    cfg = LiveOrderPlanConfig(
        capital=float(getattr(args, "capital", d.capital)),
        max_symbols=int(getattr(args, "max_symbols", d.max_symbols)),
        max_position_pct=float(getattr(args, "max_position_pct", d.max_position_pct)),
        min_order_value=float(getattr(args, "min_order_value", d.min_order_value)),
        cash_buffer_pct=float(getattr(args, "cash_buffer_pct", d.cash_buffer_pct)),
        currency=str(getattr(args, "currency", d.currency)),
        dry_run=bool(getattr(args, "dry_run", True)),
    )
    run_live_plan_cli(path_str, cfg)


def cmd_paper_rebalance(args: argparse.Namespace) -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.paper_trading.paper_trading_engine import (
        paper_rebalance_config_from_namespace,
    )
    from deepsignal.pipelines.daily_pipeline import paper_rebalance_to_db
    from deepsignal.storage.database import init_database

    settings = load_settings()
    path_str = str(init_database(settings.db_path))
    paper_rebalance_to_db(
        path_str,
        settings,
        rebalance_config=paper_rebalance_config_from_namespace(args),
    )


def cmd_run_daily(args: argparse.Namespace):
    """run-daily 실행. `DailyPipelineResult` 반환 (종료 코드는 main에서 매핑)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from deepsignal.config.settings import load_settings
    from deepsignal.paper_trading.paper_trading_engine import (
        paper_rebalance_config_from_namespace,
    )
    from deepsignal.pipelines.daily_pipeline import run_daily_pipeline

    settings = load_settings()
    raw_syms = getattr(args, "symbols", None)
    symbols_tuple: tuple[str, ...] | None = None
    if isinstance(raw_syms, str) and raw_syms.strip():
        symbols_tuple = tuple(
            p.strip() for p in raw_syms.split(",") if p.strip()
        )

    return run_daily_pipeline(
        settings,
        skip_news=bool(getattr(args, "skip_news", False)),
        skip_market=bool(getattr(args, "skip_market", False)),
        skip_macro=bool(getattr(args, "skip_macro", False)),
        symbols=symbols_tuple,
        run_backtest=not bool(getattr(args, "no_backtest", False)),
        run_paper=not bool(getattr(args, "no_paper", False)),
        paper_rebalance=bool(getattr(args, "paper_rebalance", False)),
        write_log_json=bool(getattr(args, "log_json", False)),
        paper_rebalance_config=paper_rebalance_config_from_namespace(args),
        full_analysis=not bool(getattr(args, "no_full_analysis", False)),
        sync_live_account=bool(getattr(args, "sync_live", False)),
    )


def cmd_show_signals() -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.reporting.report_service import render_signals_report
    from deepsignal.storage.database import init_database

    settings = load_settings()
    db_path = init_database(settings.db_path)
    print(render_signals_report(str(db_path)))


def cmd_show_backtests() -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.reporting.report_service import render_backtests_report
    from deepsignal.storage.database import init_database

    settings = load_settings()
    db_path = init_database(settings.db_path)
    print(render_backtests_report(str(db_path)))


def cmd_show_paper() -> None:
    from deepsignal.config.settings import load_settings
    from deepsignal.reporting.report_service import render_paper_report
    from deepsignal.storage.database import init_database

    settings = load_settings()
    db_path = init_database(settings.db_path)
    print(render_paper_report(str(db_path)))


def _cmd_setup_webapp_url(webapp_url: str = "") -> int:
    """텔레그램 봇 WebApp 메뉴 버튼 등록 헬퍼."""
    import os as _os
    from dotenv import load_dotenv as _ld
    _ld()
    token = _os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("[ERROR] DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN not set in .env")
        return 1
    url = webapp_url or _os.getenv("DEEPSIGNAL_WEBUI_PUBLIC_URL", "").strip()
    if not url:
        print("[ERROR] No WebApp URL provided. Pass --url or set DEEPSIGNAL_WEBUI_PUBLIC_URL in .env")
        return 1
    from deepsignal.live_trading.telegram.approval import telegram_api_post
    # 메뉴 버튼 등록
    res = telegram_api_post(
        "setChatMenuButton",
        {"menu_button": {"type": "web_app", "text": "📊 대시보드", "web_app": {"url": url}}},
        bot_token=token,
    )
    if res.get("ok"):
        print(f"[OK] Telegram WebApp menu button set → {url}")
    else:
        print(f"[WARN] setChatMenuButton: {res.get('description', res)}")
    # 봇 설명 업데이트
    res2 = telegram_api_post(
        "setMyDescription",
        {"description": f"DeepSignal AI 자동매매\n대시보드: {url}"},
        bot_token=token,
    )
    print(f"[{'OK' if res2.get('ok') else 'WARN'}] setMyDescription")
    return 0


def cmd_dashboard() -> None:
    from deepsignal.config.settings import load_settings
    try:
        from deepsignal.dashboard.dashboard_app import run_dashboard
    except ModuleNotFoundError as exc:
        if exc.name != "_tkinter":
            raise
        raise SystemExit(
            "tkinter is not available in this Python installation. "
            "Install a Python build with Tk support to use the dashboard."
        ) from exc
    from deepsignal.storage.database import init_database

    settings = load_settings()
    db_path = str(init_database(settings.db_path))
    run_dashboard(db_path)


def _attach_paper_rebalance_cost_args(p: argparse.ArgumentParser) -> None:
    """`paper-rebalance`·`run-daily` 공통: 모의 리밸런스 비용 옵션."""
    from deepsignal.paper_trading.paper_trading_engine import PaperRebalanceConfig

    d = PaperRebalanceConfig()
    p.add_argument(
        "--commission-rate",
        type=float,
        default=d.commission_rate,
        metavar="RATE",
        help="paper-rebalance commission (decimal, e.g. 0.001 = 0.1%%). Default %(default)s",
    )
    p.add_argument(
        "--slippage-rate",
        type=float,
        default=d.slippage_rate,
        metavar="RATE",
        help="paper-rebalance slippage vs last close (decimal). Default %(default)s",
    )
    p.add_argument(
        "--min-trade-value",
        type=float,
        default=d.min_trade_value,
        metavar="USD",
        help="Skip rebalance if abs(target$-current$) is below this. Default %(default)s",
    )
    p.add_argument(
        "--rebalance-threshold",
        type=float,
        default=d.rebalance_threshold,
        metavar="FRAC",
        help="Skip rebalance if abs(target$-current$) < equity*this. Default %(default)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="DeepSignal", description="DeepSignal CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("collect-news", help="RSS 뉴스 수집 후 SQLite 저장")
    sub.add_parser("collect-market", help="yfinance 일봉 OHLCV 수집 후 SQLite 저장")
    sub.add_parser(
        "collect-macro",
        help="yfinance 기반 거시 지표(VIX·DXY·미국채 10Y) 수집 후 economic_indicators 저장",
    )
    sub.add_parser(
        "analyze-macro",
        help="economic_indicators 최신값 기반 macro_score·시장 국면 요약",
    )
    sub.add_parser(
        "analyze-portfolio",
        help="최신 signals·거시 국면 기반 목표 배분안(분석만, 리밸런싱·실주문 없음)",
    )
    p_an = sub.add_parser(
        "analyze-news",
        help="news_items 제목·요약 키워드 기반 뉴스 감성 요약 (외부 AI 없음)",
    )
    p_an.add_argument("symbol", type=str, help="티커 (예: AAPL)")
    p_at = sub.add_parser("analyze-technical", help="market_prices 기반 RSI/EMA 요약 출력")
    p_at.add_argument("symbol", type=str, help="티커 (예: AAPL)")
    p_sc = sub.add_parser("score-symbol", help="기술지표 기반 점수 산출 후 signals 저장")
    p_sc.add_argument("symbol", type=str, help="티커 (예: AAPL)")
    p_ac = sub.add_parser(
        "show-analysis-conditions",
        help="숫자 분석 임계값 단일 출처 JSON/Markdown 출력 (주문·네트워크 없음)",
    )
    p_ac.add_argument(
        "--output-dir",
        type=str,
        default="outputs",
        metavar="DIR",
        help="산출물 디렉터리 (기본 outputs)",
    )
    p_bt = sub.add_parser("backtest-symbol", help="과거 OHLCV 기반 단일 종목 백테스트 v1/v2")
    p_bt.add_argument("symbol", type=str, help="티커 (예: AAPL)")
    p_bt.add_argument(
        "--include-news",
        action="store_true",
        help="각 거래일까지 published_at이 있는 뉴스만 감성 반영(룩어헤드 없음, DB 필요)",
    )
    p_pt = sub.add_parser("paper-step", help="모의투자 한 스텝(가상 체결 기록, 실주문 없음)")
    p_pt.add_argument("symbol", type=str, help="티커 (예: AAPL)")
    p_pr = sub.add_parser(
        "paper-rebalance",
        help="포트폴리오 목표 비중으로 모의 계좌 리밸런싱(실주문 없음, 최신 종가·비용 모델)",
    )
    _attach_paper_rebalance_cost_args(p_pr)
    from deepsignal.live_trading.live_order_plan import LiveOrderPlanConfig

    d_lp = LiveOrderPlanConfig()
    p_lp = sub.add_parser(
        "live-plan",
        help="실전 매수 전 단계: 주문 계획 JSON/Markdown만 생성 (브로커·실주문 없음)",
    )
    p_lp.add_argument(
        "--capital",
        type=float,
        default=d_lp.capital,
        metavar="AMT",
        help="계획에 사용할 총 자본(기본 %(default)s)",
    )
    p_lp.add_argument(
        "--max-symbols",
        type=int,
        default=d_lp.max_symbols,
        metavar="N",
        help="최대 BUY 종목 수 (기본 %(default)s)",
    )
    p_lp.add_argument(
        "--max-position-pct",
        type=float,
        default=d_lp.max_position_pct,
        metavar="FRAC",
        help="단일 종목 최대 비중(자본 대비, 기본 %(default)s)",
    )
    p_lp.add_argument(
        "--min-order-value",
        type=float,
        default=d_lp.min_order_value,
        metavar="AMT",
        help="최소 추정 주문 금액 미만 제외 (기본 %(default)s)",
    )
    p_lp.add_argument(
        "--cash-buffer-pct",
        type=float,
        default=d_lp.cash_buffer_pct,
        metavar="FRAC",
        help="현금 유지 비율(기본 %(default)s)",
    )
    p_lp.add_argument(
        "--currency",
        type=str,
        default=d_lp.currency,
        help="표시 통화 (기본 %(default)s, 미국 주식 중심)",
    )
    p_lp.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="계획만 생성(기본: true). 실주문 경로 없음",
    )
    p_la = sub.add_parser(
        "live-approve",
        help="live order plan 승인·검증 (dry-run / KIS safe / [실전-4] 단발 실매수)",
    )
    p_la.add_argument(
        "--broker",
        type=str,
        choices=["dry-run", "kis"],
        default="dry-run",
        metavar="NAME",
        help="dry-run(기본) 또는 kis",
    )
    p_la.add_argument(
        "--plan",
        type=str,
        required=True,
        metavar="PATH",
        help="live_order_plan_YYYYMMDD.json 경로",
    )
    p_la.add_argument(
        "--approved",
        action="store_true",
        help="사용자가 명시적으로 승인했을 때만 dry-run 실행",
    )
    p_la.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="[실전-2] 기본 true. --no-dry-run 은 거부됩니다",
    )
    p_la.add_argument(
        "--execute",
        action="store_true",
        help="[실전-4] 실매수 1회 요청(KIS·가드·--final-confirm·KIS_ENV=live·--allow-live-env 필수)",
    )
    p_la.add_argument(
        "--final-confirm",
        type=str,
        default="",
        metavar="TEXT",
        help="실매수 시 반드시 I_UNDERSTAND_REAL_ORDER",
    )
    p_la.add_argument(
        "--allow-live-env",
        action="store_true",
        help="KIS_ENV=live 실계좌 주문 허용을 명시",
    )
    p_la.add_argument(
        "--max-total-order-value",
        type=float,
        default=100_000.0,
        metavar="AMT",
        help="실매수 총 주문 추정금액 상한 (기본 %(default)s)",
    )
    p_la.add_argument(
        "--max-single-order-value",
        type=float,
        default=50_000.0,
        metavar="AMT",
        help="실매수 단일 주문 추정금액 상한 (기본 %(default)s)",
    )
    p_la.add_argument(
        "--max-orders",
        type=int,
        default=1,
        metavar="N",
        help="실매수 최대 주문 건수 (기본 %(default)s)",
    )
    p_la.add_argument(
        "--allow-symbol",
        action="append",
        default=None,
        metavar="PDNO",
        help="실매수 허용 6자리 종목(여러 번 지정 가능). 미지정 시 화이트리스트 없음",
    )
    p_la.add_argument(
        "--output-dir",
        type=str,
        default="outputs",
        metavar="DIR",
        help="감사 로그 저장 디렉터리 (기본 %(default)s)",
    )
    p_la.add_argument(
        "--require-pre-trade-runbook",
        action="store_true",
        help="[실전-11] 최근 PRE_TRADE_READY pre-trade-runbook 없으면 --execute 차단",
    )
    p_la.add_argument(
        "--pre-trade-runbook",
        type=str,
        default=None,
        metavar="PATH",
        help="검증할 pre_trade_runbook JSON (미지정 시 output-dir 최신 파일)",
    )
    p_la.add_argument(
        "--pre-trade-runbook-max-age-minutes",
        type=int,
        default=10,
        metavar="N",
        help="pre-trade runbook 유효 시간(분, 기본 %(default)s)",
    )
    p_test_plan = sub.add_parser(
        "generate-test-order-plan",
        help="[긴급-MVP] 소액 BUY/LIMIT 테스트 주문안 JSON 생성 (실행 없음)",
    )
    p_test_plan.add_argument("--symbol", type=str, required=True, metavar="SYMBOL", help="종목 코드 (예: 005930)")
    p_test_plan.add_argument("--quantity", type=int, default=1, metavar="N", help="수량 (기본 1, 최대 1)")
    p_test_plan.add_argument("--limit-price", type=float, required=True, metavar="PRICE", help="지정가 (필수)")
    p_test_plan.add_argument("--max-order-value", type=float, default=100_000.0, metavar="AMT", help="최대 주문 금액")
    p_test_plan.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_test_plan.add_argument("--currency", type=str, default="KRW", metavar="CCY")

    p_tg_test = sub.add_parser(
        "telegram-test",
        help="[긴급-MVP] Telegram Bot 연결 테스트 (sendMessage)",
    )
    p_tg_test.add_argument("--message", type=str, default="DeepSignal 연결 테스트", metavar="TEXT")
    p_tg_test.add_argument("--send", action="store_true", help="Telegram sendMessage 실제 호출")
    p_tg_test.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_tg_test.add_argument("--timeout-seconds", type=float, default=5.0, metavar="SEC")

    p_tg_req = sub.add_parser(
        "telegram-approval-request",
        help="[실전-44] Telegram 실주문 승인 요청 생성/전송",
    )
    p_tg_req.add_argument("--plan", type=str, required=True, metavar="PATH")
    p_tg_req.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_tg_req.add_argument("--send", action="store_true", help="Telegram Bot API sendMessage 호출")
    p_tg_req.add_argument("--expires-minutes", type=int, default=10, metavar="N")
    p_tg_req.add_argument("--max-total-order-value", type=float, default=100_000.0, metavar="AMT")
    p_tg_req.add_argument("--max-single-order-value", type=float, default=50_000.0, metavar="AMT")
    p_tg_req.add_argument("--max-orders", type=int, default=1, metavar="N")
    p_tg_req.add_argument("--allowed-chat-id", type=str, default=None, metavar="CHAT")
    p_tg_req.add_argument("--timeout-seconds", type=float, default=5.0, metavar="SEC")
    p_tg_req.add_argument(
        "--no-auto-execute",
        action="store_true",
        help="전송 후 승인 대기·자동 실행 생략 (legacy: execute-last-approved)",
    )
    p_tg_req.add_argument("--wait-seconds", type=float, default=600.0, metavar="SEC", help="승인 대기(초)")
    p_tg_req.add_argument("--poll-interval", type=float, default=2.0, metavar="SEC", help="getUpdates 폴링 간격")

    p_tg_listen = sub.add_parser(
        "telegram-approval-listen",
        help="[실전-최종UX] Telegram 승인 callback 수신 후 즉시 실주문 실행 (기본)",
    )
    p_tg_listen.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_tg_listen.add_argument("--allowed-chat-id", type=str, default=None, metavar="CHAT")
    p_tg_listen.add_argument("--timeout-seconds", type=float, default=5.0, metavar="SEC")
    p_tg_listen.add_argument("--offset", type=int, default=None, metavar="N")
    p_tg_listen.add_argument(
        "--no-auto-execute",
        action="store_true",
        help="승인 audit만 생성 (실주문 미실행)",
    )
    p_tg_listen.add_argument(
        "--watch",
        action="store_true",
        help="승인/거부까지 폴링 대기 (--send 없이 별도 listen 시)",
    )
    p_tg_listen.add_argument("--wait-seconds", type=float, default=600.0, metavar="SEC")
    p_tg_listen.add_argument("--poll-interval", type=float, default=2.0, metavar="SEC")

    p_tg_status = sub.add_parser(
        "telegram-approval-status",
        help="[실전-44] 최신 Telegram 승인 요청 상태 표시",
    )
    p_tg_status.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_exec_last = sub.add_parser(
        "execute-last-approved",
        help="[실전-45] 최신 Telegram 승인 audit 기반 단축 실주문 실행",
    )
    p_exec_last.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_exec_last.add_argument("--send", action="store_true", help="실행 결과를 Telegram으로 전송")
    p_exec_last.add_argument("--timeout-seconds", type=float, default=5.0, metavar="SEC")
    p_exec_last.add_argument(
        "--wait-seconds",
        type=float,
        default=60.0,
        metavar="SEC",
        help="Telegram 승인 callback 대기 시간(초, 기본 60)",
    )
    p_exec_last.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        metavar="SEC",
        help="승인 callback polling 간격(초, 기본 2)",
    )

    p_exec_approved = sub.add_parser(
        "execute-approved",
        help="[실전-45] request-id 기반 Telegram 승인 단축 실주문 실행",
    )
    p_exec_approved.add_argument("--request-id", type=str, required=True, metavar="REQUEST_ID")
    p_exec_approved.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_exec_approved.add_argument("--send", action="store_true", help="실행 결과를 Telegram으로 전송")
    p_exec_approved.add_argument("--timeout-seconds", type=float, default=5.0, metavar="SEC")
    p_daily_ai_plan = sub.add_parser(
        "daily-ai-trade-plan",
        help="[실전-46] 일일 AI 장 분석/추천/주문안 생성 (실주문 없음)",
    )
    p_daily_ai_plan.add_argument("--broker", type=str, choices=["kis"], default="kis", metavar="NAME")
    p_daily_ai_plan.add_argument("--network", action="store_true", help="KIS 조회 context 사용 가능")
    p_daily_ai_plan.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_daily_ai_plan.add_argument(
        "--max-order-value",
        type=float,
        default=100_000.0,
        metavar="AMT",
        help="AI 주문안 최대 금액(capital_limit 포지션 사이징, KRW)",
    )
    p_daily_ai_plan.add_argument(
        "--debug-plan",
        action="store_true",
        help="추천별 주문안 제외 사유 상세 출력",
    )
    p_daily_ai_plan.add_argument(
        "--allow-test-plan-order",
        action="store_true",
        help="MVP 검증용: score/confidence 완화로 LIMIT BUY 1건 포함 (장외 승인·장중 실행)",
    )
    p_daily_ai_plan.add_argument(
        "--ignore-safety-block-for-test",
        action="store_true",
        help="--allow-test-plan-order 와 함께만: safety_audit=BLOCKED를 warning으로 downgrade (실행 가드 유지)",
    )

    p_daily_ai_report = sub.add_parser(
        "daily-ai-trade-report",
        help="[실전-46] 일일 AI 투자 운영 리포트 생성",
    )
    p_daily_ai_report.add_argument("--broker", type=str, choices=["kis"], default="kis", metavar="NAME")
    p_daily_ai_report.add_argument("--network", action="store_true", help="네트워크 조회 리포트가 있으면 운영자가 선행 실행")
    p_daily_ai_report.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")

    p_daily_ai_status = sub.add_parser(
        "daily-ai-status",
        help="[실전-46] 일일 AI 운영 상태와 다음 명령 안내",
    )
    p_daily_ai_status.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_daily_ai_auto = sub.add_parser(
        "daily-ai-auto-runner",
        help="[운영고정] 일일 AI 자동 루프 (plan/승인/실행/report, Telegram)",
    )
    p_daily_ai_auto.add_argument("--broker", type=str, choices=["kis"], default="kis", metavar="NAME")
    p_daily_ai_auto.add_argument("--network", action="store_true", help="KIS 조회로 plan 생성")
    p_daily_ai_auto.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_daily_ai_auto.add_argument("--plan-time", type=str, default="09:05", metavar="HH:MM")
    p_daily_ai_auto.add_argument("--report-time", type=str, default="15:40", metavar="HH:MM")
    p_daily_ai_auto.add_argument("--max-order-value", type=float, default=300_000.0, metavar="AMT")
    p_daily_ai_auto.add_argument("--max-single-order-value", type=float, default=300_000.0, metavar="AMT")
    p_daily_ai_auto.add_argument("--max-total-order-value", type=float, default=300_000.0, metavar="AMT")
    p_daily_ai_auto.add_argument("--max-orders", type=int, default=1, metavar="N")
    p_daily_ai_auto.add_argument("--expires-minutes", type=int, default=420, metavar="N")
    p_daily_ai_auto.add_argument("--poll-interval", type=float, default=3.0, metavar="SEC")
    p_daily_ai_auto.add_argument("--loop-sleep-seconds", type=float, default=15.0, metavar="SEC")
    p_daily_ai_auto.add_argument("--timeout-seconds", type=float, default=10.0, metavar="SEC")
    p_daily_ai_auto.add_argument("--allow-test-plan-order", action="store_true")
    p_daily_ai_auto.add_argument("--ignore-safety-block-for-test", action="store_true")
    p_daily_ai_auto.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        metavar="N",
        help="테스트용: N회 tick 후 종료 (미지정 시 상시 실행)",
    )

    from deepsignal.live_trading.launchd_installer import add_launchd_runner_arguments

    p_install_launchd = sub.add_parser(
        "install-launchd",
        help="[운영-자동시작] macOS LaunchAgent 등록 (부팅·로그인 시 auto-runner)",
    )
    p_install_launchd.add_argument("--project-dir", type=str, default=None, metavar="PATH", help="프로젝트 루트 (기본: cwd)")
    p_install_launchd.add_argument("--no-load", action="store_true", help="plist만 생성, launchctl load 생략")
    p_install_launchd.add_argument(
        "--no-sanitize-path",
        action="store_true",
        help="경로에 '#' 있어도 symlink 없이 원본 경로로 plist 생성 (진단용)",
    )
    add_launchd_runner_arguments(p_install_launchd)

    p_launchd_runner_test = sub.add_parser(
        "launchd-runner-test",
        help="[운영-자동시작] launchd와 동일 argv/env로 runner 1회 실행 (OS vs runner 분리)",
    )
    p_launchd_runner_test.add_argument("--project-dir", type=str, default=None, metavar="PATH")
    p_launchd_runner_test.add_argument("--max-iterations", type=int, default=1, metavar="N")
    add_launchd_runner_arguments(p_launchd_runner_test)

    p_uninstall_launchd = sub.add_parser("uninstall-launchd", help="[운영-자동시작] LaunchAgent 제거")
    p_uninstall_launchd.add_argument("--keep-plist", action="store_true", help="unload만 하고 plist 파일 유지")
    p_uninstall_launchd.add_argument("--keep-loaded", action="store_true", help="unload 하지 않음")

    p_launchd_status = sub.add_parser("launchd-status", help="[운영-자동시작] LaunchAgent 상태 확인")
    p_launchd_status.add_argument("--project-dir", type=str, default=None, metavar="PATH")

    p_install_health = sub.add_parser(
        "install-launchd-health-check",
        help="[운영-자동시작] 로그인 후 launchd 건강검사 LaunchAgent 등록",
    )
    p_install_health.add_argument("--project-dir", type=str, default=None, metavar="PATH")
    p_install_health.add_argument("--no-load", action="store_true", help="plist만 생성, load 생략")

    sub.add_parser(
        "uninstall-launchd-health-check",
        help="[운영-자동시작] launchd 건강검사 LaunchAgent 제거",
    )

    p_health_status = sub.add_parser(
        "launchd-health-check-status",
        help="[운영-자동시작] 건강검사 LaunchAgent 설치·실행 상태",
    )

    p_health_check = sub.add_parser(
        "launchd-health-check",
        help="[운영-자동시작] 필수 launchd 프로세스 점검 (재부팅 후 수동/자동)",
    )
    p_health_check.add_argument("--project-dir", type=str, default=None, metavar="PATH")
    p_health_check.add_argument("--wait-seconds", type=float, default=None, metavar="SEC")
    p_health_check.add_argument("--no-wait", action="store_true", help="마운트 대기 생략")
    p_health_check.add_argument("--kickstart-missing", action="store_true", help="미실행 job kickstart")
    p_health_check.add_argument("--no-kickstart", action="store_true", help="kickstart 비활성")
    p_health_check.add_argument("--send-telegram", action="store_true", help="Telegram 전송 (실패 시)")
    p_health_check.add_argument("--notify-on-ok", action="store_true", help="전부 정상일 때도 Telegram")
    p_health_check.add_argument(
        "--from-launchd",
        action="store_true",
        help="LaunchAgent용: env 기본값(wait/kickstart/telegram/봇점검/부팅알림)",
    )
    p_health_check.add_argument(
        "--check-telegram",
        action="store_true",
        help="Telegram getMe·메시지·메뉴 키보드 점검 포함",
    )

    p_daily_ai_status.add_argument(
        "--freshness-date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Daily AI freshness 기준 날짜 (기본: Asia/Seoul 오늘)",
    )
    p_crypto_check = sub.add_parser("crypto-check", help="[실전-코인-01] Upbit 설정·잔고·현재가 점검")
    p_crypto_check.add_argument("--broker", type=str, default="upbit", choices=["upbit"])
    p_crypto_check.add_argument("--network", action="store_true", help="실 API 조회 (주문 없음)")

    p_crypto_plan = sub.add_parser("crypto-daily-plan", help="[실전-코인-01] 코인 매수 추천·계획 생성")
    p_crypto_plan.add_argument("--broker", type=str, default="upbit", choices=["upbit"])
    p_crypto_plan.add_argument("--market", type=str, default="KRW", help="(legacy) KRW 마켓 접두사")
    from deepsignal.crypto_trading.crypto_universe import add_crypto_universe_cli_args

    add_crypto_universe_cli_args(p_crypto_plan)
    p_crypto_plan.add_argument("--max-order-value", type=float, default=10_000.0, metavar="KRW")
    p_crypto_plan.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_crypto_plan.add_argument("--network", action="store_true", help="실시간 시세 조회")
    p_crypto_plan.add_argument("--take-profit-pct", type=float, default=2.0, metavar="PCT", help="익절 SELL 기준 수익률(%)")
    p_crypto_plan.add_argument(
        "--take-profit-buffer-pct",
        type=float,
        default=0.05,
        metavar="PCT",
        help="익절 근접 buffer(%%p). TP-buffer 이상이면 near_take_profit SELL",
    )
    p_crypto_plan.add_argument("--stop-loss-pct", type=float, default=-1.5, metavar="PCT", help="손절 SELL 기준 수익률(%)")
    p_crypto_plan.add_argument(
        "--stop-loss-buffer-pct",
        type=float,
        default=0.05,
        metavar="PCT",
        help="손절 근접 buffer(%%p). SL+buffer 이하면 near_stop_loss SELL",
    )
    p_crypto_plan.add_argument(
        "--min-volume-ratio",
        type=float,
        default=0.8,
        metavar="RATIO",
        help="BUY 거래량 ratio 최소값 (기본 0.8, 완화 예: 0.7)",
    )
    p_crypto_plan.add_argument(
        "--debug-holdings",
        action="store_true",
        help="보유 코인·평단·수익률 디버그 출력",
    )
    p_crypto_plan.add_argument(
        "--debug-quality",
        action="store_true",
        help="추천 없을 때 BUY/SELL 진단 JSON 전체 출력",
    )

    p_crypto_tg = sub.add_parser("crypto-telegram-approval", help="[실전-코인-01] Telegram 승인 요청")
    p_crypto_tg.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_crypto_tg.add_argument("--send", action="store_true", help="Telegram 전송")
    p_crypto_tg.add_argument("--poll", action="store_true", help="승인/거부 폴링")
    p_crypto_tg.add_argument("--execute", action="store_true", help="승인 시 실주문 (기본 dry-run)")
    p_crypto_tg.add_argument(
        "--wait-fill-seconds",
        type=float,
        default=0.0,
        metavar="SEC",
        help="주문 후 체결 조회 대기(0=비활성, 기본 0)",
    )
    p_crypto_tg.add_argument(
        "--fill-poll-interval",
        type=float,
        default=3.0,
        metavar="SEC",
        help="체결 조회 간격(초)",
    )

    p_crypto_paper = sub.add_parser(
        "crypto-paper-status",
        help="[코인] CRYPTO_PAPER_MODE 기간·페이퍼 성과 요약",
    )
    p_crypto_paper.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")

    p_crypto_auto = sub.add_parser("crypto-auto-runner", help="[실전-코인-01] 코인 24h auto-runner")
    p_crypto_auto.add_argument("--broker", type=str, default="upbit", choices=["upbit"])
    p_crypto_auto.add_argument("--interval-minutes", type=float, default=1.0, metavar="MIN")
    p_crypto_auto.add_argument(
        "--max-order-value",
        type=float,
        default=0.0,
        metavar="KRW",
        help="0=가용·점수 자동 (기본), >0=건당 상한",
    )
    p_crypto_auto.add_argument(
        "--max-orders-per-day",
        type=int,
        default=0,
        metavar="N",
        help="0=자동 (기본), >0=일일 BUY 상한",
    )
    from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

    _crypto_thr = DEFAULT_ANALYSIS_CONDITIONS.crypto
    p_crypto_auto.add_argument("--take-profit-pct", type=float, default=_crypto_thr.take_profit_pct, metavar="PCT")
    p_crypto_auto.add_argument("--take-profit-buffer-pct", type=float, default=0.05, metavar="PCT")
    p_crypto_auto.add_argument("--stop-loss-pct", type=float, default=_crypto_thr.stop_loss_pct, metavar="PCT")
    p_crypto_auto.add_argument(
        "--stop-loss-buffer-pct", type=float, default=_crypto_thr.stop_loss_buffer_pct, metavar="PCT"
    )
    p_crypto_auto.add_argument("--min-volume-ratio", type=float, default=_crypto_thr.min_volume_ratio, metavar="RATIO")
    p_crypto_auto.add_argument(
        "--rebuy-cooldown-minutes",
        type=int,
        default=_crypto_thr.rebuy_cooldown_minutes,
        metavar="MIN",
        help="동일 마켓 재매수 쿨다운(분)",
    )
    p_crypto_auto.add_argument(
        "--max-distinct-buy-markets-per-day",
        type=int,
        default=_crypto_thr.max_distinct_buy_markets_per_day,
        metavar="N",
        help="일일 신규 매수 종목 수 상한(0=무제한)",
    )
    p_crypto_auto.add_argument(
        "--max-buy-krw-per-day",
        type=float,
        default=_crypto_thr.max_buy_krw_per_day,
        metavar="KRW",
        help="일일 매수 금액 상한(0=자동/무제한)",
    )
    p_crypto_auto.add_argument(
        "--prefer-non-holding-buy",
        action=argparse.BooleanOptionalAction,
        default=_crypto_thr.prefer_non_holding_buy,
        help="보유하지 않은 종목 우선 매수",
    )
    from deepsignal.crypto_trading.crypto_universe import add_crypto_universe_cli_args

    add_crypto_universe_cli_args(p_crypto_auto)
    p_crypto_auto.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_crypto_auto.add_argument("--no-send", action="store_true", help="Telegram 전송 생략")
    p_crypto_auto.add_argument("--poll", action="store_true", help="승인 폴링")
    p_crypto_auto.add_argument("--execute", action="store_true", help="승인 시 실주문")
    p_crypto_auto.add_argument(
        "--wait-fill-seconds",
        type=float,
        default=0.0,
        metavar="SEC",
        help="승인 주문 후 체결 조회 대기(0=비활성)",
    )
    p_crypto_auto.add_argument(
        "--menu-poll-seconds",
        type=float,
        default=5.0,
        metavar="SEC",
        help="Telegram 메뉴·승인 callback·스트림 신선도 폴링 주기(기본 5초)",
    )
    p_crypto_auto.add_argument(
        "--fill-poll-interval",
        type=float,
        default=3.0,
        metavar="SEC",
        help="체결 조회 간격(초)",
    )

    from deepsignal.crypto_trading.crypto_launchd_installer import add_crypto_launchd_arguments

    p_install_crypto_launchd = sub.add_parser(
        "install-crypto-launchd",
        help="[실전-코인-launchd] macOS LaunchAgent 등록 (crypto-auto-runner 상시)",
    )
    p_install_crypto_launchd.add_argument("--project-dir", type=str, default=None, metavar="PATH")
    p_install_crypto_launchd.add_argument("--no-load", action="store_true", help="plist만 생성, launchctl load 생략")
    p_install_crypto_launchd.add_argument(
        "--no-sanitize-path",
        action="store_true",
        help="경로에 '#' 있어도 symlink 없이 원본 경로로 plist 생성 (진단용)",
    )
    add_crypto_launchd_arguments(p_install_crypto_launchd)

    p_uninstall_crypto_launchd = sub.add_parser(
        "uninstall-crypto-launchd",
        help="[실전-코인-launchd] crypto LaunchAgent 제거",
    )
    p_uninstall_crypto_launchd.add_argument("--keep-plist", action="store_true")
    p_uninstall_crypto_launchd.add_argument("--keep-loaded", action="store_true")

    p_crypto_tune = sub.add_parser(
        "crypto-tune-thresholds",
        help="[실전-코인] outcome DB 기반 TP/SL/min_volume_ratio 자동 튜닝",
    )
    p_crypto_tune.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_crypto_tune.add_argument("--lookback-days", type=int, default=60, metavar="N")
    p_crypto_tune.add_argument("--min-sell-samples", type=int, default=3, metavar="N")
    p_crypto_tune.add_argument("--min-buy-samples", type=int, default=5, metavar="N")

    p_crypto_tg_menu = sub.add_parser(
        "crypto-telegram-menu",
        help="[실전-코인] Telegram 메뉴(자산/추천) 폴링",
    )
    p_crypto_tg_menu.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_crypto_tg_menu.add_argument("--network", action="store_true", help="실 API 조회")
    p_crypto_tg_menu.add_argument(
        "--poll-once",
        action="store_true",
        help="getUpdates 1회 처리 (메뉴 텍스트 + 승인 callback)",
    )
    p_crypto_tg_menu.add_argument(
        "--send-menu",
        action="store_true",
        help="폴링 전 메뉴 키보드 안내 메시지 전송",
    )

    p_crypto_launchd_status = sub.add_parser(
        "crypto-launchd-status",
        help="[실전-코인-launchd] crypto LaunchAgent 상태 확인",
    )
    p_crypto_launchd_status.add_argument("--project-dir", type=str, default=None, metavar="PATH")

    p_binance_stream = sub.add_parser(
        "binance-stream",
        help="[데이터] Binance WS — tick·호가·펀딩·1m/3m/15m OHLCV 실시간 수집",
    )
    p_binance_stream.add_argument(
        "--output-dir",
        type=str,
        default="outputs/binance_stream",
        help="live_state.json 및 bars/*.jsonl 저장 경로",
    )
    p_binance_stream.add_argument(
        "--top",
        type=int,
        default=30,
        help="24h 거래대금 상위 USDT 페어 수 (기본 30)",
    )
    p_binance_stream.add_argument(
        "--symbols",
        type=str,
        default="",
        metavar="SYM,...",
        help="고정 심볼 목록 (예: BTCUSDT,ETHUSDT). 비우면 --top 기준",
    )
    p_binance_stream.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="실행 초 (0=Ctrl+C까지 무한)",
    )
    p_binance_stream.add_argument(
        "--depth-levels",
        type=int,
        default=20,
        help="호가창 depth 레벨 (5~20)",
    )
    p_binance_stream.add_argument(
        "--no-funding",
        action="store_true",
        help="선물 markPrice/펀딩비 스트림 비활성화",
    )
    p_binance_stream.add_argument(
        "--flush-seconds",
        type=float,
        default=5.0,
        help="live_state.json 갱신 주기(초)",
    )
    p_binance_stream.add_argument(
        "--ob-snapshot-seconds",
        type=float,
        default=10.0,
        help="호가창 jsonl 스냅샷 주기(초, 기본 10)",
    )

    p_fetch_fg = sub.add_parser(
        "fetch-fear-greed",
        help="[데이터] Alternative.me Fear & Greed 일별 캐시 갱신",
    )
    p_fetch_fg.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_fetch_fg.add_argument("--force", action="store_true", help="오늘 이미 받았어도 재요청")

    p_binance_features = sub.add_parser(
        "binance-features",
        help="[데이터] Binance live_state → 코인별 feature numpy 벡터 (forward-fill)",
    )
    p_binance_features.add_argument(
        "--output-dir",
        type=str,
        default="outputs/binance_stream",
    )
    p_binance_features.add_argument(
        "--live-state",
        type=str,
        default="",
        help="live_state.json 경로 (기본: output-dir/live_state.json)",
    )
    p_binance_features.add_argument(
        "--fear-greed",
        type=str,
        default="",
        help="공포탐욕 JSON ({\"value\": 55}) 경로 (선택)",
    )
    p_binance_features.add_argument("--btc-symbol", type=str, default="BTCUSDT")
    p_binance_features.add_argument(
        "--verbose",
        action="store_true",
        help="심볼별 피처 값 콘솔 출력",
    )

    p_crypto_validate_ml = sub.add_parser(
        "crypto-validate-ml",
        help="[ML] replay 피처 + TimeSeries CV + 과적합·임계값 리포트 (in-memory)",
    )
    p_crypto_validate_ml.add_argument(
        "--symbols",
        type=str,
        default="BTC,ETH",
        metavar="SYM,...",
        help="BTC,ETH 또는 BTCUSDT,... (기본 BTC,ETH)",
    )
    p_crypto_validate_ml.add_argument("--days", type=int, default=60, help="최근 N일 1m 봉")
    p_crypto_validate_ml.add_argument(
        "--fee",
        type=float,
        default=0.0005,
        help="편도 수수료 비율 (기본 0.05%% = 0.0005)",
    )
    p_crypto_validate_ml.add_argument("--horizon", type=int, default=5, help="레이블 N분 (기본 5)")
    p_crypto_validate_ml.add_argument("--threshold", type=float, default=0.55, help="매수 P(win) 임계값")
    p_crypto_validate_ml.add_argument("--splits", type=int, default=5)
    p_crypto_validate_ml.add_argument("--gap", type=int, default=10, help="TimeSeriesSplit gap")
    p_crypto_validate_ml.add_argument(
        "--slippage",
        type=float,
        default=0.5,
        help="진입 ask + spread×비율 (기본 0.5)",
    )
    p_crypto_validate_ml.add_argument(
        "--stream-dir",
        type=str,
        default="outputs/binance_stream",
    )
    p_crypto_validate_ml.add_argument(
        "--bars-dir",
        type=str,
        default="",
        help="기본: {stream-dir}/bars",
    )
    p_crypto_validate_ml.add_argument("--output-dir", type=str, default="outputs")
    p_crypto_validate_ml.add_argument("--min-warmup", type=int, default=61)
    p_crypto_validate_ml.add_argument("--no-sweep", action="store_true", help="P×N 그리드 스윕 생략")

    p_crypto_ml_suggest = sub.add_parser(
        "crypto-ml-suggest-config",
        help="[ML] CRYPTO_ML_THRESHOLD_REPORT.md → .env 제안 (자동 적용 없음)",
    )
    p_crypto_ml_suggest.add_argument("--output-dir", type=str, default="outputs")

    p_crypto_train_lgbm = sub.add_parser(
        "crypto-train-lgbm",
        help="[ML] LightGBM — N분 후 0.2% 초과 수익 이진분류 (TimeSeriesSplit)",
    )
    p_crypto_train_lgbm.add_argument(
        "--bars-dir",
        type=str,
        default="outputs/binance_stream/bars",
    )
    p_crypto_train_lgbm.add_argument("--horizon", type=int, default=5, help="레이블 horizon 분 (5 or 10)")
    p_crypto_train_lgbm.add_argument(
        "--cost-pct",
        type=float,
        default=0.2,
        help="수수료+슬리피지 허들 (%% 포인트, 기본 0.2)",
    )
    p_crypto_train_lgbm.add_argument("--symbols", type=str, default="", metavar="SYM,...")
    p_crypto_train_lgbm.add_argument("--model-dir", type=str, default="outputs/models")
    p_crypto_train_lgbm.add_argument("--splits", type=int, default=5)
    p_crypto_train_lgbm.add_argument("--threshold", type=float, default=0.55)
    p_crypto_train_lgbm.add_argument("--min-samples", type=int, default=200)
    p_crypto_train_lgbm.add_argument(
        "--max-bars-per-symbol",
        type=int,
        default=0,
        help="종목당 최근 N봉만 사용 (0=전체). O(n^2) 빌드 비용 상한·최근성 확보",
    )

    p_crypto_predict_lgbm = sub.add_parser(
        "crypto-predict-lgbm",
        help="[ML] 학습된 LightGBM으로 P(익절) 추론",
    )
    p_crypto_predict_lgbm.add_argument("--horizon", type=int, default=5)
    p_crypto_predict_lgbm.add_argument("--model-dir", type=str, default="outputs/models")
    p_crypto_predict_lgbm.add_argument("--model", type=str, default="")
    p_crypto_predict_lgbm.add_argument(
        "--output-dir",
        type=str,
        default="outputs/binance_stream",
    )
    p_crypto_predict_lgbm.add_argument("--live-state", type=str, default="")
    p_crypto_predict_lgbm.add_argument("--threshold", type=float, default=0.55)
    p_crypto_predict_lgbm.add_argument("--top", type=int, default=15)

    p_crypto_retrain = sub.add_parser(
        "crypto-retrain-lgbm",
        help="[ML] LightGBM 야간 재학습 — 검증 AUC 개선 시에만 배포",
    )
    p_crypto_retrain.add_argument("--output-dir", type=str, default="outputs")
    p_crypto_retrain.add_argument("--bars-dir", type=str, default="")
    p_crypto_retrain.add_argument("--horizon", type=int, default=5)
    p_crypto_retrain.add_argument("--cost-pct", type=float, default=0.2)
    p_crypto_retrain.add_argument("--min-samples", type=int, default=200)
    p_crypto_retrain.add_argument("--dry-run", action="store_true")
    p_crypto_retrain.add_argument(
        "--also-seq",
        action="store_true",
        help="LSTM/Transformer 시퀀스 모델도 재학습 (bars 30일)",
    )
    p_crypto_retrain.add_argument(
        "--full-retrain",
        action="store_true",
        help="warm-start 비활성 — 전체 재학습",
    )
    p_crypto_retrain.add_argument("--trade-lookback-days", type=int, default=14)
    p_crypto_retrain.add_argument("--min-trades-deploy", type=int, default=30)
    p_crypto_retrain.add_argument("--min-val-auc", type=float, default=0.52)
    p_crypto_retrain.add_argument("--seq-model", type=str, default="lstm", choices=["lstm", "transformer"])
    p_crypto_retrain.add_argument("--no-telegram", action="store_true", help="실패 시 Telegram 알림 생략")

    p_crypto_retrain_history = sub.add_parser(
        "crypto-retrain-history",
        help="[ML] outputs/retrain_history.jsonl 최근 재학습 결과 테이블",
    )
    p_crypto_retrain_history.add_argument("--output-dir", type=str, default="outputs")
    p_crypto_retrain_history.add_argument("--days", type=int, default=30)

    p_crypto_train_seq = sub.add_parser(
        "crypto-train-seq",
        help="[ML] LSTM/Transformer 시퀀스 P(win) 학습",
    )
    p_crypto_train_seq.add_argument("--model", type=str, default="lstm", choices=["lstm", "transformer"])
    p_crypto_train_seq.add_argument("--bars-dir", type=str, default="outputs/binance_stream/bars")
    p_crypto_train_seq.add_argument("--horizon", type=int, default=5)
    p_crypto_train_seq.add_argument("--seq-len", type=int, default=30)
    p_crypto_train_seq.add_argument("--cost-pct", type=float, default=0.2)
    p_crypto_train_seq.add_argument("--min-samples", type=int, default=300)
    p_crypto_train_seq.add_argument("--model-dir", type=str, default="outputs/models")

    p_install_retrain_ld = sub.add_parser(
        "install-crypto-retrain-launchd",
        help="[ML] 매일 crypto-retrain-lgbm launchd (기본 03:10)",
    )
    p_install_retrain_ld.add_argument("--project-dir", type=str, default=None)
    p_install_retrain_ld.add_argument("--output-dir", type=str, default="outputs")
    p_install_retrain_ld.add_argument("--horizon", type=int, default=5)
    p_install_retrain_ld.add_argument("--hour", type=int, default=3)
    p_install_retrain_ld.add_argument("--minute", type=int, default=10)

    sub.add_parser("uninstall-crypto-retrain-launchd", help="[ML] retrain launchd 제거")

    p_install_binance_ld = sub.add_parser(
        "install-binance-stream-launchd",
        help="[데이터] binance-stream macOS LaunchAgent 상시 수집",
    )
    p_install_binance_ld.add_argument("--project-dir", type=str, default=None)
    p_install_binance_ld.add_argument("--top", type=int, default=30)
    p_install_binance_ld.add_argument("--output-dir", type=str, default="outputs/binance_stream")
    p_install_binance_ld.add_argument("--depth-levels", type=int, default=20)
    p_install_binance_ld.add_argument("--no-load", action="store_true")
    p_install_binance_ld.add_argument("--no-sanitize-path", action="store_true")

    p_uninstall_binance_ld = sub.add_parser(
        "uninstall-binance-stream-launchd",
        help="[데이터] binance-stream LaunchAgent 제거",
    )
    p_uninstall_binance_ld.add_argument("--keep-plist", action="store_true")
    p_uninstall_binance_ld.add_argument("--keep-loaded", action="store_true")

    sub.add_parser(
        "binance-stream-launchd-status",
        help="[데이터] binance-stream launchd 상태",
    )

    # ── KIS stream launchd ──────────────────────────────────────────────
    p_install_kis_ld = sub.add_parser(
        "install-kis-stream-launchd",
        help="[K-GSQS] kis-stream macOS LaunchAgent 자동 실행 설치",
    )
    p_install_kis_ld.add_argument("--project-dir", type=str, default=None)
    p_install_kis_ld.add_argument("--paper", action="store_true", help="모의투자 WS 사용")
    p_install_kis_ld.add_argument("--universe-size", type=int, default=30)
    p_install_kis_ld.add_argument("--no-load", action="store_true")
    p_install_kis_ld.add_argument("--no-sanitize-path", action="store_true")

    p_uninstall_kis_ld = sub.add_parser(
        "uninstall-kis-stream-launchd",
        help="[K-GSQS] kis-stream LaunchAgent 제거",
    )
    p_uninstall_kis_ld.add_argument("--keep-plist", action="store_true")
    p_uninstall_kis_ld.add_argument("--keep-loaded", action="store_true")

    sub.add_parser(
        "kis-stream-launchd-status",
        help="[K-GSQS] kis-stream launchd 상태 확인",
    )

    # ── KIS overseas stream launchd ────────────────────────────────────────
    p_install_overseas_ld = sub.add_parser(
        "install-overseas-stream-launchd",
        help="[K-GSQS] overseas-stream macOS LaunchAgent 자동 실행 설치",
    )
    p_install_overseas_ld.add_argument("--project-dir", type=str, default=None)
    p_install_overseas_ld.add_argument("--paper", action="store_true", help="모의투자 WS 사용")
    p_install_overseas_ld.add_argument("--no-load", action="store_true")
    p_install_overseas_ld.add_argument("--no-sanitize-path", action="store_true")

    p_uninstall_overseas_ld = sub.add_parser(
        "uninstall-overseas-stream-launchd",
        help="[K-GSQS] overseas-stream LaunchAgent 제거",
    )
    p_uninstall_overseas_ld.add_argument("--keep-plist", action="store_true")
    p_uninstall_overseas_ld.add_argument("--keep-loaded", action="store_true")

    p_overseas_auto = sub.add_parser(
        "overseas-auto-runner",
        help="[해외주식] 미국장 무인 자동매매 러너 (분석→plan→매수→TP/SL매도)",
    )
    p_overseas_auto.add_argument("--output-dir", type=str, default="outputs")
    p_overseas_auto.add_argument("--once", action="store_true", help="1회만 실행 후 종료 (검증용)")
    p_overseas_auto.add_argument("--force", action="store_true", help="미국 장외에도 실행 (장시간 게이트 우회, 실주문 게이트는 유지)")

    sub.add_parser(
        "overseas-stream-launchd-status",
        help="[K-GSQS] overseas-stream launchd 상태 확인",
    )

    p_overseas = sub.add_parser(
        "overseas-stream",
        help="[K-GSQS] KIS 해외주식 실시간 수집 + OHLCV 봉 생성",
    )
    p_overseas.add_argument("--paper", action="store_true", help="모의투자 WS 강제 (포트 31000)")
    p_overseas.add_argument("--live", dest="live_ws", action="store_true", help="실전 WS 강제 (포트 21000)")

    p_kis_stream = sub.add_parser(
        "kis-stream",
        help="[K-GSQS] KIS WebSocket — 국내주식 체결·호가 실시간 수집 + OHLCV 봉 생성",
    )
    p_kis_stream.add_argument(
        "--symbols",
        type=str,
        default="",
        metavar="CODE,...",
        help="6자리 종목코드 쉼표 구분 (예: 005930,000660). 비우면 기본 10종목",
    )
    p_kis_stream.add_argument(
        "--output-dir",
        type=str,
        default="",
        metavar="DIR",
        help="봉/틱 JSONL 저장 디렉토리 (기본: output/kis_stream)",
    )
    p_kis_stream.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="실행 초 (0=장 마감 또는 Ctrl+C까지 무한)",
    )
    p_kis_stream.add_argument(
        "--paper",
        action="store_true",
        help="모의투자 WS 강제 (포트 31000)",
    )
    p_kis_stream.add_argument(
        "--live-ws",
        action="store_true",
        dest="live_ws",
        help="실전 WS 강제 (포트 21000, KIS_ENV=live 필요)",
    )

    p_kc = sub.add_parser(
        "kis-check",
        help="[실전-3] KIS 환경 변수 검증. 기본은 HTTP 없음; --network 시 OAuth 토큰만 테스트",
    )
    p_kc.add_argument(
        "--network",
        action="store_true",
        help="실제 OAuth 토큰 발급(HTTPS). 주문 API는 호출하지 않음",
    )
    p_los = sub.add_parser(
        "live-order-status",
        help="[실전-5] 감사 로그에서 주문번호 추출·선택 KIS 일별체결 조회",
    )
    p_los.add_argument(
        "--audit",
        type=str,
        required=True,
        metavar="PATH",
        help="live_approval_audit_*.json 경로",
    )
    p_los.add_argument("--order-id", type=str, default=None, metavar="ODNO", help="감사 로그 외 주문번호 직접 지정")
    p_los.add_argument("--symbol", type=str, default=None, metavar="PDNO", help="6자리 종목(조회 필터)")
    p_los.add_argument("--start-date", type=str, default=None, metavar="YYYYMMDD", help="조회 시작일")
    p_los.add_argument("--end-date", type=str, default=None, metavar="YYYYMMDD", help="조회 종료일")
    p_los.add_argument(
        "--network",
        action="store_true",
        help="KIS inquire-daily-ccld 실호출(기본은 감사 파싱·리포트만)",
    )
    p_los.add_argument(
        "--output-dir",
        type=str,
        default="outputs",
        metavar="DIR",
        help="리포트 저장 디렉터리 (기본 %(default)s)",
    )
    p_lsa = sub.add_parser(
        "live-sync-account",
        help="[실전-5] KIS 잔고·포지션 스냅샷 JSON/Markdown (--network 필수)",
    )
    p_lsa.add_argument("--broker", type=str, choices=["kis"], default="kis", help="현재 kis만 지원")
    p_lsa.add_argument(
        "--network",
        action="store_true",
        help="inquire-balance 실호출 필수 플래그",
    )
    p_lsa.add_argument(
        "--output-dir",
        type=str,
        default="outputs",
        metavar="DIR",
        help="스냅샷 저장 디렉터리 (기본 %(default)s)",
    )
    p_lsa.add_argument(
        "--save-db",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="real_positions / real_account_snapshots 에 저장 (기본 true). --no-save-db 는 파일만",
    )
    p_lsa.add_argument(
        "--debug-raw",
        action="store_true",
        help="KIS inquire-balance raw 구조(키 목록·행 수만)를 마스킹 출력/저장",
    )
    p_rla = sub.add_parser(
        "reconcile-live-account",
        help="[실전-6] KIS 잔고 조회 vs DB real_positions 비교 (--network 필수)",
    )
    p_rla.add_argument("--broker", type=str, choices=["kis"], default="kis", help="현재 kis만 지원")
    p_rla.add_argument(
        "--network",
        action="store_true",
        help="브로커 inquire-balance 실호출 필수",
    )
    p_rla.add_argument(
        "--output-dir",
        type=str,
        default="outputs",
        metavar="DIR",
        help="리포트 저장 디렉터리 (기본 %(default)s)",
    )
    p_rla.add_argument(
        "--debug-raw",
        action="store_true",
        help="KIS inquire-balance raw 구조(키 목록·행 수만)를 마스킹 출력/저장",
    )
    p_log = sub.add_parser(
        "live-order-guard-check",
        help="[실전-7] 중복·대기·reconcile·스냅샷 주문 위험 조회 (HTTP 없음)",
    )
    p_log.add_argument("--symbol", type=str, required=True, metavar="SYM", help="검사할 종목코드")
    p_log.add_argument("--broker", type=str, choices=["kis"], default="kis", help="현재 kis만 지원")
    p_log.add_argument("--quantity", type=int, default=1, help="가정 주문 수량 (기본 %(default)s)")
    p_log.add_argument("--limit-price", type=float, default=None, metavar="PRICE", help="가정 지정가")
    p_log.add_argument(
        "--stale-minutes",
        type=int,
        default=10,
        metavar="N",
        help="스냅샷 stale 임계(분, 기본 %(default)s)",
    )
    p_log.add_argument(
        "--output-dir",
        type=str,
        default="outputs",
        metavar="DIR",
        help="reconcile state 읽기 경로 (기본 %(default)s)",
    )
    p_lfs = sub.add_parser(
        "live-fill-summary",
        help="[실전-8] real_fill_history 집계·partial fill 요약 (HTTP 없음)",
    )
    p_lfs.add_argument("--order-id", type=str, default=None, metavar="ODNO", help="KIS 주문번호")
    p_lfs.add_argument(
        "--audit",
        type=str,
        default=None,
        metavar="PATH",
        help="live_approval_audit JSON (주문번호 추출)",
    )
    p_lfs.add_argument("--symbol", type=str, default=None, metavar="SYM", help="종목 필터(선택)")
    p_lfs.add_argument(
        "--output-dir",
        type=str,
        default="outputs",
        metavar="DIR",
        help="리포트 저장 (기본 %(default)s)",
    )
    p_tsc = sub.add_parser(
        "trading-session-check",
        help="[실전-9] 국내 정규장 주문 가능 시간 조회 (HTTP 없음)",
    )
    p_tsc.add_argument("--market", type=str, default=None, metavar="MKT", help="시장 코드 (기본 KR)")
    p_tsc.add_argument(
        "--now",
        type=str,
        default=None,
        metavar="ISO",
        help='기준 시각 ISO (예: 2026-05-15T10:00:00+09:00)',
    )
    p_tsc.add_argument(
        "--holiday",
        action="append",
        default=None,
        metavar="YYYY-MM-DD",
        help="추가 휴장일 (반복 가능, env DEEPSIGNAL_MARKET_HOLIDAYS 와 합산)",
    )
    p_tsc.add_argument(
        "--allow-after-hours",
        action="store_true",
        help="정규장 시간 외도 OPEN 판정 (주말·휴일은 여전히 CLOSED)",
    )
    p_ptr = sub.add_parser(
        "pre-trade-runbook",
        help="[실전-10] 실주문 전 운영 runbook (session·sync·reconcile·guard·plan)",
    )
    p_ptr.add_argument("--broker", type=str, choices=["kis"], default="kis")
    p_ptr.add_argument("--network", action="store_true", help="KIS HTTP 필수 (sync·reconcile)")
    p_ptr.add_argument("--plan", type=str, required=True, metavar="PATH", help="live_order_plan JSON")
    p_ptr.add_argument("--symbol", type=str, required=True, metavar="SYM", help="검사·가드 대상 종목")
    p_ptr.add_argument("--quantity", type=int, default=1)
    p_ptr.add_argument("--limit-price", type=float, default=None, metavar="PRICE")
    p_ptr.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_ptr.add_argument("--stale-minutes", type=int, default=10, metavar="N")
    p_ptr.add_argument(
        "--allow-symbol",
        action="append",
        default=None,
        metavar="SYM",
        help="plan 화이트리스트 (반복 가능)",
    )
    p_ptr.add_argument("--max-single-order-value", type=float, default=100_000.0)
    p_ptr.add_argument("--max-total-order-value", type=float, default=200_000.0)
    p_ptr.add_argument("--now", type=str, default=None, metavar="ISO", help="세션 검사 기준 시각")
    p_ptr.add_argument("--market", type=str, default=None, metavar="MKT")
    p_ptr.add_argument("--holiday", action="append", default=None, metavar="YYYY-MM-DD")
    p_ptr.add_argument("--allow-after-hours", action="store_true")
    p_ptr.add_argument(
        "--save-db",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="sync 시 real_* DB 저장 (기본 true)",
    )
    p_post = sub.add_parser(
        "post-trade-runbook",
        help="[실전-10] 실주문 후 운영 runbook (status·fill·sync·reconcile)",
    )
    p_post.add_argument("--broker", type=str, choices=["kis"], default="kis")
    p_post.add_argument("--network", action="store_true", help="KIS HTTP 필수")
    p_post.add_argument("--audit", type=str, default=None, metavar="PATH", help="live_approval_audit JSON")
    p_post.add_argument("--order-id", type=str, default=None, metavar="ODNO")
    p_post.add_argument("--symbol", type=str, default=None, metavar="SYM")
    p_post.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_post.add_argument(
        "--with-summary",
        action="store_true",
        help="post-trade 후 ops-dashboard, sell-plan, daily-ops-summary, html-dashboard까지 생성",
    )
    p_post.add_argument(
        "--full-report",
        action="store_true",
        help="--with-summary 별칭: 사후 운영 리포트 체인 전체 생성",
    )
    p_post.add_argument(
        "--save-db",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="sync 시 real_* DB 저장 (기본 true)",
    )
    p_post.add_argument(
        "--stop-loss-pct",
        type=float,
        default=-0.07,
        metavar="PCT",
        help="post-trade risk-check 손절 임계 수익률 (비율, 기본 -0.07 = -7%%)",
    )
    p_post.add_argument(
        "--take-profit-pct",
        type=float,
        default=0.15,
        metavar="PCT",
        help="post-trade risk-check 익절 임계 (기본 0.15 = 15%%)",
    )
    p_post.add_argument(
        "--warn-loss-pct",
        type=float,
        default=-0.03,
        metavar="PCT",
        help="post-trade risk-check 손실 경고 (기본 -0.03)",
    )
    p_post.add_argument(
        "--warn-profit-pct",
        type=float,
        default=0.10,
        metavar="PCT",
        help="post-trade risk-check 이익 경고 (기본 0.10)",
    )
    p_rc = sub.add_parser(
        "risk-check",
        help="[실전-12] real_positions 손절/익절 경고 (리포트만, SELL·자동매도 없음)",
    )
    p_rc.add_argument("--broker", type=str, choices=["kis"], default="kis")
    p_rc.add_argument(
        "--stop-loss-pct",
        type=float,
        default=-0.07,
        metavar="PCT",
        help="손절 임계 수익률 (비율, 기본 -0.07 = -7%%)",
    )
    p_rc.add_argument(
        "--take-profit-pct",
        type=float,
        default=0.15,
        metavar="PCT",
        help="익절 임계 (기본 0.15 = 15%%)",
    )
    p_rc.add_argument(
        "--warn-loss-pct",
        type=float,
        default=-0.03,
        metavar="PCT",
        help="손실 경고 (기본 -0.03)",
    )
    p_rc.add_argument(
        "--warn-profit-pct",
        type=float,
        default=0.10,
        metavar="PCT",
        help="이익 경고 (기본 0.10)",
    )
    p_rc.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_rc.add_argument(
        "--sync-first",
        action="store_true",
        help="(미구현) live-sync-account 후 검사. 현재는 DB 최신 스냅샷만 사용",
    )
    p_ops = sub.add_parser(
        "ops-dashboard",
        help="[실전-14] 로컬 DB/outputs 운영 상태 요약 리포트 (조회 전용)",
    )
    p_ops.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_ops.add_argument(
        "--recent-orders",
        type=int,
        default=10,
        metavar="N",
        help="표시할 최근 real_order_history 건수 (기본 %(default)s)",
    )
    p_sp = sub.add_parser(
        "sell-plan",
        help="[실전-15] 운영자 검토용 수동 SELL 계획서 생성 (주문 실행 없음)",
    )
    p_sp.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_sp.add_argument(
        "--stop-loss-pct",
        type=float,
        default=-0.07,
        metavar="PCT",
        help="EXIT 제안 임계 수익률 (비율, 기본 -0.07 = -7%%)",
    )
    p_sp.add_argument(
        "--take-profit-pct",
        type=float,
        default=0.15,
        metavar="PCT",
        help="REDUCE 제안 임계 수익률 (기본 0.15 = 15%%)",
    )
    p_sp.add_argument(
        "--warn-loss-pct",
        type=float,
        default=-0.03,
        metavar="PCT",
        help="REVIEW 제안 손실 경고 임계 (기본 -0.03)",
    )
    p_na = sub.add_parser(
        "notify-alerts",
        help="[실전-16] Telegram/Discord alert-only 알림 (--send 없으면 dry-run)",
    )
    p_na.add_argument("--channel", type=str, choices=["telegram", "discord"], default=None)
    p_na.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="네트워크 호출 없이 audit만 생성 (기본)",
    )
    p_na.add_argument(
        "--send",
        action="store_true",
        help="실제 Telegram/Discord 전송. 주문 실행은 하지 않음",
    )
    p_na.add_argument("--include-ok", action="store_true", help="OK/정상 상태도 INFO로 포함")
    p_na.add_argument(
        "--include-maintenance",
        action="store_true",
        help="weekly_maintenance/report_health 최신 리포트도 알림 source에 포함",
    )
    p_na.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_dos = sub.add_parser(
        "daily-ops-summary",
        help="[실전-17] 오늘 운영 상태 통합 JSON/Markdown 요약 (조회 전용)",
    )
    p_dos.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_dos.add_argument("--date", type=str, default=None, metavar="YYYY-MM-DD")
    p_dos.add_argument(
        "--include-latest-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="오늘 파일이 없으면 최신 파일로 fallback (기본 true)",
    )
    p_dos.add_argument(
        "--notify-dry-run",
        action="store_true",
        help="요약 전 notify-alerts dry-run audit을 생성해 포함",
    )
    p_html = sub.add_parser(
        "html-dashboard",
        help="[실전-18] outputs 기반 정적 HTML 운영 대시보드 생성",
    )
    p_html.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_html.add_argument("--open", action="store_true", help="생성 후 기본 브라우저로 HTML 열기")
    p_clean = sub.add_parser(
        "cleanup-reports",
        help="[실전-20] outputs 리포트 보존/정리 (기본 dry-run)",
    )
    p_clean.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_clean.add_argument("--keep-days", type=int, default=14, metavar="N")
    p_clean.add_argument("--keep-latest", type=int, default=20, metavar="N")
    p_clean.add_argument("--archive", action="store_true", help="삭제 대신 archive-dir로 이동")
    p_clean.add_argument("--archive-dir", type=str, default=None, metavar="DIR")
    p_clean.add_argument("--remove-appledouble", action="store_true", help="._* AppleDouble 메타파일도 정리")
    p_clean.add_argument("--dry-run", action="store_true", default=True, help="파일 변경 없이 후보만 audit (기본)")
    p_clean.add_argument("--apply", action="store_true", help="실제 삭제/이동 적용")
    p_index = sub.add_parser(
        "report-index",
        help="[실전-21] outputs/archive 리포트 정적 HTML/Markdown 인덱스 생성",
    )
    p_index.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_index.add_argument("--archive-dir", type=str, default=None, metavar="DIR")
    p_index.add_argument("--max-items", type=int, default=200, metavar="N")
    p_odr = sub.add_parser(
        "ops-dry-run",
        help="[실전-22] 실주문 없는 하루 운영 점검 dry-run",
    )
    p_odr.add_argument("--network", action="store_true", help="KIS OAuth/잔고조회/reconcile 조회 포함")
    p_odr.add_argument("--broker", type=str, choices=["kis"], default="kis")
    p_odr.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_odr.add_argument("--archive-dir", type=str, default=None, metavar="DIR")
    p_odr.add_argument("--recent-orders", type=int, default=10, metavar="N")
    p_view = sub.add_parser(
        "open-dashboard",
        help="[실전-23] 로컬 운영 HTML/Markdown 리포트 경로 안내 및 선택 열기",
    )
    p_view.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_view.add_argument("--open", action="store_true", help="OPS_DASHBOARD.html만 기본 브라우저로 열기")
    p_view.add_argument("--open-index", action="store_true", help="REPORT_INDEX.html만 기본 브라우저로 열기")
    p_view.add_argument("--open-archive", action="store_true", help="ARCHIVE_VIEWER.html만 기본 브라우저로 열기")
    p_view.add_argument("--open-all", action="store_true", help="존재하는 HTML 리포트만 모두 열기")
    p_health = sub.add_parser(
        "report-health-check",
        help="[실전-25] outputs 리포트/DB/token 상태 진단 (수정·삭제·네트워크 없음)",
    )
    p_health.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_health.add_argument("--db-path", type=str, default=None, metavar="PATH")
    p_health.add_argument("--max-age-hours", type=float, default=24.0, metavar="HOURS")
    p_health.add_argument("--max-output-files", type=int, default=500, metavar="N")
    p_weekly = sub.add_parser(
        "weekly-maintenance",
        help="[실전-26] 주간 운영 점검 dry-run (삭제·archive 이동·네트워크 없음)",
    )
    p_weekly.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_weekly.add_argument("--archive-dir", type=str, default="outputs/archive", metavar="DIR")
    p_weekly.add_argument("--db-path", type=str, default=None, metavar="PATH")
    p_weekly.add_argument("--keep-days", type=int, default=14, metavar="N")
    p_weekly.add_argument("--keep-latest", type=int, default=20, metavar="N")
    p_weekly.add_argument("--max-age-hours", type=float, default=24.0, metavar="HOURS")
    p_weekly.add_argument("--max-output-files", type=int, default=500, metavar="N")
    p_weekly.add_argument(
        "--tune-threshold-from-outcomes",
        action="store_true",
        help="[학습루프-02] recommendation_outcomes 기반 min_final_score 재계산 및 summary 갱신",
    )
    p_weekly.add_argument("--outcomes-db", type=str, default=None, metavar="PATH")
    p_weekly.add_argument("--tune-lookback-days", type=int, default=60, metavar="N")
    p_weekly.add_argument("--tune-min-samples", type=int, default=10, metavar="N")
    p_weekly.add_argument("--tune-blend-with-validation", type=float, default=0.5, metavar="FRAC")
    p_tune_thr = sub.add_parser(
        "tune-threshold-from-outcomes",
        help="[학습루프-02] live 추천 outcomes로 min_final_score 재계산 (AI_VALIDATION_THRESHOLD_SUMMARY 갱신)",
    )
    p_tune_thr.add_argument("--outcomes-db", type=str, default="outputs/recommendation_outcomes.db", metavar="PATH")
    p_tune_thr.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_tune_thr.add_argument("--lookback-days", type=int, default=60, metavar="N")
    p_tune_thr.add_argument("--min-samples", type=int, default=10, metavar="N")
    p_tune_thr.add_argument("--target-win-rate", type=float, default=0.45, metavar="FRAC")
    p_tune_thr.add_argument("--min-avg-return", type=float, default=0.0, metavar="PCT")
    p_tune_thr.add_argument("--blend-with-validation", type=float, default=0.5, metavar="FRAC")
    p_bundle = sub.add_parser(
        "weekly-report-bundle",
        help="[실전-28] 주간 운영 리포트 번들 폴더 생성 (복사/인덱스 전용)",
    )
    p_bundle.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_bundle.add_argument("--bundle-dir", type=str, default=None, metavar="DIR")
    p_bundle.add_argument("--db-path", type=str, default=None, metavar="PATH")
    p_bundle.add_argument("--zip", action="store_true", help="번들 ZIP 생성 (기본 off)")
    p_bundle.add_argument("--open", action="store_true", help="생성 후 BUNDLE_INDEX.html 열기")
    p_archive_viewer = sub.add_parser(
        "archive-viewer",
        help="[실전-32] outputs/archive 로컬 리포트 viewer 생성 (읽기 전용)",
    )
    p_archive_viewer.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_archive_viewer.add_argument("--archive-dir", type=str, default="outputs/archive", metavar="DIR")
    p_archive_viewer.add_argument("--limit", type=int, default=200, metavar="N")
    p_archive_viewer.add_argument("--trend-days", type=int, default=7, metavar="N")
    p_archive_viewer.add_argument("--no-csv", action="store_true", help="ARCHIVE_VIEWER.csv 생성 생략")
    p_archive_viewer.add_argument("--no-summary-md", action="store_true", help="ARCHIVE_VIEWER_SUMMARY.md 생성 생략")
    p_ai_rec = sub.add_parser(
        "ai-live-recommend",
        help="AI 실계좌 추천 리포트와 PENDING_APPROVAL 주문안 생성 (실주문 없음)",
    )
    p_ai_rec.add_argument("--broker", type=str, choices=["kis"], default="kis", metavar="NAME")
    p_ai_rec.add_argument("--network", action="store_true", help="KIS 잔고/포지션 조회만 수행하고 주문 API는 호출하지 않음")
    p_ai_rec.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_ai_rec.add_argument("--symbols", type=str, default="", metavar="CSV")
    p_ai_rec.add_argument("--max-recommendations", type=int, default=10, metavar="N")
    p_ai_rec.add_argument("--capital-limit", type=float, default=None, metavar="AMT")
    p_ai_rec.add_argument(
        "--max-order-value",
        type=float,
        default=None,
        metavar="AMT",
        help="AI 주문안 최대 금액(미지정 시 capital-limit 사용)",
    )
    p_ai_rec.add_argument("--allow-sell-candidates", action="store_true", help="SELL/REDUCE 후보를 리포트에 명시적으로 허용(주문안은 BUY 호환 유지)")
    p_ai_val = sub.add_parser(
        "validate-ai-recommendation",
        help="AI 추천 정책을 로컬 DB 기반 in-memory portfolio로 검증 (실계좌/paper_* 수정 없음)",
    )
    p_ai_val.add_argument("--symbols", type=str, default="", metavar="CSV")
    p_ai_val.add_argument("--start-date", type=str, default=None, metavar="YYYY-MM-DD")
    p_ai_val.add_argument("--end-date", type=str, default=None, metavar="YYYY-MM-DD")
    p_ai_val.add_argument("--initial-cash", type=float, default=1_000_000.0, metavar="AMT")
    p_ai_val.add_argument("--include-sell-reduce", action="store_true", help="SELL/REDUCE 추천도 가상 포트폴리오에 반영")
    p_ai_val.add_argument("--benchmark", action=argparse.BooleanOptionalAction, default=True, help="동일비중 buy-and-hold benchmark 계산")
    p_ai_val.add_argument("--risk-free-rate", type=float, default=0.0, metavar="FRAC")
    p_ai_val.add_argument("--commission-rate", type=float, default=0.001, metavar="FRAC")
    p_ai_val.add_argument("--tax-rate", type=float, default=0.0, metavar="FRAC")
    p_ai_val.add_argument("--slippage-bps", type=float, default=5.0, metavar="BPS")
    p_ai_val.add_argument("--min-order-value", type=float, default=10_000.0, metavar="AMT")
    p_ai_val.add_argument("--max-order-value", type=float, default=None, metavar="AMT")
    p_ai_val.add_argument("--currency", type=str, default="KRW", metavar="CCY")
    p_ai_val.add_argument("--no-costs", action="store_true", help="수수료/세금/슬리피지/최소주문금액 비용 모델 비활성화")
    p_ai_val.add_argument("--sector-map", type=str, default=None, metavar="PATH")
    p_ai_val.add_argument("--max-symbol-weight", type=float, default=0.35, metavar="FRAC")
    p_ai_val.add_argument("--max-sector-weight", type=float, default=0.50, metavar="FRAC")
    p_ai_val.add_argument("--correlation-threshold", type=float, default=0.80, metavar="FRAC")
    p_ai_val.add_argument("--correlation-lookback-days", type=int, default=60, metavar="DAYS")
    p_ai_val.add_argument("--min-correlation-points", type=int, default=20, metavar="N")
    p_ai_val.add_argument("--liquidity-limit-pct", type=float, default=None, metavar="FRAC")
    p_ai_val.add_argument("--min-daily-volume", type=float, default=None, metavar="VOL")
    p_ai_val.add_argument("--min-daily-value", type=float, default=None, metavar="AMT")
    p_ai_val.add_argument("--volume-lookback-days", type=int, default=20, metavar="DAYS")
    p_ai_val.add_argument("--base-currency", type=str, default="KRW", metavar="CCY")
    p_ai_val.add_argument("--default-symbol-currency", type=str, default="KRW", metavar="CCY")
    p_ai_val.add_argument("--fx-rates", type=str, default=None, metavar="PATH")
    p_ai_val.add_argument("--symbol-currency-map", type=str, default=None, metavar="PATH")
    p_ai_val.add_argument("--fallback-fx", type=str, default="", metavar="CSV")
    p_ai_val.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_checklists = sub.add_parser(
        "generate-checklists",
        help="[실전-29] daily/weekly 수동 운영 체크리스트 Markdown 생성 (스케줄러 아님)",
    )
    p_checklists.add_argument("--output-dir", type=str, default="outputs/checklists", metavar="DIR")
    p_safety = sub.add_parser(
        "safety-audit",
        help="[실전-30] 로컬 읽기 전용 안전 감사 리포트 생성 (네트워크·주문·cleanup 없음)",
    )
    p_safety.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    p_safety.add_argument("--db-path", type=str, default=None, metavar="PATH")
    p_safety.add_argument("--strict", action="store_true", help="WARNING도 BLOCKED 종료 코드로 승격")
    p_safety.add_argument(
        "--freshness-date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Daily AI freshness 기준 날짜 (기본: Asia/Seoul 오늘)",
    )
    p_context = sub.add_parser(
        "init-context",
        help="표준 AI_CONTEXT Markdown 구조 생성 (기존 파일 overwrite 없음)",
    )
    p_context.add_argument("--project", type=str, default=".", metavar="PATH", help="대상 프로젝트 루트 또는 --all-projects 기준 폴더")
    p_context.add_argument("--all-projects", action="store_true", help="기준 폴더의 하위 프로젝트들을 일괄 초기화")
    sub.add_parser("show-signals", help="signals 최신 20건 콘솔 표 (technical/news/final 등)")
    sub.add_parser("show-backtests", help="backtest_results 최신 20건 콘솔 표")
    sub.add_parser("show-paper", help="모의 계좌 스냅샷·포지션·체결 최신 요약")
    p_halt = sub.add_parser("trading-halt", help="[킬스위치] 전역 거래 중단 (모든 러너 신규매수 차단)")
    p_halt.add_argument("--reason", type=str, default="manual halt")
    p_halt.add_argument("--output-dir", type=str, default="outputs")
    p_resume = sub.add_parser("trading-resume", help="[킬스위치] 거래 중단 해제")
    p_resume.add_argument("--output-dir", type=str, default="outputs")
    p_halt_st = sub.add_parser("trading-halt-status", help="[킬스위치] halt 상태·당일 실현손익 확인")
    p_halt_st.add_argument("--output-dir", type=str, default="outputs")
    p_lev = sub.add_parser("leverage-trend", help="[레버리지] 나스닥 2x ETF 추세 — 상태/실행/러너 (다중 게이트)")
    p_lev.add_argument("lev_mode", nargs="?", default="status", choices=["status", "run", "runner"])
    p_lev.add_argument("--output-dir", type=str, default="outputs")
    p_lev.add_argument("--execute", action="store_true", help="실주문(모든 게이트 충족 시에만 POST)")
    p_lev.add_argument("--interval-minutes", type=float, default=30.0)
    p_intra = sub.add_parser("intraday-runner", help="[고속회전] 장중 보유 트레일링스톱 점검 루프 (기본 dry-run)")
    p_intra.add_argument("--output-dir", type=str, default="outputs")
    p_intra.add_argument("--market", type=str, default="kr", choices=["kr", "us"])
    p_intra.add_argument("--once", action="store_true", help="1틱만 실행 후 종료(검증용)")
    p_intra.add_argument("--execute", action="store_true", help="실주문 모드(미연결, 운영검증 후)")
    p_mor = sub.add_parser("market-open-report", help="[보고] 장 시작 — 코인+국내+해외 오늘자 통합 매매요약 텔레그램 발송")
    p_mor.add_argument("--label", type=str, default="", help="제목 라벨 (예: 국내장 시작 / 해외장 시작)")
    p_agr = sub.add_parser("aggression-report", help="[보고] 공격성 단계별·추격거래별 성과 집계 (단계 재책정용)")
    p_agr.add_argument("--output-dir", type=str, default="outputs")
    p_agr.add_argument("--telegram", action="store_true", help="결과를 텔레그램으로도 발송")
    p_krs = sub.add_parser("kr-scan", help="[국내-스캔] 전 시장 급등주 스캔(KIS 순위 API) → 신호 기록")
    p_krs.add_argument("--force", action="store_true", help="KR_SCANNER_ENABLED 무시하고 강제 실행")
    p_nws = sub.add_parser("crypto-news-refresh", help="[코인-LLM] 뉴스 감성/악재를 LLM 분석해 캐시 갱신 (스코어·게이트가 읽음)")
    p_nws.add_argument("--output-dir", type=str, default="outputs")
    p_nws.add_argument("--markets", type=str, default="", help="쉼표구분 마켓 고정 (예: KRW-BTC,KRW-XRP). 미지정시 유니버스 상위")
    p_nws.add_argument("--max-markets", type=int, default=40)
    p_rt = sub.add_parser("regime-trend-status", help="[추세추종] S&P500 200일선 신호·권고 행동 (유일 robust 엣지)")
    p_rt.add_argument("--output-dir", type=str, default="outputs")
    p_rt.add_argument("--json", action="store_true", help="JSON 상세 출력")
    p_rtr = sub.add_parser("regime-trend-run", help="[추세추종] 신호에 따라 ETF 진입/청산 (기본 dry-run)")
    p_rtr.add_argument("--output-dir", type=str, default="outputs")
    p_rtr.add_argument("--execute", action="store_true", help="실제 주문 (LIVE+게이트 충족 시에만 POST)")
    p_rtrn = sub.add_parser("regime-trend-runner", help="[추세추종] 상시 러너 — 배포+장시간이면 자동 매매")
    p_rtrn.add_argument("--output-dir", type=str, default="outputs")
    p_rtrn.add_argument("--interval-minutes", type=float, default=5.0)
    p_rtrn.add_argument("--execute", action="store_true", help="실제 주문 (LIVE+게이트 충족 시에만)")
    sub.add_parser("dashboard", help="tkinter 조회 전용 대시보드 (로컬, 실주문 없음)")
    p_webui = sub.add_parser("web-ui", help="웹 대시보드 — 러너 제어 / 설정 / 로그 (localhost:8765)")
    p_webui.add_argument("--port",       type=int, default=8765,          help="포트 (기본 8765)")
    p_webui.add_argument("--host",       default="127.0.0.1",             help="바인드 주소 (기본 127.0.0.1)")
    p_webui.add_argument("--output-dir", default="outputs",               help="outputs 디렉터리")
    p_webui.add_argument("--no-browser", action="store_true",             help="브라우저 자동 오픈 안 함")
    p_webui.add_argument("--tunnel",     action="store_true",             help="Cloudflare Quick Tunnel 자동 시작")

    p_tgwa = sub.add_parser("setup-telegram-webapp", help="텔레그램 봇에 WebApp 메뉴 버튼 등록")
    p_tgwa.add_argument("--url",          default="",    help="공개 URL (미입력 시 .env DEEPSIGNAL_WEBUI_PUBLIC_URL 사용)")
    p_tgwa.add_argument("--quick-tunnel", action="store_true", help="Cloudflare Quick Tunnel로 임시 URL 발급 후 등록")
    p_rd = sub.add_parser(
        "run-daily",
        help="일일 파이프라인: collect-news → collect-market → collect-macro → 심볼별 score/backtest/paper (또는 --paper-rebalance 시 포트폴리오 리밸런싱)",
    )
    p_rd.add_argument(
        "--skip-news",
        action="store_true",
        help="RSS 뉴스 수집 단계 생략",
    )
    p_rd.add_argument(
        "--skip-market",
        action="store_true",
        help="yfinance 시장 수집 단계 생략",
    )
    p_rd.add_argument(
        "--skip-macro",
        action="store_true",
        help="거시 지표 수집(collect-macro) 단계 생략",
    )
    p_rd.add_argument(
        "--symbols",
        type=str,
        default=None,
        metavar="SYM,...",
        help="처리할 티커(쉼표 구분). 지정 시 MARKET_SYMBOLS 대신 이 목록으로 수집·루프",
    )
    p_rd.add_argument(
        "--no-backtest",
        action="store_true",
        help="종목별 backtest-symbol 단계 생략",
    )
    p_rd.add_argument(
        "--no-paper",
        action="store_true",
        help="종목별 paper-step 및 paper-rebalance 생략",
    )
    p_rd.add_argument(
        "--paper-rebalance",
        action="store_true",
        help="모의 단계에서 종목별 paper-step 대신 포트폴리오 기반 paper-rebalance 1회 (--no-paper 시 무시)",
    )
    p_rd.add_argument(
        "--log-json",
        action="store_true",
        dest="log_json",
        help="logs/daily_pipeline_YYYYMMDD_HHMMSS.json 에 실행 요약 저장",
    )
    p_rd.add_argument(
        "--no-full-analysis",
        action="store_true",
        help="밸류에이션 배치·IPS 집중도·AUTO_ANALYSIS_SUMMARY 생략",
    )
    p_rd.add_argument(
        "--sync-live",
        action="store_true",
        help="KIS live-sync-account(+peak_price 갱신)를 run-daily 마지막에 실행 (--network, .env 필요)",
    )
    _attach_paper_rebalance_cost_args(p_rd)

    p_opt_w = sub.add_parser(
        "optimize-weights",
        help="[신호] GSQS 가중치 자동 최적화 실행 (200건 미만이면 현황만 표시)",
    )
    p_opt_w.add_argument(
        "--output-dir", type=str, default="outputs", metavar="DIR",
        help="signal_log.jsonl 및 optimized_weights.json 경로 (기본: outputs)",
    )
    p_opt_w.add_argument(
        "--force", action="store_true",
        help="200건 미만에도 강제 실행",
    )
    p_opt_w.add_argument(
        "--status", action="store_true",
        help="현황만 출력 (최적화 실행 안 함)",
    )
    p_opt_w.add_argument(
        "--horizon", type=int, default=5, choices=[1, 3, 5, 15],
        help="목표 시간대 분 (기본: 5)",
    )

    return parser


def _ensure_utf8_stdio() -> None:
    """Windows 콘솔 등에서 한국어 출력이 깨지지 않도록 시도한다."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        enc = getattr(stream, "encoding", None) or ""
        if enc.lower() == "utf-8":
            continue
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8")
            except (OSError, ValueError, TypeError):
                pass


def main(argv: list[str] | None = None) -> int:
    """CLI 진입. live-* 운영 커맨드 등은 결과에 따라 0/1, 그 외 0."""
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else None)
    if args.command == "collect-news":
        cmd_collect_news()
    elif args.command == "collect-market":
        cmd_collect_market()
    elif args.command == "collect-macro":
        cmd_collect_macro()
    elif args.command == "analyze-macro":
        cmd_analyze_macro()
    elif args.command == "analyze-portfolio":
        cmd_analyze_portfolio()
    elif args.command == "analyze-news":
        cmd_analyze_news(args.symbol)
    elif args.command == "analyze-technical":
        cmd_analyze_technical(args.symbol)
    elif args.command == "score-symbol":
        cmd_score_symbol(args.symbol)
    elif args.command == "show-analysis-conditions":
        return cmd_show_analysis_conditions(args)
    elif args.command == "backtest-symbol":
        cmd_backtest_symbol(args.symbol, include_news=bool(getattr(args, "include_news", False)))
    elif args.command == "paper-step":
        cmd_paper_step(args.symbol)
    elif args.command == "paper-rebalance":
        cmd_paper_rebalance(args)
    elif args.command == "live-plan":
        cmd_live_plan(args)
    elif args.command == "live-approve":
        return cmd_live_approve(args)
    elif args.command == "generate-test-order-plan":
        return cmd_generate_test_order_plan(args)
    elif args.command == "telegram-test":
        return cmd_telegram_test(args)
    elif args.command == "telegram-approval-request":
        return cmd_telegram_approval_request(args)
    elif args.command == "telegram-approval-listen":
        return cmd_telegram_approval_listen(args)
    elif args.command == "telegram-approval-status":
        return cmd_telegram_approval_status(args)
    elif args.command == "execute-last-approved":
        return cmd_execute_last_approved(args)
    elif args.command == "execute-approved":
        return cmd_execute_approved(args)
    elif args.command == "daily-ai-trade-plan":
        return cmd_daily_ai_trade_plan(args)
    elif args.command == "daily-ai-trade-report":
        return cmd_daily_ai_trade_report(args)
    elif args.command == "daily-ai-auto-runner":
        return cmd_daily_ai_auto_runner(args)
    elif args.command == "install-launchd":
        return cmd_install_launchd(args)
    elif args.command == "uninstall-launchd":
        return cmd_uninstall_launchd(args)
    elif args.command == "launchd-status":
        return cmd_launchd_status(args)
    elif args.command == "launchd-health-check":
        return cmd_launchd_health_check(args)
    elif args.command == "install-launchd-health-check":
        return cmd_install_launchd_health_check(args)
    elif args.command == "uninstall-launchd-health-check":
        return cmd_uninstall_launchd_health_check(args)
    elif args.command == "launchd-health-check-status":
        return cmd_launchd_health_check_status(args)
    elif args.command == "launchd-runner-test":
        return cmd_launchd_runner_test(args)
    elif args.command == "daily-ai-status":
        return cmd_daily_ai_status(args)
    elif args.command == "crypto-check":
        return cmd_crypto_check(args)
    elif args.command == "crypto-daily-plan":
        return cmd_crypto_daily_plan(args)
    elif args.command == "crypto-telegram-approval":
        return cmd_crypto_telegram_approval(args)
    elif args.command == "crypto-paper-status":
        return cmd_crypto_paper_status(args)
    elif args.command == "trading-halt":
        return cmd_trading_halt(args)
    elif args.command == "trading-resume":
        return cmd_trading_resume(args)
    elif args.command == "trading-halt-status":
        return cmd_trading_halt_status(args)
    elif args.command == "leverage-trend":
        return cmd_leverage_trend(args)
    elif args.command == "intraday-runner":
        return cmd_intraday_runner(args)
    elif args.command == "market-open-report":
        return cmd_market_open_report(args)
    elif args.command == "aggression-report":
        return cmd_aggression_report(args)
    elif args.command == "crypto-news-refresh":
        return cmd_crypto_news_refresh(args)
    elif args.command == "kr-scan":
        return cmd_kr_scan(args)
    elif args.command == "regime-trend-status":
        return cmd_regime_trend_status(args)
    elif args.command == "regime-trend-run":
        return cmd_regime_trend_run(args)
    elif args.command == "regime-trend-runner":
        return cmd_regime_trend_runner(args)
    elif args.command == "crypto-auto-runner":
        return cmd_crypto_auto_runner(args)
    elif args.command == "install-crypto-launchd":
        return cmd_install_crypto_launchd(args)
    elif args.command == "uninstall-crypto-launchd":
        return cmd_uninstall_crypto_launchd(args)
    elif args.command == "crypto-tune-thresholds":
        return cmd_crypto_tune_thresholds(args)
    elif args.command == "crypto-telegram-menu":
        return cmd_crypto_telegram_menu(args)
    elif args.command == "crypto-launchd-status":
        return cmd_crypto_launchd_status(args)
    elif args.command == "binance-stream":
        return cmd_binance_stream(args)
    elif args.command == "fetch-fear-greed":
        return cmd_fetch_fear_greed(args)
    elif args.command == "binance-features":
        return cmd_binance_features(args)
    elif args.command == "crypto-validate-ml":
        return cmd_crypto_validate_ml(args)
    elif args.command == "crypto-ml-suggest-config":
        return cmd_crypto_ml_suggest_config(args)
    elif args.command == "crypto-train-lgbm":
        return cmd_crypto_train_lgbm(args)
    elif args.command == "crypto-predict-lgbm":
        return cmd_crypto_predict_lgbm(args)
    elif args.command == "crypto-retrain-lgbm":
        return cmd_crypto_retrain_lgbm(args)
    elif args.command == "crypto-retrain-history":
        return cmd_crypto_retrain_history(args)
    elif args.command == "crypto-train-seq":
        return cmd_crypto_train_seq(args)
    elif args.command == "install-crypto-retrain-launchd":
        return cmd_install_crypto_retrain_launchd(args)
    elif args.command == "uninstall-crypto-retrain-launchd":
        from deepsignal.ml.crypto_retrain_launchd_installer import uninstall_crypto_retrain_launchd

        print(uninstall_crypto_retrain_launchd())
        return 0
    elif args.command == "install-binance-stream-launchd":
        return cmd_install_binance_stream_launchd(args)
    elif args.command == "uninstall-binance-stream-launchd":
        return cmd_uninstall_binance_stream_launchd(args)
    elif args.command == "binance-stream-launchd-status":
        return cmd_binance_stream_launchd_status(args)
    elif args.command == "install-kis-stream-launchd":
        return cmd_install_kis_stream_launchd(args)
    elif args.command == "uninstall-kis-stream-launchd":
        return cmd_uninstall_kis_stream_launchd(args)
    elif args.command == "kis-stream-launchd-status":
        return cmd_kis_stream_launchd_status(args)
    elif args.command == "install-overseas-stream-launchd":
        return cmd_install_kis_overseas_launchd(args)
    elif args.command == "uninstall-overseas-stream-launchd":
        return cmd_uninstall_kis_overseas_launchd(args)
    elif args.command == "overseas-stream-launchd-status":
        return cmd_kis_overseas_launchd_status(args)
    elif args.command == "overseas-stream":
        return cmd_overseas_stream(args)
    elif args.command == "overseas-auto-runner":
        return cmd_overseas_auto_runner(args)
    elif args.command == "kis-stream":
        return cmd_kis_stream(args)
    elif args.command == "kis-check":
        return cmd_kis_check(args)
    elif args.command == "live-order-status":
        return cmd_live_order_status(args)
    elif args.command == "live-sync-account":
        return cmd_live_sync_account(args)
    elif args.command == "reconcile-live-account":
        return cmd_reconcile_live_account(args)
    elif args.command == "live-order-guard-check":
        return cmd_live_order_guard_check(args)
    elif args.command == "live-fill-summary":
        return cmd_live_fill_summary(args)
    elif args.command == "trading-session-check":
        return cmd_trading_session_check(args)
    elif args.command == "pre-trade-runbook":
        return cmd_pre_trade_runbook(args)
    elif args.command == "post-trade-runbook":
        return cmd_post_trade_runbook(args)
    elif args.command == "risk-check":
        return cmd_risk_check(args)
    elif args.command == "ops-dashboard":
        return cmd_ops_dashboard(args)
    elif args.command == "sell-plan":
        return cmd_sell_plan(args)
    elif args.command == "notify-alerts":
        return cmd_notify_alerts(args)
    elif args.command == "daily-ops-summary":
        return cmd_daily_ops_summary(args)
    elif args.command == "html-dashboard":
        return cmd_html_dashboard(args)
    elif args.command == "cleanup-reports":
        return cmd_cleanup_reports(args)
    elif args.command == "report-index":
        return cmd_report_index(args)
    elif args.command == "ops-dry-run":
        return cmd_ops_dry_run(args)
    elif args.command == "open-dashboard":
        return cmd_open_dashboard(args)
    elif args.command == "report-health-check":
        return cmd_report_health_check(args)
    elif args.command == "weekly-maintenance":
        return cmd_weekly_maintenance(args)
    elif args.command == "tune-threshold-from-outcomes":
        return cmd_tune_threshold_from_outcomes(args)
    elif args.command == "weekly-report-bundle":
        return cmd_weekly_report_bundle(args)
    elif args.command == "archive-viewer":
        return cmd_archive_viewer(args)
    elif args.command == "ai-live-recommend":
        return cmd_ai_live_recommend(args)
    elif args.command == "validate-ai-recommendation":
        return cmd_validate_ai_recommendation(args)
    elif args.command == "generate-checklists":
        return cmd_generate_checklists(args)
    elif args.command == "safety-audit":
        return cmd_safety_audit(args)
    elif args.command == "init-context":
        return cmd_init_context(args)
    elif args.command == "show-signals":
        cmd_show_signals()
    elif args.command == "show-backtests":
        cmd_show_backtests()
    elif args.command == "show-paper":
        cmd_show_paper()
    elif args.command == "dashboard":
        cmd_dashboard()
    elif args.command == "web-ui":
        from deepsignal.web_ui.server import run_web_ui
        import os as _os
        _tunnel_proc = None
        if getattr(args, "tunnel", False):
            from deepsignal.web_ui.tunnel_manager import start_quick_tunnel, update_env_public_url
            from pathlib import Path as _Path
            print("[tunnel] Starting Cloudflare Quick Tunnel...", flush=True)
            _tunnel_proc, _tunnel_url = start_quick_tunnel(args.port)
            if _tunnel_url:
                print(f"[tunnel] URL: {_tunnel_url}", flush=True)
                _env_file = _Path(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env"))
                update_env_public_url(_tunnel_url, _env_file)
                # 텔레그램 메뉴 버튼 자동 업데이트 + 새 접속 링크 알림
                try:
                    _cmd_setup_webapp_url(_tunnel_url)
                    # .env 명시 로드 (launchd 환경에서 env 미로드 대비)
                    try:
                        from dotenv import load_dotenv as _ld
                        _ld(str(_env_file), override=True)
                    except Exception:
                        pass
                    _tok = _os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
                    _chat = _os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
                    if _tok and _chat:
                        from deepsignal.live_trading.telegram.approval import telegram_api_post as _tap
                        _tap("sendMessage", {
                            "chat_id": _chat,
                            "text": "🔄 DeepSignal 재시작 — 새 링크로 접속하세요",
                            "reply_markup": {"inline_keyboard": [[
                                {"text": "📊 DeepSignal 열기", "web_app": {"url": _tunnel_url}}
                            ]]},
                        }, bot_token=_tok)
                        print(f"[tunnel] Telegram 새 링크 알림 전송 완료", flush=True)
                    else:
                        print(f"[tunnel] Telegram 토큰 없음 — 알림 생략", flush=True)
                except Exception as _e:
                    print(f"[tunnel] menu button update skipped: {_e}", flush=True)
            else:
                print("[tunnel] Could not get tunnel URL — continuing without tunnel.", flush=True)
        run_web_ui(
            host=args.host,
            port=args.port,
            output_dir=args.output_dir,
            project_root=str(_os.path.dirname(_os.path.abspath(__file__))),
            env_path=str(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env")),
            no_browser=args.no_browser,
        )
        if _tunnel_proc:
            _tunnel_proc.terminate()
        return 0

    elif args.command == "setup-telegram-webapp":
        import os as _os
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv()
        _webapp_url = (getattr(args, "url", "") or "").strip()
        if getattr(args, "quick_tunnel", False):
            from deepsignal.web_ui.tunnel_manager import start_quick_tunnel, update_env_public_url
            from pathlib import Path as _Path
            print("[setup] Starting Cloudflare Quick Tunnel...", flush=True)
            _proc, _tunnel_url = start_quick_tunnel(8765)
            if not _tunnel_url:
                print("[ERROR] Could not start tunnel. Is cloudflared installed?")
                return 1
            print(f"[setup] Tunnel URL: {_tunnel_url}", flush=True)
            _webapp_url = _tunnel_url
            _env_file = _Path(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env"))
            update_env_public_url(_webapp_url, _env_file)
            print(f"[setup] Saved to .env: DEEPSIGNAL_WEBUI_PUBLIC_URL={_webapp_url}", flush=True)
            if _proc:
                _proc.terminate()
        result_code = _cmd_setup_webapp_url(_webapp_url)
        return result_code
    elif args.command == "optimize-weights":
        return cmd_optimize_weights(args)
    elif args.command == "run-daily":
        result = cmd_run_daily(args)
        from deepsignal.config.settings import load_settings as _load_settings
        from deepsignal.notifiers.notification_service import notify_pipeline_failure

        _settings = _load_settings()
        if not result.success and _settings.notify_on_failure:
            notify_pipeline_failure(_settings, result)
        return 0 if result.success else 1
    else:
        cmd_init()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
