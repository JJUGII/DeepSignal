"""SQLite 연결 및 스키마 초기화."""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional

from deepsignal.config.settings import load_settings

if TYPE_CHECKING:
    from deepsignal.backtest.backtest_engine import BacktestResult
    from deepsignal.collector.market.market_data import MarketData
    from deepsignal.collector.news.news_item import NewsItem
    from deepsignal.paper_trading.paper_trading_engine import (
        PaperAccountSnapshot,
        PaperTrade,
    )
    from deepsignal.scoring.signal_scorer import SignalResult

_SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"

_EXPECTED_TABLES = frozenset(
    {
        "news_items",
        "market_prices",
        "economic_indicators",
        "signals",
        "trades",
        "backtest_results",
        "paper_positions",
        "paper_trades",
        "paper_account_snapshots",
        "collection_runs",
        "real_positions",
        "real_account_snapshots",
        "real_order_history",
        "real_fill_history",
    }
)


def _ensure_parent_dir(db_path: Path) -> None:
    parent = db_path.parent
    if parent and str(parent) not in (".", ""):
        parent.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """SQLite 연결을 반환한다. db_path가 None이면 load_settings().db_path 사용."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    conn = sqlite3.connect(str(resolved), timeout=5.0)
    conn.row_factory = sqlite3.Row
    # 동시성: 러너·대시보드·헬스체크가 같은 DB를 동시에 열 수 있으므로
    # WAL(읽기-쓰기 비차단) + busy_timeout(락 대기)으로 "database is locked"를 방지한다.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    return conn


def _migrate_news_items_columns(conn: sqlite3.Connection) -> None:
    """기존 DB에 news_items 확장 컬럼을 추가한다."""
    cur = conn.execute("PRAGMA table_info(news_items)")
    cols = {row[1] for row in cur.fetchall()}
    if "summary" not in cols:
        conn.execute("ALTER TABLE news_items ADD COLUMN summary TEXT")
    if "symbol" not in cols:
        conn.execute("ALTER TABLE news_items ADD COLUMN symbol TEXT")
    if "raw_json" not in cols:
        conn.execute("ALTER TABLE news_items ADD COLUMN raw_json TEXT")


def _migrate_market_prices_schema(conn: sqlite3.Connection) -> None:
    """기존 market_prices에 source/adj/raw 및 UNIQUE 갱신을 반영한다."""
    cur = conn.execute("PRAGMA table_info(market_prices)")
    cols = {row[1] for row in cur.fetchall()}
    if not cols:
        return
    if "source" in cols and "adjusted_close" in cols and "raw_json" in cols:
        return
    conn.execute("ALTER TABLE market_prices RENAME TO market_prices_old")
    conn.execute(
        """
        CREATE TABLE market_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            bar_time TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'yfinance',
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adjusted_close REAL,
            volume REAL,
            raw_json TEXT,
            UNIQUE (symbol, timeframe, bar_time, source)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO market_prices (
            created_at, symbol, timeframe, bar_time, source,
            open, high, low, close, adjusted_close, volume, raw_json
        )
        SELECT
            created_at, symbol, timeframe, bar_time, 'yfinance',
            open, high, low, close, NULL, volume, NULL
        FROM market_prices_old
        """
    )
    conn.execute("DROP TABLE market_prices_old")


def _migrate_signals_schema(conn: sqlite3.Connection) -> None:
    """signals 테이블을 점수화 v1 스키마로 갱신한다."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='signals'"
    )
    if not cur.fetchone():
        return
    cur = conn.execute("PRAGMA table_info(signals)")
    cols = {row[1] for row in cur.fetchall()}
    if "signal_date" in cols and "strategy_name" in cols and "final_score" in cols:
        return
    conn.execute("ALTER TABLE signals RENAME TO signals_old")
    conn.execute(
        """
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            symbol TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            strategy_name TEXT NOT NULL DEFAULT 'technical_v1',
            technical_score REAL,
            news_score REAL,
            macro_score REAL,
            final_score REAL,
            action TEXT NOT NULL,
            confidence REAL,
            reason TEXT,
            raw_json TEXT,
            UNIQUE (symbol, signal_date, strategy_name)
        )
        """
    )
    conn.execute("DROP TABLE signals_old")


def _migrate_backtest_results_schema(conn: sqlite3.Connection) -> None:
    """backtest_results를 심볼·기간·지표 v1 스키마로 갱신한다."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='backtest_results'"
    )
    if not cur.fetchone():
        return
    cur = conn.execute("PRAGMA table_info(backtest_results)")
    cols = {row[1] for row in cur.fetchall()}
    if not cols:
        return
    if "symbol" in cols and "start_date" in cols and "final_value" in cols:
        return
    conn.execute("ALTER TABLE backtest_results RENAME TO backtest_results_old")
    conn.execute(
        """
        CREATE TABLE backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            symbol TEXT NOT NULL,
            strategy_name TEXT NOT NULL DEFAULT 'technical_v1',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            initial_cash REAL NOT NULL,
            final_value REAL NOT NULL,
            total_return_pct REAL NOT NULL,
            trade_count INTEGER NOT NULL,
            win_rate REAL,
            max_drawdown_pct REAL,
            raw_json TEXT,
            UNIQUE (symbol, strategy_name, start_date, end_date)
        )
        """
    )
    conn.execute("DROP TABLE backtest_results_old")


def _migrate_economic_indicators_schema(conn: sqlite3.Connection) -> None:
    """economic_indicators를 macro v1 스키마(indicator_name·source·raw_json·UNIQUE)로 맞춘다."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='economic_indicators'"
    )
    if not cur.fetchone():
        return
    cur = conn.execute("PRAGMA table_info(economic_indicators)")
    cols = {row[1] for row in cur.fetchall()}
    if not cols:
        return
    if (
        "indicator_name" in cols
        and "indicator_date" in cols
        and "source" in cols
        and "raw_json" in cols
    ):
        return
    conn.execute("ALTER TABLE economic_indicators RENAME TO economic_indicators_old")
    conn.execute(
        """
        CREATE TABLE economic_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            indicator_name TEXT NOT NULL,
            indicator_date TEXT NOT NULL,
            value REAL,
            source TEXT NOT NULL DEFAULT 'yfinance',
            raw_json TEXT,
            UNIQUE (indicator_name, indicator_date, source)
        )
        """
    )
    if "series_id" in cols and "observed_at" in cols:
        conn.execute(
            """
            INSERT OR IGNORE INTO economic_indicators (
                created_at, indicator_name, indicator_date, value, source, raw_json
            )
            SELECT
                created_at,
                series_id,
                observed_at,
                value,
                'yfinance',
                NULL
            FROM economic_indicators_old
            """
        )
    conn.execute("DROP TABLE economic_indicators_old")


