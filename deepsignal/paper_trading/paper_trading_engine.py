"""모의투자 v1: DB 기반 가상 체결 기록 (실주문·브로커 없음)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

from deepsignal.analyzer.technical.technical_analyzer import TechnicalAnalyzer
from deepsignal.portfolio.portfolio_models import PortfolioSnapshot
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS
from deepsignal.scoring.signal_scorer import SignalScorer
from deepsignal.storage.database import (
    clear_paper_position,
    fetch_latest_market_price,
    fetch_market_prices,
    get_paper_cash,
    get_paper_positions,
    insert_paper_account_snapshot,
    insert_paper_trade,
    upsert_paper_position,
)


_COST = DEFAULT_ANALYSIS_CONDITIONS.cost


@dataclass
class PaperRebalanceConfig:
    """포트폴리오 모의 리밸런스 비용·최소 거래 규칙 (실주문·브로커 없음)."""

    commission_rate: float = _COST.commission_rate
    slippage_rate: float = _COST.slippage_rate
    min_trade_value: float = _COST.min_trade_value_usd
    rebalance_threshold: float = _COST.rebalance_threshold_fraction

    @classmethod
    def legacy_no_costs(cls) -> PaperRebalanceConfig:
        """기존 v1 단가 체결과 동일하게 맞추기 위한 설정(테스트용)."""
        return cls(
            commission_rate=0.0,
            slippage_rate=0.0,
            min_trade_value=0.0,
            rebalance_threshold=0.0,
        )


def paper_rebalance_config_from_namespace(ns: Any | None) -> PaperRebalanceConfig:
    """CLI `Namespace`에서 설정 구성. 속성이 없으면 기본값."""
    defaults = PaperRebalanceConfig()
    if ns is None:
        return defaults
    return PaperRebalanceConfig(
        commission_rate=float(
            getattr(ns, "commission_rate", defaults.commission_rate)
        ),
        slippage_rate=float(getattr(ns, "slippage_rate", defaults.slippage_rate)),
        min_trade_value=float(
            getattr(ns, "min_trade_value", defaults.min_trade_value)
        ),
        rebalance_threshold=float(
            getattr(ns, "rebalance_threshold", defaults.rebalance_threshold)
        ),
    )


@dataclass
class PaperPosition:
    """모의 포지션 스냅샷(평가용)."""

    symbol: str
    quantity: int
    avg_price: float
    last_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


@dataclass
class PaperTrade:
    """모의 체결 한 건."""

    symbol: str
    trade_date: str
    side: str
    price: float
    quantity: int
    cash_before: float
    cash_after: float
    reason: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaperAccountSnapshot:
    """모의 계좌 스냅샷."""

    snapshot_date: str
    cash: float
    equity: float
    positions_value: float
    positions: list[PaperPosition]
    last_action: str
    reason: str
    raw: dict[str, Any] = field(default_factory=dict)


def _float_close(row: Mapping[str, Any]) -> float | None:
    c = row.get("close")
    if c is None:
        return None
    try:
        v = float(c)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _last_close(db_path: str, symbol: str) -> float | None:
    rows = fetch_market_prices(
        db_path, symbol, source="yfinance", limit=1, timeframe="1d"
    )
    if not rows:
        return None
    return _float_close(rows[-1])


class PaperTradingEngine:
    """
    가상 계좌 단계 실행.

    - 최신 일봉 종가를 체결가로 사용 (v1).
    - `paper-step`은 수수료·슬리피지 0.
    - `rebalance_portfolio`는 `PaperRebalanceConfig`로 비용·최소 거래 반영 가능.
    """

    def __init__(
        self,
        initial_cash: float = 10000.0,
        strategy_name: str = "technical_v1",
        *,
        analyzer: TechnicalAnalyzer | None = None,
        scorer: SignalScorer | None = None,
    ) -> None:
        self.initial_cash = float(initial_cash)
        self.strategy_name = strategy_name
        self._analyzer = analyzer or TechnicalAnalyzer()
        self._scorer = scorer or SignalScorer()

    def load_state(self, db_path: str) -> tuple[float, list[dict]]:
        """현금(최신 스냅샷)과 `paper_positions` 행(dict) 목록."""
        cash = get_paper_cash(db_path, self.initial_cash)
        positions = get_paper_positions(db_path)
        return cash, positions

    def run_step(self, db_path: str, symbol: str) -> PaperAccountSnapshot | None:
        sym = symbol.strip().upper()
        indicators = self._analyzer.analyze_symbol_from_db(
            db_path, sym, source="yfinance", limit=120
        )
        if not indicators:
            return None
        latest = indicators[-1]
        price = latest.close
        if price is None or not math.isfinite(float(price)):
            return None
        fill = float(price)
        trade_date = str(latest.trade_date)

        from deepsignal.scoring.symbol_signal_builder import build_symbol_signal

        signal, _ = build_symbol_signal(db_path, sym)
        if signal is None:
            return None

        cash, pos_rows = self.load_state(db_path)
        pos_here = next(
            (p for p in pos_rows if str(p.get("symbol", "")).upper() == sym),
            None,
        )
        has_pos = (
            pos_here is not None and int(pos_here.get("quantity") or 0) > 0
        )

        last_action = signal.action
        reason = signal.reason
        extra_raw: dict[str, Any] = {
            "strategy_name": self.strategy_name,
            "signal_action": signal.action,
            "final_score": signal.final_score,
            "technical_score": signal.technical_score,
        }

        if signal.action == "BUY_CANDIDATE" and not has_pos and cash > 0:
            qty = int(cash // fill)
            if qty <= 0:
                last_action = "HOLD"
                reason = f"{signal.reason} (매수 가능 수량 없음)"
            else:
                cost = qty * fill
                cash_after = cash - cost
                insert_paper_trade(
                    db_path,
                    PaperTrade(
                        symbol=sym,
                        trade_date=trade_date,
                        side="BUY",
                        price=fill,
                        quantity=qty,
                        cash_before=cash,
                        cash_after=cash_after,
                        reason=signal.reason,
                        raw=extra_raw,
                    ),
                )
                upsert_paper_position(
                    db_path,
                    {"symbol": sym, "quantity": qty, "avg_price": fill},
                )
                cash = cash_after

        elif signal.action == "SELL_CANDIDATE" and has_pos and pos_here is not None:
            qty = int(pos_here.get("quantity") or 0)
            if qty > 0:
                proceeds = qty * fill
                cash_after = cash + proceeds
                insert_paper_trade(
                    db_path,
                    PaperTrade(
                        symbol=sym,
                        trade_date=trade_date,
                        side="SELL",
                        price=fill,
                        quantity=qty,
                        cash_before=cash,
                        cash_after=cash_after,
                        reason=signal.reason,
                        raw=extra_raw,
                    ),
                )
                clear_paper_position(db_path, sym)
                cash = cash_after

        pos_rows = get_paper_positions(db_path)
        positions_out: list[PaperPosition] = []
        pos_val = 0.0
        for pr in pos_rows:
            psym = str(pr.get("symbol", "")).upper()
            qty = int(pr.get("quantity") or 0)
            if qty <= 0:
                continue
            avg_px = float(pr.get("avg_price") or 0.0)
            last_px = _last_close(db_path, psym)
            if last_px is None:
                last_px = avg_px
            mv = qty * last_px
            unreal = (last_px - avg_px) * qty
            unreal_pct = ((last_px / avg_px) - 1.0) * 100.0 if avg_px > 0 else 0.0
            positions_out.append(
                PaperPosition(
                    symbol=psym,
                    quantity=qty,
                    avg_price=avg_px,
                    last_price=last_px,
                    market_value=mv,
                    unrealized_pnl=unreal,
                    unrealized_pnl_pct=unreal_pct,
                )
            )
            pos_val += mv

        equity = cash + pos_val
        snap_raw: dict[str, Any] = {
            "step_symbol": sym,
            "strategy_name": self.strategy_name,
            "fill_price": fill,
            "signal": extra_raw,
        }
        snapshot = PaperAccountSnapshot(
            snapshot_date=trade_date,
            cash=float(cash),
            equity=float(equity),
            positions_value=float(pos_val),
            positions=positions_out,
            last_action=last_action,
            reason=reason,
            raw=snap_raw,
        )
        insert_paper_account_snapshot(db_path, snapshot)
        return snapshot

    def rebalance_portfolio(
        self,
        db_path: str,
        portfolio_snapshot: PortfolioSnapshot,
        *,
        liquidate_missing: bool = True,
        rebalance_config: PaperRebalanceConfig | None = None,
    ) -> PaperAccountSnapshot | None:
        """`allocations_for_paper` 기준 가상 리밸런싱 (실주문·브로커 없음).

        시장가는 DB 최신 종가. 체결가는 슬리피지 반영, 수수료는 현금에 반영.
        `paper_trades.price`에는 **체결가(executed)** 를 저장하고, 세부는 `raw_json`에 둔다.
        """
        cfg = rebalance_config or PaperRebalanceConfig()
        raw_snap = portfolio_snapshot.raw or {}
        allocs = raw_snap.get("allocations_for_paper") or []
        if not isinstance(allocs, list):
            allocs = []

        cash = float(get_paper_cash(db_path, self.initial_cash))
        pos_rows = get_paper_positions(db_path)
        positions: dict[str, dict[str, float]] = {}
        for pr in pos_rows:
            sym = str(pr.get("symbol", "")).strip().upper()
            q = int(pr.get("quantity") or 0)
            if not sym or q <= 0:
                continue
            positions[sym] = {
                "quantity": float(q),
                "avg_price": float(pr.get("avg_price") or 0.0),
            }

        target_syms: set[str] = set()
        targets: list[tuple[str, float, str]] = []
        for a in allocs:
            if not isinstance(a, dict):
                continue
            sym = str(a.get("symbol", "")).strip().upper()
            if not sym:
                continue
            try:
                amt = float(a.get("target_amount") or 0.0)
            except (TypeError, ValueError):
                amt = 0.0
            rationale = str(a.get("rationale") or "portfolio rebalance v1")
            target_syms.add(sym)
            targets.append((sym, amt, rationale))

        prices: dict[str, tuple[str, float]] = {}
        symbols_needed: set[str] = set(positions.keys()) | target_syms
        for sym in symbols_needed:
            row = fetch_latest_market_price(db_path, sym, source="yfinance")
            if row is None:
                continue
            td = str(row.get("trade_date") or "")
            px = float(row["close"])
            if px > 0 and math.isfinite(px):
                prices[sym] = (td, px)

        def _mark_equity(
            csh: float, pos: dict[str, dict[str, float]], px_map: dict[str, tuple[str, float]]
        ) -> float:
            v = float(csh)
            for s, st in pos.items():
                q = int(st["quantity"])
                if q <= 0:
                    continue
                if s in px_map:
                    v += q * float(px_map[s][1])
                else:
                    v += q * float(st["avg_price"])
            return v

        initial_equity = _mark_equity(cash, positions, prices)

        def _skip_gap(abs_gap: float) -> bool:
            if abs_gap < float(cfg.min_trade_value):
                return True
            thr = float(initial_equity) * float(cfg.rebalance_threshold)
            if abs_gap < thr:
                return True
            return False

        trade_log: list[dict[str, Any]] = []
        snapshot_dates: list[str] = []

        def _trade_raw_base() -> dict[str, Any]:
            return {
                "mode": "rebalance_v1",
                "strategy_name": self.strategy_name,
                "commission_rate": cfg.commission_rate,
                "slippage_rate": cfg.slippage_rate,
                "min_trade_value": cfg.min_trade_value,
                "rebalance_threshold": cfg.rebalance_threshold,
            }

        def _sell(sym: str, qty: int, market_px: float, td: str, reason: str) -> None:
            nonlocal cash, positions, trade_log
            if qty <= 0 or market_px <= 0 or not math.isfinite(market_px):
                return
            st = positions.get(sym)
            if st is None:
                return
            cur_q = int(st["quantity"])
            if cur_q <= 0:
                return
            sell_q = min(qty, cur_q)
            executed = float(market_px) * (1.0 - float(cfg.slippage_rate))
            if executed <= 0 or not math.isfinite(executed):
                return
            gross = sell_q * executed
            commission = gross * float(cfg.commission_rate)
            net_in = gross - commission
            cash_after = cash + net_in
            raw = {
                **_trade_raw_base(),
                "market_price": float(market_px),
                "executed_price": float(executed),
                "commission": float(commission),
            }
            insert_paper_trade(
                db_path,
                PaperTrade(
                    symbol=sym,
                    trade_date=td,
                    side="SELL",
                    price=float(executed),
                    quantity=sell_q,
                    cash_before=cash,
                    cash_after=cash_after,
                    reason=reason,
                    raw=raw,
                ),
            )
            trade_log.append(
                {
                    "side": "SELL",
                    "symbol": sym,
                    "quantity": sell_q,
                    "price": float(executed),
                    "market_price": float(market_px),
                    "executed_price": float(executed),
                    "commission": float(commission),
                }
            )
            cash = cash_after
            new_q = cur_q - sell_q
            if new_q <= 0:
                del positions[sym]
                clear_paper_position(db_path, sym)
            else:
                avg = float(st["avg_price"])
                positions[sym] = {"quantity": float(new_q), "avg_price": avg}
                upsert_paper_position(
                    db_path,
                    {"symbol": sym, "quantity": new_q, "avg_price": avg},
                )

        def _buy(sym: str, qty: int, market_px: float, td: str, reason: str) -> None:
            nonlocal cash, positions, trade_log
            if qty <= 0 or market_px <= 0 or not math.isfinite(market_px):
                return
            executed = float(market_px) * (1.0 + float(cfg.slippage_rate))
            if executed <= 0 or not math.isfinite(executed):
                return
            unit_all_in = executed * (1.0 + float(cfg.commission_rate))
            if unit_all_in <= 0:
                return
            max_afford = int(cash // unit_all_in)
            buy_q = min(qty, max_afford)
            if buy_q <= 0:
                return
            gross = buy_q * executed
            commission = gross * float(cfg.commission_rate)
            total_out = gross + commission
            cash_after = cash - total_out
            st = positions.get(sym)
            if st is None or int(st["quantity"]) <= 0:
                new_q = buy_q
                new_avg = total_out / buy_q if buy_q > 0 else executed
            else:
                old_q = int(st["quantity"])
                old_avg = float(st["avg_price"])
                new_q = old_q + buy_q
                old_cost = old_q * old_avg
                new_avg = (old_cost + total_out) / new_q if new_q > 0 else executed
            raw = {
                **_trade_raw_base(),
                "market_price": float(market_px),
                "executed_price": float(executed),
                "commission": float(commission),
            }
            insert_paper_trade(
                db_path,
                PaperTrade(
                    symbol=sym,
                    trade_date=td,
                    side="BUY",
                    price=float(executed),
                    quantity=buy_q,
                    cash_before=cash,
                    cash_after=cash_after,
                    reason=reason,
                    raw=raw,
                ),
            )
            trade_log.append(
                {
                    "side": "BUY",
                    "symbol": sym,
                    "quantity": buy_q,
                    "price": float(executed),
                    "market_price": float(market_px),
                    "executed_price": float(executed),
                    "commission": float(commission),
                }
            )
            cash = cash_after
            positions[sym] = {"quantity": float(new_q), "avg_price": float(new_avg)}
            upsert_paper_position(
                db_path,
                {"symbol": sym, "quantity": new_q, "avg_price": new_avg},
            )

        if liquidate_missing:
            for sym in list(positions.keys()):
                if sym not in target_syms:
                    if sym not in prices:
                        continue
                    td, mkt = prices[sym]
                    if not td:
                        continue
                    cur_q = int(positions[sym]["quantity"])
                    cur_val = cur_q * mkt
                    if _skip_gap(abs(0.0 - cur_val)):
                        continue
                    snapshot_dates.append(td)
                    _sell(
                        sym,
                        cur_q,
                        mkt,
                        td,
                        "liquidate_missing (not in target portfolio)",
                    )

        for sym, target_amt, rationale in targets:
            if sym not in prices:
                continue
            td, mkt = prices[sym]
            if not td:
                continue
            cur_qty = int(positions[sym]["quantity"]) if sym in positions else 0
            current_value = cur_qty * mkt

            if target_amt <= 0:
                if sym in positions and liquidate_missing:
                    if _skip_gap(abs(0.0 - current_value)):
                        continue
                    snapshot_dates.append(td)
                    _sell(
                        sym,
                        cur_qty,
                        mkt,
                        td,
                        "target amount zero",
                    )
                continue

            if _skip_gap(abs(float(target_amt) - float(current_value))):
                continue

            target_qty = int(target_amt // mkt)
            diff = target_qty - cur_qty
            if diff != 0:
                snapshot_dates.append(td)
            if diff < 0:
                _sell(sym, -diff, mkt, td, rationale)
            elif diff > 0:
                _buy(sym, diff, mkt, td, rationale)

        positions_out: list[PaperPosition] = []
        pos_val = 0.0
        for sym, st in positions.items():
            qty = int(st["quantity"])
            if qty <= 0:
                continue
            avg_px = float(st["avg_price"])
            last_px = _last_close(db_path, sym)
            if last_px is None:
                last_px = avg_px
            mv = qty * float(last_px)
            unreal = (float(last_px) - avg_px) * qty
            unreal_pct = ((float(last_px) / avg_px) - 1.0) * 100.0 if avg_px > 0 else 0.0
            positions_out.append(
                PaperPosition(
                    symbol=sym,
                    quantity=qty,
                    avg_price=avg_px,
                    last_price=float(last_px),
                    market_value=mv,
                    unrealized_pnl=unreal,
                    unrealized_pnl_pct=unreal_pct,
                )
            )
            pos_val += mv

        equity = cash + pos_val
        if snapshot_dates:
            snap_date = max(snapshot_dates)
        else:
            snap_date = str(portfolio_snapshot.analyzed_at)[:10]
        if len(snap_date) < 10:
            snap_date = date.today().isoformat()

        summary_actions = (
            ", ".join(
                f"{t['side']} {t['symbol']} qty={t['quantity']} price={t['price']:.2f}"
                for t in trade_log
            )
            if trade_log
            else "no trades"
        )
        snap = PaperAccountSnapshot(
            snapshot_date=str(snap_date)[:10],
            cash=float(cash),
            equity=float(equity),
            positions_value=float(pos_val),
            positions=positions_out,
            last_action="REBALANCE",
            reason=f"portfolio rebalance v1 ({summary_actions})",
            raw={
                "mode": "rebalance_v1",
                "strategy_name": self.strategy_name,
                "portfolio_analyzed_at": portfolio_snapshot.analyzed_at,
                "market_regime": portfolio_snapshot.market_regime,
                "rebalance_trades": trade_log,
                "liquidate_missing": liquidate_missing,
                "rebalance_config": {
                    "commission_rate": cfg.commission_rate,
                    "slippage_rate": cfg.slippage_rate,
                    "min_trade_value": cfg.min_trade_value,
                    "rebalance_threshold": cfg.rebalance_threshold,
                },
            },
        )
        insert_paper_account_snapshot(db_path, snap)
        return snap
