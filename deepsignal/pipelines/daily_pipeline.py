"""일일 파이프라인: 뉴스·시장 수집 후 설정된 심볼별 점수·백테스트·모의 1스텝.

브로커·실주문 없음. DB 경로는 settings.db_path 기준으로 init_database 후 사용한다.
"""

from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepsignal.config.settings import Settings
    from deepsignal.paper_trading.paper_trading_engine import PaperRebalanceConfig


@dataclass
class PipelineStepResult:
    """단일 단계(수집·심볼별 score 등) 실행 결과."""

    name: str
    status: str  # success | skipped | failed | partial_failed
    message: str = ""
    raw: Any = None


@dataclass
class DailyPipelineResult:
    """run-daily 전체 실행 요약."""

    started_at: str
    finished_at: str
    symbols: tuple[str, ...]
    steps: list[PipelineStepResult] = field(default_factory=list)
    success: bool = True
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    log_json_path: str | None = None


def _normalize_symbol_list(symbols: tuple[str, ...] | None, settings: Settings) -> tuple[str, ...]:
    if symbols is None or len(symbols) == 0:
        src = settings.market_symbols
    else:
        src = symbols
    out: list[str] = []
    for raw in src:
        s = raw.strip()
        if s:
            out.append(s.upper())
    return tuple(out)


def collect_news_to_db(path_str: str, settings: Settings) -> dict[str, Any]:
    """RSS 뉴스 수집 후 SQLite 저장 (cmd_collect_news와 동일 동작)."""
    from deepsignal.collector.news.news_collector import NewsCollector
    from deepsignal.storage.database import insert_collection_run, insert_news_items

    collector = NewsCollector(feeds=settings.rss_feeds)
    all_items: list = []
    for source_name, batch, err in collector.collect_per_source():
        status = "success" if err is None else "error"
        insert_collection_run(path_str, source_name, status, len(batch), err)
        all_items.extend(batch)

    stats = insert_news_items(path_str, all_items)
    print("DeepSignal news collection finished")
    print(f"Collected: {len(all_items)}")
    print(f"Inserted: {stats['inserted']}")
    print(f"Skipped: {stats['skipped']}")
    print(f"Failed: {stats['failed']}")
    return {
        "collected": len(all_items),
        "inserted": stats["inserted"],
        "skipped": stats["skipped"],
        "failed": stats["failed"],
    }