def init_database(db_path: Optional[str] = None) -> Path:
    """
    스키마를 적용해 DB를 초기화한다.

    Returns:
        실제 사용된 DB 파일의 절대 경로.
    """
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)

    schema_sql = _SCHEMA_FILE.read_text(encoding="utf-8")
    with sqlite3.connect(str(resolved)) as conn:
        conn.executescript(schema_sql)
        _migrate_news_items_columns(conn)
        _migrate_market_prices_schema(conn)
        _migrate_signals_schema(conn)
        _migrate_backtest_results_schema(conn)
        _migrate_economic_indicators_schema(conn)
        _migrate_real_account_tables(conn)
        conn.commit()

    return resolved


def insert_news_items(
    db_path: Optional[str],
    items: list["NewsItem"],
) -> dict[str, int]:
    """news_items에 일괄 삽입. source_hash UNIQUE 충돌은 skipped로 집계."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)

    inserted = 0
    skipped = 0
    failed = 0
    sql = (
        "INSERT OR IGNORE INTO news_items "
        "(source, source_hash, url, title, body_text, published_at, summary, symbol, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    with sqlite3.connect(str(resolved)) as conn:
        _migrate_news_items_columns(conn)
        for item in items:
            try:
                raw_json = json.dumps(item.raw, ensure_ascii=False)
            except (TypeError, ValueError):
                failed += 1
                continue
            url_val = item.url if item.url else None
            try:
                cur = conn.execute(
                    sql,
                    (
                        item.source,
                        item.source_hash,
                        url_val,
                        item.title,
                        None,
                        item.published_at,
                        item.summary,
                        item.symbol,
                        raw_json,
                    ),
                )
            except sqlite3.Error:
                failed += 1
                continue
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
        conn.commit()
    return {"inserted": inserted, "skipped": skipped, "failed": failed}


def fetch_recent_news_items(
    db_path: Optional[str],
    symbol: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """최근 뉴스 행을 dict 목록으로 반환한다.

    ``symbol``이 주어지면 ``news_items.symbol``이 일치하는 행 또는
    제목·요약에 해당 티커 문자열이 포함된 행(LIKE, 대소문자 무시)을 포함한다.
    ``symbol``이 비어 있으면 전체 뉴스 중 최근 ``limit``건만 반환한다.
    """
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    cap = max(1, min(int(limit), 500))
    rows: list[dict[str, Any]] = []
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_news_items_columns(conn)
        if symbol and str(symbol).strip():
            s = str(symbol).strip().upper()
            like_pat = f"%{s}%"
            cur = conn.execute(
                """
                SELECT id, source, title, summary, symbol, published_at, url, created_at
                FROM news_items
                WHERE (
                    (symbol IS NOT NULL AND TRIM(symbol) != ''
                        AND UPPER(TRIM(symbol)) = ?)
                    OR (UPPER(IFNULL(title, '')) LIKE ?)
                    OR (UPPER(IFNULL(summary, '')) LIKE ?)
                )
                ORDER BY COALESCE(NULLIF(TRIM(published_at), ''), created_at) DESC
                LIMIT ?
                """,
                (s, like_pat, like_pat, cap),
            )
        else:
            cur = conn.execute(
                """
                SELECT id, source, title, summary, symbol, published_at, url, created_at
                FROM news_items
                ORDER BY COALESCE(NULLIF(TRIM(published_at), ''), created_at) DESC
                LIMIT ?
                """,
                (cap,),
            )
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def fetch_news_items_until(
    db_path: Optional[str],
    symbol: str,
    until_date: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """``until_date``(거래일, YYYY-MM-DD) 이하 ``published_at`` 만 뉴스로 조회한다.

    ``published_at`` 이 NULL이거나 공백인 행은 제외(룩어헤드 방지·피드 품질 가정).
    ``symbol`` 컬럼 일치 또는 ``title``/``summary`` LIKE(대소문자 무시)로 필터한다.
    최신 ``published_at`` 기준 상위 ``limit`` 건만 가져온 뒤, ``published_at`` 오름차순으로 정렬해 반환한다.
    """
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    cap = max(1, min(int(limit), 500))
    ud = str(until_date).strip()
    if len(ud) >= 10:
        ud = ud[:10]
    s = str(symbol).strip().upper()
    if not s:
        return []
    like_pat = f"%{s}%"
    rows_rev: list[dict[str, Any]] = []
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_news_items_columns(conn)
        cur = conn.execute(
            """
            SELECT id, source, title, summary, symbol, published_at, url, created_at
            FROM news_items
            WHERE published_at IS NOT NULL
              AND TRIM(published_at) != ''
              AND DATE(TRIM(published_at)) <= DATE(?)
              AND (
                  (symbol IS NOT NULL AND TRIM(symbol) != ''
                      AND UPPER(TRIM(symbol)) = ?)
                  OR (UPPER(IFNULL(title, '')) LIKE ?)
                  OR (UPPER(IFNULL(summary, '')) LIKE ?)
              )
            ORDER BY DATE(TRIM(published_at)) DESC, id DESC
            LIMIT ?
            """,
            (ud, s, like_pat, like_pat, cap),
        )
        rows_rev = [dict(r) for r in cur.fetchall()]
    rows_rev.reverse()
    return rows_rev


def insert_market_prices(
    db_path: Optional[str],
    items: list["MarketData"],
    *,
    timeframe: str = "1d",
) -> dict[str, int]:
    """market_prices에 일괄 삽입. (symbol, timeframe, bar_time, source) UNIQUE 충돌은 skipped."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)

    inserted = 0
    skipped = 0
    failed = 0
    sql = (
        "INSERT OR IGNORE INTO market_prices "
        "(symbol, timeframe, bar_time, source, open, high, low, close, adjusted_close, volume, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    tf = timeframe.strip() or "1d"
    with sqlite3.connect(str(resolved)) as conn:
        _migrate_market_prices_schema(conn)
        for item in items:
            try:
                raw_json = json.dumps(item.raw, ensure_ascii=False)
            except (TypeError, ValueError):
                failed += 1
                continue
            vol = float(item.volume) if item.volume is not None else None
            try:
                cur = conn.execute(
                    sql,
                    (
                        item.symbol,
                        tf,
                        item.trade_date,
                        item.source,
                        item.open,
                        item.high,
                        item.low,
                        item.close,
                        item.adjusted_close,
                        vol,
                        raw_json,
                    ),
                )
            except sqlite3.Error:
                failed += 1
                continue
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
        conn.commit()
    return {"inserted": inserted, "skipped": skipped, "failed": failed}


def insert_economic_indicators(
    db_path: Optional[str],
    items: list[Any],
) -> dict[str, int]:
    """economic_indicators 일괄 삽입. (indicator_name, indicator_date, source) UNIQUE는 skipped."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)

    inserted = 0
    skipped = 0
    failed = 0
    sql = (
        "INSERT OR IGNORE INTO economic_indicators "
        "(indicator_name, indicator_date, value, source, raw_json) "
        "VALUES (?, ?, ?, ?, ?)"
    )
    with sqlite3.connect(str(resolved)) as conn:
        _migrate_economic_indicators_schema(conn)
        for item in items:
            name = str(getattr(item, "indicator_name", "") or "").strip()
            idate = str(getattr(item, "indicator_date", "") or "").strip()
            source = str(getattr(item, "source", "yfinance") or "yfinance").strip() or "yfinance"
            val = getattr(item, "value", None)
            raw_obj = getattr(item, "raw", None) or {}
            if not name or not idate:
                failed += 1
                continue
            try:
                raw_json = json.dumps(raw_obj, ensure_ascii=False)
            except (TypeError, ValueError):
                failed += 1
                continue
            try:
                cur = conn.execute(sql, (name, idate, val, source, raw_json))
            except sqlite3.Error:
                failed += 1
                continue
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
        conn.commit()
    return {"inserted": inserted, "skipped": skipped, "failed": failed}


def fetch_latest_economic_indicators(db_path: Optional[str]) -> list[dict[str, Any]]:
    """지표명당 최신 indicator_date 한 건씩 dict 목록으로 반환."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    rows: list[dict[str, Any]] = []
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_economic_indicators_schema(conn)
        cur = conn.execute(
            """
            SELECT indicator_name, indicator_date, value, source, raw_json
            FROM economic_indicators
            ORDER BY indicator_date DESC, id DESC
            """
        )
        seen: set[str] = set()
        for r in cur.fetchall():
            d = dict(r)
            n = str(d.get("indicator_name", "") or "").strip().upper()
            if not n or n in seen:
                continue
            seen.add(n)
            rows.append(d)
    return rows


def fetch_market_prices(
    db_path: Optional[str],
    symbol: str,
    source: str = "yfinance",
    limit: Optional[int] = None,
    *,
    timeframe: str = "1d",
) -> list[dict]:
    """
    market_prices에서 심볼별 OHLCV 행을 조회한다.

    - bar_time 오름차순으로 반환한다.
    - limit가 있으면 **최근** limit개를 가져온 뒤, 다시 오래된 순으로 정렬한다.
    """
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    sym = symbol.strip().upper()
    tf = (timeframe or "1d").strip() or "1d"

    cols = (
        "id, created_at, symbol, timeframe, bar_time, source, "
        "open, high, low, close, adjusted_close, volume, raw_json"
    )
    base_where = "symbol = ? AND source = ? AND timeframe = ?"
    params_base: list = [sym, source, tf]

    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_market_prices_schema(conn)
        if limit is not None and int(limit) > 0:
            lim = int(limit)
            cur = conn.execute(
                f"SELECT {cols} FROM market_prices WHERE {base_where} "
                "ORDER BY bar_time DESC LIMIT ?",
                (*params_base, lim),
            )
            rows = list(reversed(cur.fetchall()))
        else:
            cur = conn.execute(
                f"SELECT {cols} FROM market_prices WHERE {base_where} ORDER BY bar_time ASC",
                params_base,
            )
            rows = cur.fetchall()

    out: list[dict] = []
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        d["trade_date"] = d.get("bar_time")
        out.append(d)
    return out


def fetch_latest_market_price(
    db_path: Optional[str],
    symbol: str,
    source: str = "yfinance",
    *,
    timeframe: str = "1d",
) -> dict[str, Any] | None:
    """심볼의 가장 최근 일봉 종가 한 건. 없거나 종가 불가면 None."""
    rows = fetch_market_prices(
        db_path,
        symbol,
        source=source,
        limit=1,
        timeframe=timeframe,
    )
    if not rows:
        return None
    r = rows[-1]
    close = r.get("close")
    if close is None:
        return None
    try:
        px = float(close)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(px) or px <= 0:
        return None
    td = r.get("trade_date") or r.get("bar_time")
    if td is None:
        return None
    return {
        "symbol": str(symbol).strip().upper(),
        "trade_date": str(td),
        "close": px,
    }


def fetch_recent_signals(db_path: Optional[str], limit: int = 20) -> list[dict[str, Any]]:
    """signals 최신순(created_at, id 기준) limit개.

    반환 dict 키: symbol, signal_date, action, technical_score, news_score,
    macro_score, final_score, confidence, reason
    """
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    lim = max(1, int(limit))
    sql = (
        "SELECT symbol, signal_date, action, technical_score, news_score, macro_score, "
        "final_score, confidence, reason "
        "FROM signals ORDER BY created_at DESC, id DESC LIMIT ?"
    )
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_signals_schema(conn)
        cur = conn.execute(sql, (lim,))
        rows = cur.fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def fetch_latest_signals(db_path: Optional[str], limit: int = 100) -> list[dict[str, Any]]:
    """심볼당 최신 1건(`technical_v1`, `signal_date`·`id` 기준) signals를 최대 limit개 반환.

    반환 dict 키: symbol, signal_date, final_score, action, confidence,
    technical_score, news_score, macro_score
    """
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    cap = 500
    try:
        import os

        cap_raw = (os.environ.get("KIS_STOCK_SIGNAL_SCAN_MAX") or "").strip()
        if cap_raw:
            cap = max(500, min(int(float(cap_raw)), 5000))
        elif int(limit) > 500:
            cap = max(500, min(int(limit), 5000))
    except (TypeError, ValueError):
        cap = 500
    lim = max(1, min(int(limit), cap))
    sql = """
WITH ranked AS (
    SELECT symbol, signal_date, final_score, action, confidence,
           technical_score, news_score, macro_score,
           ROW_NUMBER() OVER (
               PARTITION BY symbol
               ORDER BY signal_date DESC, id DESC
           ) AS rn
    FROM signals
    WHERE strategy_name = 'technical_v1'
)
SELECT symbol, signal_date, final_score, action, confidence,
       technical_score, news_score, macro_score
FROM ranked
WHERE rn = 1
LIMIT ?
"""
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_signals_schema(conn)
        cur = conn.execute(sql, (lim,))
        rows = cur.fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def fetch_distinct_market_symbols(
    db_path: Optional[str],
    *,
    limit: int = 2000,
    lookback_days: int = 30,
    source: str | None = "yfinance",
) -> list[str]:
    """최근 market_prices에 등장한 심볼 목록(신호 DB 밖 종목 스캔용)."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    lim = max(1, min(int(limit), 10000))
    days = max(1, int(lookback_days))
    params: list[Any] = [f"-{days} days"]
    source_clause = ""
    if source:
        source_clause = " AND source = ?"
        params.append(str(source))
    params.append(lim)
    sql = f"""
SELECT symbol
FROM market_prices
WHERE bar_time >= date('now', ?)
{source_clause}
GROUP BY symbol
ORDER BY MAX(bar_time) DESC
LIMIT ?
"""
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_market_prices_schema(conn)
        cur = conn.execute(sql, tuple(params))
        rows = cur.fetchall()
    out: list[str] = []
    for row in rows:
        sym = str(row["symbol"] or "").strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out


def fetch_recent_backtests(db_path: Optional[str], limit: int = 20) -> list[dict[str, Any]]:
    """backtest_results 최신순 limit개."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    lim = max(1, int(limit))
    sql = (
        "SELECT symbol, strategy_name, start_date, end_date, final_value, "
        "total_return_pct, trade_count, win_rate, max_drawdown_pct "
        "FROM backtest_results ORDER BY created_at DESC, id DESC LIMIT ?"
    )
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_backtest_results_schema(conn)
        cur = conn.execute(sql, (lim,))
        rows = cur.fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def fetch_latest_paper_snapshot(db_path: Optional[str]) -> dict[str, Any] | None:
    """paper_account_snapshots 최신 1건 또는 None."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    sql = (
        "SELECT id, created_at, snapshot_date, cash, equity, positions_value, "
        "last_action, reason, raw_json FROM paper_account_snapshots "
        "ORDER BY id DESC LIMIT 1"
    )
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql)
        row = cur.fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def fetch_recent_paper_trades(db_path: Optional[str], limit: int = 20) -> list[dict[str, Any]]:
    """paper_trades 최신순 limit개."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    lim = max(1, int(limit))
    sql = (
        "SELECT symbol, trade_date, side, price, quantity, cash_before, cash_after, reason "
        "FROM paper_trades ORDER BY id DESC LIMIT ?"
    )
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, (lim,))
        rows = cur.fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def upsert_kgsqs_signal(
    db_path: Optional[str],
    symbol: str,
    signal_date: str,
    total_score: float,
    action: str,
    confidence: float,
    reason: str,
    raw: dict,
) -> dict[str, int]:
    """K-GSQS 신호를 signals 테이블에 upsert.

    같은 (symbol, signal_date, 'k_gsqs_v1') 행이 이미 있으면
    신규 total_score가 더 높을 때만 덮어쓴다 (당일 최고점 유지).
    """
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    sql = (
        "INSERT INTO signals "
        "(symbol, signal_date, strategy_name, technical_score, final_score, action, "
        "confidence, reason, raw_json) "
        "VALUES (?, ?, 'k_gsqs_v1', ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(symbol, signal_date, strategy_name) DO UPDATE SET "
        "  technical_score = CASE WHEN excluded.final_score > final_score "
        "                    THEN excluded.technical_score ELSE technical_score END, "
        "  final_score  = MAX(final_score, excluded.final_score), "
        "  action       = CASE WHEN excluded.final_score > final_score "
        "                 THEN excluded.action    ELSE action       END, "
        "  confidence   = CASE WHEN excluded.final_score > final_score "
        "                 THEN excluded.confidence ELSE confidence  END, "
        "  reason       = CASE WHEN excluded.final_score > final_score "
        "                 THEN excluded.reason    ELSE reason       END, "
        "  raw_json     = CASE WHEN excluded.final_score > final_score "
        "                 THEN excluded.raw_json  ELSE raw_json     END"
    )
    try:
        raw_json = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"inserted": 0, "updated": 0, "failed": 1}
    try:
        with sqlite3.connect(str(resolved)) as conn:
            conn.row_factory = sqlite3.Row
            _migrate_signals_schema(conn)
            cur = conn.execute(
                sql,
                (symbol, signal_date, total_score, total_score,
                 action, confidence, reason, raw_json),
            )
            conn.commit()
            # rowcount==1 → INSERT, rowcount==0 → UPDATE(no-op because score lower)
            changed = cur.rowcount
    except sqlite3.Error as exc:
        return {"inserted": 0, "updated": 0, "failed": 1, "error": str(exc)}
    return {"inserted": 1 if changed == 1 else 0, "updated": 1 if changed == 0 else 0, "failed": 0}


