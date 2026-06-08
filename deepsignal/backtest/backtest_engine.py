"""단일 종목 백테스트 v1/v2 (과거 데이터 검증용, 실주문 없음)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from deepsignal.analyzer.technical.technical_analyzer import TechnicalAnalyzer
from deepsignal.scoring.signal_scorer import SignalResult, SignalScorer


@dataclass
class BacktestTrade:
    """가상 매매 한 건(진입·청산 기록)."""

    symbol: str
    entry_date: str
    exit_date: str | None
    entry_price: float
    exit_price: float | None
    quantity: int
    pnl: float | None
    pnl_pct: float | None
    reason: str


@dataclass
class BacktestResult:
    """백테스트 집계 결과."""

    symbol: str
    strategy_name: str
    start_date: str
    end_date: str
    initial_cash: float
    final_value: float
    total_return_pct: float
    trade_count: int
    win_rate: float | None
    max_drawdown_pct: float | None
    raw: dict[str, Any] = field(default_factory=dict)


def _row_close(row: Mapping[str, Any]) -> float | None:
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


class BacktestEngine:
    """
    과거 OHLCV 리플레이.

    - 시그널은 일 i 종가 시점까지의 지표로 산출.
    - 체결은 시그널 발생 다음 거래일 종가(v1 단순화).
    - 수수료·슬리피지 0 (추후 필드로 확장).
    - ``include_news=True``이면 각 ``trade_date``까지 ``published_at``이 있는 뉴스만
      감성에 사용(당일 포함, 이후 뉴스 제외·룩어헤드 없음).
    """

    COMMISSION_RATE: float = 0.0
    SLIPPAGE_BPS: float = 0.0
    DEFAULT_STRATEGY: str = "technical_v1"

    def __init__(
        self,
        *,
        analyzer: TechnicalAnalyzer | None = None,
        scorer: SignalScorer | None = None,
    ) -> None:
        self._analyzer = analyzer or TechnicalAnalyzer()
        self._scorer = scorer or SignalScorer()

    def run_symbol_backtest(
        self,
        symbol: str,
        rows: Sequence[Mapping[str, Any]],
        initial_cash: float = 10000.0,
        *,
        include_news: bool = False,
        db_path: str | None = None,
    ) -> BacktestResult | None:
        if initial_cash <= 0 or not math.isfinite(initial_cash):
            return None

        sym = symbol.strip().upper()
        sorted_rows = sorted(
            rows,
            key=lambda r: str(r.get("bar_time") or r.get("trade_date") or ""),
        )
        valid_rows = [r for r in sorted_rows if _row_close(r) is not None]
        if not valid_rows:
            return None

        indicators = self._analyzer.analyze_prices(sym, valid_rows)
        n = len(indicators)
        if n == 0:
            return None

        use_news = bool(include_news) and db_path is not None and str(db_path).strip() != ""

        news_scores: list[float | None] = []
        day_signals: list[SignalResult | None] = []
        for i in range(n):
            news_score_val: float | None = None
            if use_news:
                try:
                    from deepsignal.analyzer.sentiment.sentiment_analyzer import SentimentAnalyzer
                    from deepsignal.storage.database import fetch_news_items_until

                    until = str(indicators[i].trade_date)
                    nrows = fetch_news_items_until(db_path, sym, until, limit=100)
                    news_score_val = SentimentAnalyzer().analyze_news_items(sym, nrows).news_score
                except Exception:
                    news_score_val = None
            news_scores.append(news_score_val)
            sig = self._scorer.score_latest(
                sym,
                indicators[: i + 1],
                news_score=news_score_val,
                macro_score=None,
            )
            day_signals.append(sig)

        cash = float(initial_cash)
        qty = 0
        open_trade: BacktestTrade | None = None
        closed_trades: list[BacktestTrade] = []
        equity_curve: list[dict[str, Any]] = []
        peak_equity = 0.0
        min_dd_pct: float | None = None

        for i in range(n):
            ind = indicators[i]
            px = ind.close
            if px is None or not math.isfinite(px):
                continue
            dt = str(ind.trade_date)

            if i > 0:
                prev = day_signals[i - 1]
                if prev is None:
                    act = "INSUFFICIENT_DATA"
                    reason = ""
                else:
                    act = prev.action
                    reason = prev.reason

                if act == "BUY_CANDIDATE" and qty == 0:
                    q = int(cash // float(px))
                    if q > 0:
                        cost = q * float(px)
                        cash -= cost
                        qty = q
                        open_trade = BacktestTrade(
                            symbol=sym,
                            entry_date=dt,
                            exit_date=None,
                            entry_price=float(px),
                            exit_price=None,
                            quantity=q,
                            pnl=None,
                            pnl_pct=None,
                            reason=reason or "BUY_CANDIDATE",
                        )
                elif act == "SELL_CANDIDATE" and qty > 0 and open_trade is not None:
                    proceeds = qty * float(px)
                    cash += proceeds
                    cost_basis = open_trade.entry_price * open_trade.quantity
                    pnl = proceeds - cost_basis
                    pnl_pct = (pnl / cost_basis * 100.0) if cost_basis > 0 else None
                    closed = BacktestTrade(
                        symbol=sym,
                        entry_date=open_trade.entry_date,
                        exit_date=dt,
                        entry_price=open_trade.entry_price,
                        exit_price=float(px),
                        quantity=open_trade.quantity,
                        pnl=float(pnl),
                        pnl_pct=float(pnl_pct) if pnl_pct is not None else None,
                        reason=reason or "SELL_CANDIDATE",
                    )
                    closed_trades.append(closed)
                    qty = 0
                    open_trade = None

            equity = cash + qty * float(px)
            pt: dict[str, Any] = {"trade_date": dt, "equity": equity}
            if use_news:
                pt["news_score"] = news_scores[i]
            equity_curve.append(pt)
            if equity > peak_equity:
                peak_equity = equity
            if peak_equity > 0:
                dd_pct = (equity / peak_equity - 1.0) * 100.0
                min_dd_pct = dd_pct if min_dd_pct is None else min(min_dd_pct, dd_pct)

        last = indicators[-1]
        last_close = last.close
        if last_close is None or not math.isfinite(last_close):
            last_close = 0.0
        final_value = cash + qty * float(last_close)
        total_return_pct = (final_value / float(initial_cash) - 1.0) * 100.0

        wins = sum(1 for t in closed_trades if t.pnl is not None and t.pnl > 0)
        trade_count = len(closed_trades)
        win_rate = (wins / trade_count * 100.0) if trade_count > 0 else None

        start_date = str(indicators[0].trade_date)
        end_date = str(indicators[-1].trade_date)

        trades_out: list[dict[str, Any]] = [asdict(t) for t in closed_trades]
        if open_trade is not None:
            trades_out.append(asdict(open_trade))

        raw: dict[str, Any] = {
            "trades": trades_out,
            "equity_curve": equity_curve,
            "parameters": {
                "initial_cash": initial_cash,
                "commission_rate": self.COMMISSION_RATE,
                "slippage_bps": self.SLIPPAGE_BPS,
                "strategy_name": self.DEFAULT_STRATEGY,
                "execution": "next_bar_close",
                "include_news": include_news,
                "db_path_used": use_news,
            },
        }

        return BacktestResult(
            symbol=sym,
            strategy_name=self.DEFAULT_STRATEGY,
            start_date=start_date,
            end_date=end_date,
            initial_cash=float(initial_cash),
            final_value=float(final_value),
            total_return_pct=float(total_return_pct),
            trade_count=trade_count,
            win_rate=win_rate,
            max_drawdown_pct=min_dd_pct,
            raw=raw,
        )