def collect_market_to_db(
    path_str: str,
    settings: Settings,
    symbols: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """yfinance 일봉 수집 후 SQLite 저장 (cmd_collect_market와 동일 동작).

    symbols가 주어지면 해당 목록만 수집한다. None이면 settings.market_symbols를 쓴다.
    """
    from deepsignal.collector.market.market_collector import MarketCollector
    from deepsignal.storage.database import insert_collection_run, insert_market_prices

    sym_tuple = symbols if symbols is not None else settings.market_symbols
    collector = MarketCollector(
        symbols=sym_tuple,
        period=settings.market_period,
        interval=settings.market_interval,
    )

    all_rows: list = []
    errors: list[str] = []
    for symbol, batch, err in collector.collect_per_symbol():
        if err:
            errors.append(f"{symbol}: {err}")
        all_rows.extend(batch)

    if errors and not all_rows:
        yf_status = "failed"
    elif errors:
        yf_status = "partial_failed"
    else:
        yf_status = "success"

    err_msg = "; ".join(errors) if errors else None

    stats = insert_market_prices(
        path_str, all_rows, timeframe=settings.market_interval
    )
    insert_collection_run(
        path_str,
        "yfinance",
        yf_status,
        len(all_rows),
        err_msg,
        collector_type_override="market_yfinance",
    )

    sym_line = ", ".join(sym_tuple)
    print("DeepSignal market collection finished")
    print(f"Symbols: {sym_line}")
    print(f"Collected: {len(all_rows)}")
    print(f"Inserted: {stats['inserted']}")
    print(f"Skipped: {stats['skipped']}")
    print(f"Failed: {stats['failed']}")
    return {
        "yfinance_status": yf_status,
        "symbols": list(sym_tuple),
        "collected": len(all_rows),
        "inserted": stats["inserted"],
        "skipped": stats["skipped"],
        "failed": stats["failed"],
        "collector_errors": errors,
    }


def collect_macro_to_db(path_str: str, settings: Settings) -> dict[str, Any]:
    """VIX·DXY·TNX 등 yfinance 거시 지표 수집 후 economic_indicators 저장.

    ``settings``는 시그니처 일관성용(현재 미사용).
    """
    _ = settings
    from deepsignal.collector.economic.economic_collector import EconomicCollector
    from deepsignal.storage.database import insert_collection_run, insert_economic_indicators

    collector = EconomicCollector()
    rows = collector.collect_macro_indicators()
    stats = insert_economic_indicators(path_str, rows)
    if len(rows) == 0:
        coll_status = "failed"
    elif len(rows) < 3:
        coll_status = "partial_failed"
    else:
        coll_status = "success"
    err_msg = None if coll_status == "success" else f"collected {len(rows)} of 3 series"
    insert_collection_run(
        path_str,
        "economic_macro",
        coll_status,
        len(rows),
        err_msg,
        collector_type_override="macro_yfinance",
    )

    print("DeepSignal macro indicators collection finished")
    print(f"Collected series: {len(rows)}")
    print(f"Inserted: {stats['inserted']}")
    print(f"Skipped: {stats['skipped']}")
    print(f"Failed: {stats['failed']}")
    return {
        "collector_status": coll_status,
        "collected": len(rows),
        "inserted": stats["inserted"],
        "skipped": stats["skipped"],
        "failed": stats["failed"],
    }


def score_symbol_to_db(path_str: str, symbol: str) -> dict[str, Any]:
    """기술·뉴스(궤적)·거시·밸류 통합 점수 산출 후 signals 저장."""
    from deepsignal.scoring.symbol_signal_builder import build_symbol_signal
    from deepsignal.storage.database import insert_signal_result

    sym = symbol.strip().upper()
    meta: dict[str, Any] = {
        "outcome": "no_data",
        "symbol": sym,
        "news_score": None,
        "news_count": 0,
        "technical_score": None,
        "macro_score": None,
        "valuation_score": None,
        "mispricing_pct": None,
        "final_score": None,
        "signal_date": None,
    }

    signal, build_meta = build_symbol_signal(path_str, sym)
    meta.update(build_meta)
    if signal is None:
        print(
            f"Insufficient technical data for {sym}. "
            "Run collect-market with longer period."
        )
        return meta

    stats = insert_signal_result(path_str, signal)
    meta["outcome"] = "success"
    meta["news_score"] = signal.news_score
    meta["technical_score"] = signal.technical_score
    meta["macro_score"] = signal.macro_score
    meta["final_score"] = signal.final_score
    meta["signal_date"] = signal.signal_date
    meta["action"] = signal.action
    meta["valuation_score"] = build_meta.get("valuation_score")
    meta["mispricing_pct"] = build_meta.get("mispricing_pct")

    print("DeepSignal signal scoring finished")
    print(f"Symbol: {signal.symbol}")
    print(f"Date: {signal.signal_date}")
    print(f"Action: {signal.action}")
    tech_s = "-" if signal.technical_score is None else f"{signal.technical_score:.1f}"
    news_s = "-" if signal.news_score is None else f"{signal.news_score:.1f}"
    macro_s = "-" if signal.macro_score is None else f"{signal.macro_score:.2f}"
    val_s = "-" if meta.get("valuation_score") is None else f"{meta['valuation_score']:.1f}"
    fs = "-" if signal.final_score is None else f"{signal.final_score:.1f}"
    print(f"Technical Score: {tech_s}")
    print(f"News Score: {news_s}")
    print(f"Macro Score: {macro_s}")
    print(f"Valuation Score: {val_s}")
    print(f"Final Score: {fs}")
    cf = "-" if signal.confidence is None else f"{signal.confidence:.2f}"
    print(f"Confidence: {cf}")
    print(f"Reason: {signal.reason}")
    print(
        f"Saved: inserted={stats['inserted']}, skipped={stats['skipped']}, failed={stats['failed']}"
    )
    return meta


def backtest_symbol_to_db(path_str: str, symbol: str, *, include_news: bool = False) -> str:
    """단일 종목 백테스트 v1/v2. 반환: success | no_data | no_result."""
    from deepsignal.backtest.backtest_engine import BacktestEngine
    from deepsignal.storage.database import fetch_market_prices, insert_backtest_result

    sym = symbol.strip().upper()
    rows = fetch_market_prices(
        path_str, sym, source="yfinance", limit=None, timeframe="1d"
    )
    if not rows:
        print(
            f"Insufficient market data for {sym}. "
            "Run collect-market with longer period."
        )
        return "no_data"

    engine = BacktestEngine()
    result = engine.run_symbol_backtest(
        sym,
        rows,
        include_news=include_news,
        db_path=path_str if include_news else None,
    )
    if result is None:
        print(
            f"Insufficient market data for {sym}. "
            "Run collect-market with longer period."
        )
        return "no_result"

    stats = insert_backtest_result(path_str, result)

    print("DeepSignal backtest finished")
    print(f"Include News: {include_news}")
    print(f"Symbol: {result.symbol}")
    print(f"Strategy: {result.strategy_name}")
    print(f"Period: {result.start_date} ~ {result.end_date}")
    print(f"Initial Cash: {result.initial_cash:.2f}")
    print(f"Final Value: {result.final_value:.2f}")
    print(f"Total Return: {result.total_return_pct:.2f}%")
    print(f"Trades: {result.trade_count}")
    wr = "N/A" if result.win_rate is None else f"{result.win_rate:.2f}%"
    print(f"Win Rate: {wr}")
    mdd = "N/A" if result.max_drawdown_pct is None else f"{result.max_drawdown_pct:.2f}%"
    print(f"Max Drawdown: {mdd}")
    print(
        f"Saved: inserted={stats['inserted']}, skipped={stats['skipped']}, failed={stats['failed']}"
    )
    return "success"


def paper_step_to_db(path_str: str, symbol: str) -> str:
    """모의투자 한 스텝. 반환: success | no_data."""
    from deepsignal.paper_trading.paper_trading_engine import PaperTradingEngine

    sym = symbol.strip().upper()
    engine = PaperTradingEngine()
    snap = engine.run_step(path_str, sym)
    if snap is None:
        print(
            f"Insufficient market data for {sym}. "
            "Run collect-market with longer period."
        )
        return "no_data"

    print("DeepSignal paper trading step finished")
    print(f"Symbol: {sym}")
    print(f"Date: {snap.snapshot_date}")
    print(f"Action: {snap.last_action}")
    print(f"Cash: {snap.cash:.2f}")
    print(f"Equity: {snap.equity:.2f}")
    print(f"Positions Value: {snap.positions_value:.2f}")
    print(f"Reason: {snap.reason}")
    return "success"


def paper_rebalance_to_db(
    path_str: str,
    settings: Settings,
    *,
    rebalance_config: PaperRebalanceConfig | None = None,
) -> dict[str, Any]:
    """포트폴리오 스냅샷 기준 모의 리밸런싱. 반환 dict에 outcome·trades 등."""
    _ = settings
    from deepsignal.paper_trading.paper_trading_engine import (
        PaperRebalanceConfig,
        PaperTradingEngine,
    )
    from deepsignal.portfolio.portfolio_engine import PortfolioEngine
    from deepsignal.scoring.macro_scorer import MacroScorer
    from deepsignal.storage.database import (
        fetch_latest_economic_indicators,
        fetch_latest_paper_snapshot,
        fetch_latest_signals,
    )

    cfg = rebalance_config or PaperRebalanceConfig()

    signals = fetch_latest_signals(path_str, limit=100)
    macro = MacroScorer().calculate_macro_score(fetch_latest_economic_indicators(path_str))
    snap_row = fetch_latest_paper_snapshot(path_str)
    if snap_row is not None and snap_row.get("equity") is not None:
        total_cash = float(snap_row["equity"])
    elif snap_row is not None and snap_row.get("cash") is not None:
        total_cash = float(snap_row["cash"])
    else:
        total_cash = 10_000.0

    p_snap = PortfolioEngine().build_portfolio(signals, total_cash, macro)
    engine = PaperTradingEngine()
    snap = engine.rebalance_portfolio(
        path_str,
        p_snap,
        liquidate_missing=True,
        rebalance_config=cfg,
    )
    if snap is None:
        meta = {"outcome": "no_data", "trades": []}
        print("DeepSignal paper rebalance skipped (no snapshot)")
        return meta

    trades = list(snap.raw.get("rebalance_trades") or [])
    print("DeepSignal paper rebalance finished")
    print(f"Date: {snap.snapshot_date}")
    print(f"Cash: {snap.cash:.2f}")
    print(f"Equity: {snap.equity:.2f}")
    print(f"Positions Value: {snap.positions_value:.2f}")
    print("Trades:")
    if not trades:
        print("(none)")
    else:
        for t in trades:
            print(
                f"{t.get('side')} {t.get('symbol')} qty={t.get('quantity')} "
                f"price={float(t.get('price', 0)):.2f}"
            )

    return {
        "outcome": "success",
        "snapshot_date": snap.snapshot_date,
        "cash": snap.cash,
        "equity": snap.equity,
        "positions_value": snap.positions_value,
        "trades": trades,
        "market_regime": p_snap.market_regime,
        "rebalance_config": asdict(cfg),
    }


def _outcome_to_status(outcome: str) -> str:
    if outcome == "success":
        return "success"
    return "partial_failed"


def _normalize_score_symbol_return(out: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """`score_symbol_to_db` 반환(str 레거시 또는 dict)을 (outcome, step_raw)로 통일."""
    if isinstance(out, dict):
        oc = str(out.get("outcome", "failed"))
        return oc, dict(out)
    return str(out), {"outcome": out}


def _market_step_status(raw: dict[str, Any]) -> str:
    yf = raw.get("yfinance_status", "success")
    if yf == "failed":
        return "failed"
    if yf == "partial_failed":
        return "partial_failed"
    return "success"


def _macro_step_status(raw: dict[str, Any]) -> str:
    st = raw.get("collector_status", "success")
    if st == "failed":
        return "failed"
    if st == "partial_failed":
        return "partial_failed"
    return "success"


def _write_json_log(
    path: Path,
    result: DailyPipelineResult,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def step_dict(s: PipelineStepResult) -> dict[str, Any]:
        return {"name": s.name, "status": s.status, "message": s.message, "raw": s.raw}

    payload: dict[str, Any] = {
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "options": result.options,
        "symbols": list(result.symbols),
        "steps": [step_dict(s) for s in result.steps],
        "errors": result.errors,
        "success": result.success,
        "summary": result.summary,
    }
    macro_log: dict[str, Any] | None = None
    for s in result.steps:
        if s.name.startswith("score:") and isinstance(s.raw, dict):
            r = s.raw
            macro_log = {
                "symbol": r.get("symbol"),
                "macro_score": r.get("macro_score"),
                "market_regime": r.get("market_regime"),
                "macro_confidence": r.get("macro_confidence"),
            }
            break
    payload["macro"] = macro_log
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def print_daily_pipeline_summary(result: DailyPipelineResult) -> None:
    """터미널용 실행 요약."""
    print()
    print("DeepSignal daily pipeline finished")
    print(f"Success: {result.success}")
    sym_line = ", ".join(result.symbols) if result.symbols else "(none)"
    print(f"Symbols: {sym_line}")
    print("Steps:")
    for s in result.steps:
        msg = f" ({s.message})" if s.message else ""
        print(f"- {s.name}: {s.status}{msg}")
    if result.errors:
        print("Errors:")
        for e in result.errors:
            print(f"- {e}")
    if result.log_json_path:
        print(f"Log JSON: {result.log_json_path}")


def run_daily_pipeline(
    settings: Settings,
    *,
    skip_news: bool = False,
    skip_market: bool = False,
    skip_macro: bool = False,
    symbols: tuple[str, ...] | None = None,
    run_backtest: bool = True,
    run_paper: bool = True,
    paper_rebalance: bool = False,
    write_log_json: bool = False,
    paper_rebalance_config: PaperRebalanceConfig | None = None,
    full_analysis: bool = True,
    sync_live_account: bool = False,
) -> DailyPipelineResult:
    """일일 순서: DB 초기화 → 수집 → 심볼별 score/backtest/paper → (옵션) 밸류·집중도·실계좌 동기화."""
    from deepsignal.paper_trading.paper_trading_engine import PaperRebalanceConfig
    from deepsignal.storage.database import init_database

    started = datetime.now()
    started_at = started.isoformat(timespec="seconds")
    rb_cfg = paper_rebalance_config or PaperRebalanceConfig()
    options: dict[str, Any] = {
        "skip_news": skip_news,
        "skip_market": skip_market,
        "skip_macro": skip_macro,
        "symbols_override": list(symbols) if symbols is not None else None,
        "run_backtest": run_backtest,
        "run_paper": run_paper,
        "paper_rebalance": paper_rebalance,
        "write_log_json": write_log_json,
        "paper_rebalance_config": asdict(rb_cfg),
        "full_analysis": full_analysis,
        "sync_live_account": sync_live_account,
    }

    steps: list[PipelineStepResult] = []
    errors: list[str] = []
    loop_symbols = _normalize_symbol_list(symbols, settings)
    use_symbol_override = bool(symbols)
    market_symbols_arg: tuple[str, ...] | None = (
        loop_symbols if use_symbol_override else None
    )

    result = DailyPipelineResult(
        started_at=started_at,
        finished_at=started_at,
        symbols=loop_symbols,
        steps=steps,
        success=True,
        errors=errors,
        summary={},
        options=options,
    )

    try:
        path_str = str(init_database(settings.db_path))
    except Exception as e:
        tb = traceback.format_exc()
        err_line = f"init_database: {type(e).__name__}: {e}"
        errors.append(err_line)
        errors.append(tb)
        steps.append(
            PipelineStepResult(
                "init_database",
                "failed",
                str(e),
                {"traceback": tb},
            )
        )
        result.finished_at = datetime.now().isoformat(timespec="seconds")
        result.steps = steps
        result.errors = errors
        result.success = False
        result.summary = {
            "fatal": True,
            "step_count": len(steps),
            "failed_steps": 1,
        }
        if write_log_json:
            log_name = f"daily_pipeline_{started.strftime('%Y%m%d_%H%M%S')}.json"
            log_path = Path("logs") / log_name
            _write_json_log(log_path, result)
            result.log_json_path = str(log_path.resolve())
        print("DeepSignal daily pipeline started")
        print(f"(init failed) {err_line}")
        print_daily_pipeline_summary(result)
        return result

    print("DeepSignal daily pipeline started")
    print(f"DB: {path_str}")

    print()
    print("--- Step: collect-news ---")
    if skip_news:
        steps.append(
            PipelineStepResult("collect-news", "skipped", "skip_news=True", None)
        )
    else:
        try:
            raw = collect_news_to_db(path_str, settings)
            steps.append(PipelineStepResult("collect-news", "success", "", raw))
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"{type(e).__name__}: {e}"
            errors.append(f"collect-news: {msg}")
            errors.append(tb)
            steps.append(
                PipelineStepResult(
                    "collect-news",
                    "failed",
                    msg,
                    {"traceback": tb},
                )
            )

    print()
    print("--- Step: collect-market ---")
    if skip_market:
        steps.append(
            PipelineStepResult("collect-market", "skipped", "skip_market=True", None)
        )
    else:
        try:
            raw = collect_market_to_db(
                path_str, settings, symbols=market_symbols_arg
            )
            st = _market_step_status(raw)
            msg = ""
            if raw.get("collector_errors"):
                msg = "; ".join(raw["collector_errors"][:3])
                if len(raw["collector_errors"]) > 3:
                    msg += " ..."
            steps.append(PipelineStepResult("collect-market", st, msg, raw))
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"{type(e).__name__}: {e}"
            errors.append(f"collect-market: {msg}")
            errors.append(tb)
            steps.append(
                PipelineStepResult(
                    "collect-market",
                    "failed",
                    msg,
                    {"traceback": tb},
                )
            )

    print()
    print("--- Step: collect-macro ---")
    if skip_macro:
        steps.append(
            PipelineStepResult("collect-macro", "skipped", "skip_macro=True", None)
        )
    else:
        try:
            raw = collect_macro_to_db(path_str, settings)
            st = _macro_step_status(raw)
            msg = ""
            if raw.get("collector_status") != "success":
                msg = f"status={raw.get('collector_status')}"
            steps.append(PipelineStepResult("collect-macro", st, msg, raw))
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"{type(e).__name__}: {e}"
            errors.append(f"collect-macro: {msg}")
            errors.append(tb)
            steps.append(
                PipelineStepResult(
                    "collect-macro",
                    "failed",
                    msg,
                    {"traceback": tb},
                )
            )

    for u in loop_symbols:
        print()
        print(f"--- Symbol: {u} (score-symbol, backtest-symbol, paper-step) ---")

        # score
        try:
            out = score_symbol_to_db(path_str, u)
            outcome, step_raw = _normalize_score_symbol_return(out)
            st = _outcome_to_status(outcome)
            message = "" if outcome == "success" else f"outcome={outcome}"
            steps.append(
                PipelineStepResult(f"score:{u}", st, message, step_raw)
            )
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"{type(e).__name__}: {e}"
            errors.append(f"score:{u}: {msg}")
            errors.append(tb)
            steps.append(
                PipelineStepResult(
                    f"score:{u}",
                    "failed",
                    msg,
                    {"traceback": tb},
                )
            )

        if run_backtest:
            try:
                out = backtest_symbol_to_db(path_str, u)
                st = _outcome_to_status(out)
                message = "" if out == "success" else f"outcome={out}"
                steps.append(
                    PipelineStepResult(f"backtest:{u}", st, message, {"outcome": out})
                )
            except Exception as e:
                tb = traceback.format_exc()
                msg = f"{type(e).__name__}: {e}"
                errors.append(f"backtest:{u}: {msg}")
                errors.append(tb)
                steps.append(
                    PipelineStepResult(
                        f"backtest:{u}",
                        "failed",
                        msg,
                        {"traceback": tb},
                    )
                )
        else:
            steps.append(
                PipelineStepResult(
                    f"backtest:{u}",
                    "skipped",
                    "run_backtest=False",
                    None,
                )
            )

        if run_paper:
            if paper_rebalance:
                steps.append(
                    PipelineStepResult(
                        f"paper:{u}",
                        "skipped",
                        "paper_rebalance=True (single portfolio step after loop)",
                        None,
                    )
                )
            else:
                try:
                    out = paper_step_to_db(path_str, u)
                    st = _outcome_to_status(out)
                    message = "" if out == "success" else f"outcome={out}"
                    steps.append(
                        PipelineStepResult(f"paper:{u}", st, message, {"outcome": out})
                    )
                except Exception as e:
                    tb = traceback.format_exc()
                    msg = f"{type(e).__name__}: {e}"
                    errors.append(f"paper:{u}: {msg}")
                    errors.append(tb)
                    steps.append(
                        PipelineStepResult(
                            f"paper:{u}",
                            "failed",
                            msg,
                            {"traceback": tb},
                        )
                    )
        else:
            steps.append(
                PipelineStepResult(
                    f"paper:{u}",
                    "skipped",
                    "run_paper=False",
                    None,
                )
            )

    if run_paper and paper_rebalance:
        print()
        print("--- Step: paper-rebalance (portfolio) ---")
        try:
            raw = paper_rebalance_to_db(path_str, settings, rebalance_config=rb_cfg)
            oc = str(raw.get("outcome", "failed"))
            st = _outcome_to_status(oc)
            msg = "" if oc == "success" else f"outcome={oc}"
            steps.append(PipelineStepResult("paper-rebalance", st, msg, raw))
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"{type(e).__name__}: {e}"
            errors.append(f"paper-rebalance: {msg}")
            errors.append(tb)
            steps.append(
                PipelineStepResult(
                    "paper-rebalance",
                    "failed",
                    msg,
                    {"traceback": tb},
                )
            )

    if sync_live_account:
        print()
        print("--- Step: live-sync-account (network) ---")
        try:
            from deepsignal.live_trading.live_account_sync import (
                build_account_snapshot_payload,
                persist_live_account_snapshot_to_db,
                write_live_account_snapshot_paths,
            )
            from deepsignal.live_trading.kis_broker import KISBroker
            from deepsignal.live_trading.kis_config import load_kis_config_from_env

            br = KISBroker(load_kis_config_from_env(), safe_mode=True)
            payload = build_account_snapshot_payload(br)
            write_live_account_snapshot_paths(payload)
            npos, _, ts = persist_live_account_snapshot_to_db(path_str, payload, broker="kis")
            steps.append(
                PipelineStepResult(
                    "live-sync-account",
                    "success",
                    f"positions={npos} snapshot={ts}",
                    {"positions": npos, "peak_tracking": True},
                )
            )
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"{type(e).__name__}: {e}"
            errors.append(f"live-sync-account: {msg}")
            steps.append(
                PipelineStepResult("live-sync-account", "failed", msg, {"traceback": tb})
            )

    if full_analysis:
        print()
        print("--- Step: full-analysis (valuation + concentration) ---")
        try:
            from deepsignal.pipelines.auto_analysis import run_full_analysis_extras

            val_step, conc_step = run_full_analysis_extras(path_str, loop_symbols, broker="kis")
            steps.append(val_step)
            steps.append(conc_step)
            if val_step.status == "failed":
                errors.append(f"valuation-batch: {val_step.message}")
            if conc_step.status == "failed":
                errors.append(f"concentration-check: {conc_step.message}")
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"{type(e).__name__}: {e}"
            errors.append(f"full-analysis: {msg}")
            steps.append(
                PipelineStepResult("full-analysis", "failed", msg, {"traceback": tb})
            )

    finished_at = datetime.now().isoformat(timespec="seconds")
    failed_like = sum(1 for s in steps if s.status == "failed")
    result.finished_at = finished_at
    result.steps = steps
    result.errors = errors
    result.success = failed_like == 0 and len(errors) == 0
    result.summary = {
        "step_count": len(steps),
        "failed_steps": failed_like,
        "skipped_steps": sum(1 for s in steps if s.status == "skipped"),
        "partial_failed_steps": sum(1 for s in steps if s.status == "partial_failed"),
        "error_lines": len(errors),
    }

    if write_log_json:
        log_name = f"daily_pipeline_{started.strftime('%Y%m%d_%H%M%S')}.json"
        log_path = Path("logs") / log_name
        _write_json_log(log_path, result)
        result.log_json_path = str(log_path.resolve())

    print_daily_pipeline_summary(result)
    return result