def insert_signal_result(
    db_path: Optional[str],
    result: "SignalResult",
) -> dict[str, int]:
    """signals에 한 건 삽입. (symbol, signal_date, strategy_name) UNIQUE 충돌 시 skipped."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    sql = (
        "INSERT OR IGNORE INTO signals "
        "(symbol, signal_date, strategy_name, technical_score, news_score, macro_score, "
        "final_score, action, confidence, reason, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    inserted = 0
    skipped = 0
    failed = 0
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_signals_schema(conn)
        try:
            raw_json = json.dumps(result.raw, ensure_ascii=False)
        except (TypeError, ValueError):
            return {"inserted": 0, "skipped": 0, "failed": 1}
        try:
            cur = conn.execute(
                sql,
                (
                    result.symbol,
                    result.signal_date,
                    result.strategy_name,
                    result.technical_score,
                    result.news_score,
                    result.macro_score,
                    result.final_score,
                    result.action,
                    result.confidence,
                    result.reason,
                    raw_json,
                ),
            )
        except sqlite3.Error:
            conn.rollback()
            return {"inserted": 0, "skipped": 0, "failed": 1}
        if cur.rowcount == 1:
            inserted = 1
        else:
            skipped = 1
        conn.commit()
    return {"inserted": inserted, "skipped": skipped, "failed": failed}


def insert_backtest_result(
    db_path: Optional[str],
    result: "BacktestResult",
) -> dict[str, int]:
    """backtest_results에 한 건 삽입. (symbol, strategy_name, start_date, end_date) UNIQUE 충돌 시 skipped."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    sql = (
        "INSERT OR IGNORE INTO backtest_results "
        "(symbol, strategy_name, start_date, end_date, initial_cash, final_value, "
        "total_return_pct, trade_count, win_rate, max_drawdown_pct, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    inserted = 0
    skipped = 0
    failed = 0
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_backtest_results_schema(conn)
        try:
            raw_json = json.dumps(result.raw, ensure_ascii=False)
        except (TypeError, ValueError):
            return {"inserted": 0, "skipped": 0, "failed": 1}
        try:
            cur = conn.execute(
                sql,
                (
                    result.symbol,
                    result.strategy_name,
                    result.start_date,
                    result.end_date,
                    result.initial_cash,
                    result.final_value,
                    result.total_return_pct,
                    int(result.trade_count),
                    result.win_rate,
                    result.max_drawdown_pct,
                    raw_json,
                ),
            )
        except sqlite3.Error:
            conn.rollback()
            return {"inserted": 0, "skipped": 0, "failed": 1}
        if cur.rowcount == 1:
            inserted = 1
        else:
            skipped = 1
        conn.commit()
    return {"inserted": inserted, "skipped": skipped, "failed": failed}


