-- DeepSignal 초기 SQLite 스키마 (마이그레이션 전략은 추후 합의)

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT NOT NULL,
    source_hash TEXT NOT NULL UNIQUE,
    url TEXT,
    title TEXT,
    body_text TEXT,
    published_at TEXT,
    summary TEXT,
    symbol TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS market_prices (
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
);

CREATE TABLE IF NOT EXISTS economic_indicators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    indicator_name TEXT NOT NULL,
    indicator_date TEXT NOT NULL,
    value REAL,
    source TEXT NOT NULL DEFAULT 'yfinance',
    raw_json TEXT,
    UNIQUE (indicator_name, indicator_date, source)
);

CREATE TABLE IF NOT EXISTS signals (
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
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    mode TEXT NOT NULL DEFAULT 'paper',
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL,
    price REAL,
    external_ref TEXT
);

CREATE TABLE IF NOT EXISTS backtest_results (
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
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    symbol TEXT NOT NULL UNIQUE,
    quantity INTEGER NOT NULL,
    avg_price REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    symbol TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    cash_before REAL NOT NULL,
    cash_after REAL NOT NULL,
    reason TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS paper_account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    snapshot_date TEXT NOT NULL,
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    positions_value REAL NOT NULL,
    last_action TEXT NOT NULL,
    reason TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    collector_type TEXT NOT NULL,
    status TEXT NOT NULL,
    records_count INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    run_hash TEXT UNIQUE
);

-- [실전-6] 실계좌 전용 (paper_* 와 분리; 모의·백테스트와 혼합 금지)
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

-- [실전-7] 실주문 이력 (paper_* 와 분리)
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

-- [실전-8] 실체결 이력 (paper_* 와 분리)
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