def get_paper_cash(db_path: Optional[str], initial_cash: float = 10000.0) -> float:
    """가상 현금: 최신 `paper_account_snapshots.cash`, 없으면 initial_cash."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    with sqlite3.connect(str(resolved)) as conn:
        cur = conn.execute(
            "SELECT cash FROM paper_account_snapshots ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        return float(initial_cash)
    return float(row[0])


def get_paper_positions(db_path: Optional[str]) -> list[dict]:
    """paper_positions 전체를 dict 리스트로 반환한다."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT symbol, quantity, avg_price, created_at, updated_at "
            "FROM paper_positions ORDER BY symbol ASC"
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_paper_position(
    db_path: Optional[str],
    position: Mapping[str, Any],
) -> None:
    """종목별 포지션을 삽입하거나 갱신한다 (UNIQUE symbol)."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    sym = str(position["symbol"]).strip().upper()
    qty = int(position["quantity"])
    avg = float(position["avg_price"])
    sql = (
        "INSERT INTO paper_positions (symbol, quantity, avg_price, created_at, updated_at) "
        "VALUES (?, ?, ?, datetime('now'), datetime('now')) "
        "ON CONFLICT(symbol) DO UPDATE SET "
        "quantity = excluded.quantity, "
        "avg_price = excluded.avg_price, "
        "updated_at = datetime('now')"
    )
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute(sql, (sym, qty, avg))
        conn.commit()


def clear_paper_position(db_path: Optional[str], symbol: str) -> None:
    """해당 심볼의 모의 포지션을 제거한다."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    sym = symbol.strip().upper()
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute("DELETE FROM paper_positions WHERE symbol = ?", (sym,))
        conn.commit()


def insert_paper_trade(db_path: Optional[str], trade: "PaperTrade") -> None:
    """paper_trades에 한 건 기록한다."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    raw_json = json.dumps(trade.raw, ensure_ascii=False)
    sql = (
        "INSERT INTO paper_trades (symbol, trade_date, side, price, quantity, "
        "cash_before, cash_after, reason, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute(
            sql,
            (
                trade.symbol,
                trade.trade_date,
                trade.side,
                trade.price,
                trade.quantity,
                trade.cash_before,
                trade.cash_after,
                trade.reason,
                raw_json,
            ),
        )
        conn.commit()


def insert_paper_account_snapshot(
    db_path: Optional[str],
    snapshot: "PaperAccountSnapshot",
) -> None:
    """paper_account_snapshots에 한 건 기록한다."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    raw_out = {
        **snapshot.raw,
        "positions": [
            {
                "symbol": p.symbol,
                "quantity": p.quantity,
                "avg_price": p.avg_price,
                "last_price": p.last_price,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pnl_pct": p.unrealized_pnl_pct,
            }
            for p in snapshot.positions
        ],
    }
    raw_json = json.dumps(raw_out, ensure_ascii=False)
    sql = (
        "INSERT INTO paper_account_snapshots (snapshot_date, cash, equity, "
        "positions_value, last_action, reason, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute(
            sql,
            (
                snapshot.snapshot_date,
                snapshot.cash,
                snapshot.equity,
                snapshot.positions_value,
                snapshot.last_action,
                snapshot.reason,
                raw_json,
            ),
        )
        conn.commit()


def insert_collection_run(
    db_path: Optional[str],
    source: str,
    status: str,
    item_count: int,
    error_message: Optional[str] = None,
    *,
    collector_type_override: Optional[str] = None,
) -> None:
    """collection_runs에 한 건 기록한다."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    collector_type = (
        collector_type_override
        if collector_type_override is not None
        else f"news_rss:{source}"
    )
    with sqlite3.connect(str(resolved)) as conn:
        conn.execute(
            "INSERT INTO collection_runs (collector_type, status, records_count, message) "
            "VALUES (?, ?, ?, ?)",
            (collector_type, status, int(item_count), error_message),
        )
        conn.commit()


def _migrate_real_account_tables(conn: sqlite3.Connection) -> None:
    """[실전-6] 실계좌 테이블 (paper_* 와 분리). 기존 DB에도 CREATE IF NOT EXISTS 적용."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS real_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_time TEXT NOT NULL,
            broker TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            avg_price REAL,
            current_price REAL,
            market_value REAL,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS real_account_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_time TEXT NOT NULL,
            broker TEXT NOT NULL,
            cash REAL,
            withdrawable_cash REAL,
            total_market_value REAL,
            total_equity REAL,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS real_order_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            broker TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            limit_price REAL,
            estimated_order_value REAL,
            status TEXT,
            order_id TEXT,
            audit_path TEXT,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS real_fill_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            broker TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT,
            order_id TEXT,
            fill_id TEXT,
            fill_quantity INTEGER,
            fill_price REAL,
            fill_value REAL,
            fill_timestamp TEXT,
            raw_json TEXT
        );
        """
    )


def save_real_positions(
    db_path: Optional[str],
    snapshot_time: str,
    broker: str,
    positions: list[dict[str, Any]],
) -> int:
    """실계좌 포지션 스냅샷 행 삽입. 반환: 삽입 건수."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    n = 0
    sql = (
        "INSERT INTO real_positions (snapshot_time, broker, symbol, quantity, "
        "avg_price, current_price, market_value, raw_json) VALUES (?,?,?,?,?,?,?,?)"
    )
    with sqlite3.connect(str(resolved)) as conn:
        _migrate_real_account_tables(conn)
        for p in positions:
            sym = str(p.get("symbol") or "").strip()
            if not sym:
                continue
            qty = int(p.get("quantity") or 0)
            if qty <= 0:
                continue
            raw = p.get("raw") if isinstance(p.get("raw"), dict) else {}
            try:
                raw_s = json.dumps(raw, ensure_ascii=False)
            except (TypeError, ValueError):
                raw_s = "{}"
            conn.execute(
                sql,
                (
                    snapshot_time,
                    broker,
                    sym,
                    qty,
                    p.get("avg_price"),
                    p.get("current_price"),
                    p.get("market_value"),
                    raw_s,
                ),
            )
            n += 1
        conn.commit()
    return n


def save_real_account_snapshot(
    db_path: Optional[str],
    snapshot_time: str,
    broker: str,
    *,
    cash: float | None,
    withdrawable_cash: float | None,
    total_market_value: float | None,
    total_equity: float | None,
    raw_payload: dict[str, Any],
) -> int:
    """실계좌 계좌 스냅샷 1행 삽입. 반환: 1."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    try:
        raw_s = json.dumps(raw_payload, ensure_ascii=False)
    except (TypeError, ValueError):
        raw_s = "{}"
    with sqlite3.connect(str(resolved)) as conn:
        _migrate_real_account_tables(conn)
        conn.execute(
            """
            INSERT INTO real_account_snapshots (
                snapshot_time, broker, cash, withdrawable_cash,
                total_market_value, total_equity, raw_json
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                snapshot_time,
                broker,
                cash,
                withdrawable_cash,
                total_market_value,
                total_equity,
                raw_s,
            ),
        )
        conn.commit()
    return 1


def load_latest_real_positions(
    db_path: Optional[str] = None,
    *,
    broker: str = "kis",
) -> list[dict[str, Any]]:
    """동일 broker의 최신 계좌 스냅샷 시각에 해당하는 포지션 행 목록."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return []
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_real_account_tables(conn)
        cur = conn.execute(
            "SELECT snapshot_time FROM real_account_snapshots WHERE broker = ? ORDER BY snapshot_time DESC, id DESC LIMIT 1",
            (broker,),
        )
        row = cur.fetchone()
        if row and row["snapshot_time"] is not None:
            mx = str(row["snapshot_time"])
        else:
            # Backward compatibility for older tests/DBs that saved positions before account snapshots existed.
            cur = conn.execute(
                "SELECT MAX(snapshot_time) AS mx FROM real_positions WHERE broker = ?",
                (broker,),
            )
            row = cur.fetchone()
            if not row or row["mx"] is None:
                return []
            mx = str(row["mx"])
        cur2 = conn.execute(
            """
            SELECT snapshot_time, broker, symbol, quantity, avg_price, current_price,
                   market_value, raw_json
            FROM real_positions
            WHERE broker = ? AND snapshot_time = ?
            ORDER BY symbol
            """,
            (broker, mx),
        )
        out: list[dict[str, Any]] = []
        for r in cur2.fetchall():
            raw: Any = {}
            if r["raw_json"]:
                try:
                    raw = json.loads(r["raw_json"])
                except json.JSONDecodeError:
                    raw = {"_parse_error": True}
            out.append(
                {
                    "snapshot_time": r["snapshot_time"],
                    "broker": r["broker"],
                    "symbol": r["symbol"],
                    "quantity": int(r["quantity"] or 0),
                    "avg_price": r["avg_price"],
                    "current_price": r["current_price"],
                    "market_value": r["market_value"],
                    "raw": raw,
                }
            )
        return out


def load_latest_real_account_snapshot(
    db_path: Optional[str] = None,
    *,
    broker: str = "kis",
) -> dict[str, Any] | None:
    """가장 최근 `real_account_snapshots` 1건."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return None
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_real_account_tables(conn)
        cur = conn.execute(
            """
            SELECT snapshot_time, broker, cash, withdrawable_cash,
                   total_market_value, total_equity, raw_json
            FROM real_account_snapshots
            WHERE broker = ?
            ORDER BY snapshot_time DESC, id DESC
            LIMIT 1
            """,
            (broker,),
        )
        r = cur.fetchone()
        if not r:
            return None
        raw: Any = {}
        if r["raw_json"]:
            try:
                raw = json.loads(r["raw_json"])
            except json.JSONDecodeError:
                raw = {}
        return {
            "snapshot_time": r["snapshot_time"],
            "broker": r["broker"],
            "cash": r["cash"],
            "withdrawable_cash": r["withdrawable_cash"],
            "total_market_value": r["total_market_value"],
            "total_equity": r["total_equity"],
            "raw": raw,
        }


def save_real_order_history(
    db_path: Optional[str],
    *,
    broker: str,
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float | None = None,
    estimated_order_value: float | None = None,
    status: str | None = None,
    order_id: str | None = None,
    audit_path: str | None = None,
    raw_payload: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> int:
    """실주문 이력 1건 저장. 반환: 삽입 행 id."""
    from datetime import datetime

    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    ts = created_at or datetime.now().isoformat(timespec="seconds")
    raw_s = "{}"
    if raw_payload is not None:
        try:
            raw_s = json.dumps(raw_payload, ensure_ascii=False)
        except (TypeError, ValueError):
            raw_s = "{}"
    with sqlite3.connect(str(resolved)) as conn:
        _migrate_real_account_tables(conn)
        cur = conn.execute(
            """
            INSERT INTO real_order_history (
                created_at, broker, symbol, side, quantity, limit_price,
                estimated_order_value, status, order_id, audit_path, raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                broker,
                symbol,
                side,
                int(quantity),
                limit_price,
                estimated_order_value,
                status,
                order_id,
                audit_path,
                raw_s,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def load_recent_real_orders(
    db_path: Optional[str] = None,
    *,
    broker: str = "kis",
    symbol: str | None = None,
    since_minutes: int = 30,
) -> list[dict[str, Any]]:
    """최근 N분 실주문 이력 (신규순)."""
    from datetime import datetime, timedelta

    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return []
    since_dt = datetime.now() - timedelta(minutes=max(0, since_minutes))
    since_iso = since_dt.isoformat(timespec="seconds")
    sym = (symbol or "").strip()
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_real_account_tables(conn)
        if sym:
            cur = conn.execute(
                """
                SELECT created_at, broker, symbol, side, quantity, limit_price,
                       estimated_order_value, status, order_id, audit_path, raw_json
                FROM real_order_history
                WHERE broker = ? AND symbol = ? AND created_at >= ?
                ORDER BY created_at DESC, id DESC
                """,
                (broker, sym, since_iso),
            )
        else:
            cur = conn.execute(
                """
                SELECT created_at, broker, symbol, side, quantity, limit_price,
                       estimated_order_value, status, order_id, audit_path, raw_json
                FROM real_order_history
                WHERE broker = ? AND created_at >= ?
                ORDER BY created_at DESC, id DESC
                """,
                (broker, since_iso),
            )
        out: list[dict[str, Any]] = []
        for r in cur.fetchall():
            raw: Any = {}
            if r["raw_json"]:
                try:
                    raw = json.loads(r["raw_json"])
                except json.JSONDecodeError:
                    raw = {"_parse_error": True}
            out.append(
                {
                    "created_at": r["created_at"],
                    "broker": r["broker"],
                    "symbol": r["symbol"],
                    "side": r["side"],
                    "quantity": int(r["quantity"] or 0),
                    "limit_price": r["limit_price"],
                    "estimated_order_value": r["estimated_order_value"],
                    "status": r["status"],
                    "order_id": r["order_id"],
                    "audit_path": r["audit_path"],
                    "raw": raw,
                }
            )
        return out


def load_latest_real_snapshot_time(
    db_path: Optional[str] = None,
    *,
    broker: str = "kis",
) -> str | None:
    """가장 최근 `real_account_snapshots.snapshot_time`."""
    snap = load_latest_real_account_snapshot(db_path, broker=broker)
    if not snap:
        return None
    st = snap.get("snapshot_time")
    return str(st) if st else None


def _fill_dedupe_exists(
    conn: sqlite3.Connection,
    *,
    broker: str,
    fill_id: str | None,
    order_id: str | None,
    fill_timestamp: str | None,
    fill_quantity: int,
    fill_price: float | None,
) -> bool:
    """fill_id 또는 synthetic key 기준 중복 여부."""
    if fill_id:
        cur = conn.execute(
            "SELECT 1 FROM real_fill_history WHERE broker = ? AND fill_id = ? LIMIT 1",
            (broker, fill_id),
        )
        if cur.fetchone():
            return True
    if order_id and fill_timestamp:
        cur = conn.execute(
            """
            SELECT 1 FROM real_fill_history
            WHERE broker = ? AND order_id = ? AND fill_timestamp = ?
              AND fill_quantity = ? AND (fill_price = ? OR (fill_price IS NULL AND ? IS NULL))
            LIMIT 1
            """,
            (broker, order_id, fill_timestamp, fill_quantity, fill_price, fill_price),
        )
        return cur.fetchone() is not None
    return False


def save_real_fill(
    db_path: Optional[str],
    *,
    broker: str,
    symbol: str,
    fill_quantity: int,
    side: str | None = None,
    order_id: str | None = None,
    fill_id: str | None = None,
    fill_price: float | None = None,
    fill_value: float | None = None,
    fill_timestamp: str | None = None,
    raw_payload: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> int:
    """
    실체결 1건 저장. 중복이면 0 반환, 신규면 row id.

    dedupe: `fill_id` 또는 (order_id + fill_timestamp + qty + price).
    """
    from datetime import datetime

    if fill_quantity <= 0:
        return 0
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    _ensure_parent_dir(resolved)
    ts = created_at or datetime.now().isoformat(timespec="seconds")
    fv = fill_value
    if fv is None and fill_price is not None:
        fv = float(fill_price) * int(fill_quantity)
    raw_s = "{}"
    if raw_payload is not None:
        try:
            raw_s = json.dumps(raw_payload, ensure_ascii=False)
        except (TypeError, ValueError):
            raw_s = "{}"
    with sqlite3.connect(str(resolved)) as conn:
        _migrate_real_account_tables(conn)
        if _fill_dedupe_exists(
            conn,
            broker=broker,
            fill_id=fill_id,
            order_id=order_id,
            fill_timestamp=fill_timestamp,
            fill_quantity=int(fill_quantity),
            fill_price=fill_price,
        ):
            return 0
        cur = conn.execute(
            """
            INSERT INTO real_fill_history (
                created_at, broker, symbol, side, order_id, fill_id,
                fill_quantity, fill_price, fill_value, fill_timestamp, raw_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                broker,
                symbol,
                side,
                order_id,
                fill_id,
                int(fill_quantity),
                fill_price,
                fv,
                fill_timestamp,
                raw_s,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def load_real_fills_by_order(
    db_path: Optional[str],
    *,
    broker: str = "kis",
    order_id: str,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """주문번호별 체결 이력."""
    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return []
    sym = (symbol or "").strip()
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_real_account_tables(conn)
        if sym:
            cur = conn.execute(
                """
                SELECT created_at, broker, symbol, side, order_id, fill_id,
                       fill_quantity, fill_price, fill_value, fill_timestamp, raw_json
                FROM real_fill_history
                WHERE broker = ? AND order_id = ? AND symbol = ?
                ORDER BY fill_timestamp ASC, id ASC
                """,
                (broker, order_id, sym),
            )
        else:
            cur = conn.execute(
                """
                SELECT created_at, broker, symbol, side, order_id, fill_id,
                       fill_quantity, fill_price, fill_value, fill_timestamp, raw_json
                FROM real_fill_history
                WHERE broker = ? AND order_id = ?
                ORDER BY fill_timestamp ASC, id ASC
                """,
                (broker, order_id),
            )
        return _rows_to_fill_dicts(cur.fetchall())


def load_recent_real_fills(
    db_path: Optional[str] = None,
    *,
    broker: str = "kis",
    symbol: str | None = None,
    since_minutes: int = 1440,
) -> list[dict[str, Any]]:
    """최근 N분 체결 이력."""
    from datetime import datetime, timedelta

    path = db_path or load_settings().db_path
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return []
    since_dt = datetime.now() - timedelta(minutes=max(0, since_minutes))
    since_iso = since_dt.isoformat(timespec="seconds")
    sym = (symbol or "").strip()
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_real_account_tables(conn)
        if sym:
            cur = conn.execute(
                """
                SELECT created_at, broker, symbol, side, order_id, fill_id,
                       fill_quantity, fill_price, fill_value, fill_timestamp, raw_json
                FROM real_fill_history
                WHERE broker = ? AND symbol = ? AND created_at >= ?
                ORDER BY created_at DESC, id DESC
                """,
                (broker, sym, since_iso),
            )
        else:
            cur = conn.execute(
                """
                SELECT created_at, broker, symbol, side, order_id, fill_id,
                       fill_quantity, fill_price, fill_value, fill_timestamp, raw_json
                FROM real_fill_history
                WHERE broker = ? AND created_at >= ?
                ORDER BY created_at DESC, id DESC
                """,
                (broker, since_iso),
            )
        return _rows_to_fill_dicts(cur.fetchall())


def _rows_to_fill_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        raw: Any = {}
        if r["raw_json"]:
            try:
                raw = json.loads(r["raw_json"])
            except json.JSONDecodeError:
                raw = {"_parse_error": True}
        out.append(
            {
                "created_at": r["created_at"],
                "broker": r["broker"],
                "symbol": r["symbol"],
                "side": r["side"],
                "order_id": r["order_id"],
                "fill_id": r["fill_id"],
                "fill_quantity": int(r["fill_quantity"] or 0),
                "fill_price": r["fill_price"],
                "fill_value": r["fill_value"],
                "fill_timestamp": r["fill_timestamp"],
                "raw": raw,
            }
        )
    return out


def aggregate_fill_summary(
    db_path: Optional[str],
    *,
    broker: str = "kis",
    order_id: str,
    symbol: str | None = None,
    order_quantity: int | None = None,
) -> dict[str, Any]:
    """DB 체결 + 선택 주문 수량으로 집계."""
    from deepsignal.live_trading.fill_tracker import aggregate_order_fills

    fills = load_real_fills_by_order(db_path, broker=broker, order_id=order_id, symbol=symbol)
    oq = order_quantity
    if oq is None:
        for o in load_recent_real_orders(db_path, broker=broker, symbol=symbol, since_minutes=60 * 24 * 7):
            if str(o.get("order_id") or "") == str(order_id):
                oq = int(o.get("quantity") or 0)
                break
    return aggregate_order_fills(
        fills,
        order_quantity=oq,
        order_id=order_id,
        symbol=symbol,
    )


def list_user_tables(db_path: Optional[str] = None) -> set[str]:
    """sqlite_master 기준 사용자 테이블 이름 집합 (검증·테스트용)."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return {row[0] for row in cur.fetchall()}


def assert_core_schema_present(db_path: Optional[str] = None) -> None:
    tables = list_user_tables(db_path)
    missing = sorted(_EXPECTED_TABLES - tables)
    if missing:
        raise RuntimeError(f"스키마 불완전: 누락 테이블 {missing}")
