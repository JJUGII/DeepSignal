"""로컬 통합 매매 장부 — 내 실제 체결을 정확히 기록·집계.

웹 API 뷰(불완전·혼란)가 아니라, 각 브로커의 *실제 체결*을 로컬 DB(trade_ledger)에
모은다. 매수/매도 1건씩 fill로 기록하고 FIFO로 실현손익을 계산한다.
"얼마 매수·얼마 매도·얼마 잃고·얼마 벌었나"를 내 자료로 정확히 남긴다.

소스:
  - 코인: outputs/crypto_trades.db (진입+청산 완결 거래)
  - 국내: KIS inquire-daily-ccld (get_order_status)
  - 해외: KIS 해외 기간손익(추후)
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))

_DDL = """
CREATE TABLE IF NOT EXISTS trade_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  asset_class TEXT NOT NULL,        -- domestic / overseas / crypto
  broker TEXT,
  symbol TEXT NOT NULL,
  name TEXT,
  side TEXT NOT NULL,               -- BUY / SELL
  quantity REAL NOT NULL,
  price REAL NOT NULL,
  value_krw REAL NOT NULL,          -- 체결금액(원화환산)
  fee_krw REAL DEFAULT 0,
  source TEXT,                      -- 출처 식별
  ext_id TEXT,                      -- 중복방지 키
  recorded_at TEXT,
  UNIQUE(source, ext_id)
)
"""


def _conn(db_path: str | None) -> sqlite3.Connection:
    from deepsignal.config.settings import load_settings
    p = db_path or str(Path(load_settings().db_path).parent / "trade_ledger.db")
    conn = sqlite3.connect(str(Path(p).expanduser().resolve()))
    conn.execute(_DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tl_sym ON trade_ledger(asset_class, symbol, ts)")
    return conn


def _ins(conn, *, ts, asset, broker, symbol, name, side, qty, price, value_krw, fee=0.0, source, ext_id):
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO trade_ledger(ts,asset_class,broker,symbol,name,side,quantity,price,"
            "value_krw,fee_krw,source,ext_id,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, asset, broker, str(symbol), name, side, float(qty), float(price), float(value_krw),
             float(fee or 0), source, ext_id, datetime.now(timezone.utc).isoformat()),
        )
        return cur.rowcount or 0
    except Exception:
        return 0


# ── 인제스천 ──────────────────────────────────────────────

def ingest_crypto(conn, *, crypto_db: str = "outputs/crypto_trades.db") -> int:
    """crypto_trades.db 완결거래 → 진입(BUY)+청산(SELL) fill로 기록."""
    p = Path(crypto_db)
    if not p.is_file():
        return 0
    src = sqlite3.connect(str(p.resolve()))
    n = 0
    try:
        rows = src.execute(
            "SELECT id,symbol,entry_time,entry_price,exit_time,exit_price,position_size,paper "
            "FROM crypto_trades WHERE paper=0 AND entry_price>0"
        ).fetchall()
    except Exception:
        rows = []
    src.close()
    for tid, sym, et, ep, xt, xp, qty, paper in rows:
        qty = float(qty or 0)
        if qty <= 0:
            continue
        n += _ins(conn, ts=et, asset="crypto", broker="upbit", symbol=sym, name=sym, side="BUY",
                  qty=qty, price=float(ep), value_krw=qty * float(ep), source="crypto_trades", ext_id=f"{tid}_buy")
        if xt and xp:
            n += _ins(conn, ts=xt, asset="crypto", broker="upbit", symbol=sym, name=sym, side="SELL",
                      qty=qty, price=float(xp), value_krw=qty * float(xp), source="crypto_trades", ext_id=f"{tid}_sell")
    conn.commit()
    return n


def ingest_kis_domestic(conn, *, days: int = 30) -> int:
    """KIS 국내 일별 체결조회 → fill 기록."""
    try:
        import os
        for l in open(".env"):
            if l.strip() and "=" in l and not l.startswith("#"):
                k, v = l.strip().split("=", 1)
                os.environ.setdefault(k, v)
        from datetime import timedelta
        from deepsignal.live_trading.kis_config import load_kis_config_from_env
        from deepsignal.live_trading.broker.kis_broker import KISBroker
        b = KISBroker(load_kis_config_from_env(load_dotenv_file=False))
        end = datetime.now(timezone(timedelta(hours=9)))
        start = end - timedelta(days=days)
        fills = b.get_order_status(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    except Exception:
        return 0
    n = 0
    for f in fills or []:
        raw = f.raw if isinstance(getattr(f, "raw", None), dict) else {}
        # 체결수량/체결단가 (KIS output1 필드)
        qty = float(raw.get("tot_ccld_qty") or raw.get("ccld_qty") or getattr(f, "filled_quantity", 0) or 0)
        price = float(raw.get("avg_prvs") or raw.get("ccld_prvs") or getattr(f, "avg_fill_price", 0) or 0)
        if qty <= 0 or price <= 0:
            continue
        sd = str(raw.get("sll_buy_dvsn_cd") or "")
        side = "SELL" if sd == "01" else ("BUY" if sd == "02" else (getattr(f, "side", "") or "").upper())
        if side not in ("BUY", "SELL"):
            continue
        sym = str(getattr(f, "symbol", "") or raw.get("pdno") or "")
        name = str(raw.get("prdt_name") or sym)
        odno = str(raw.get("odno") or raw.get("orgn_odno") or "")
        dt = str(raw.get("ord_dt") or raw.get("ccld_dt") or "")
        ts = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}" if len(dt) == 8 else datetime.now().isoformat()
        n += _ins(conn, ts=ts, asset="domestic", broker="kis", symbol=sym, name=name, side=side,
                  qty=qty, price=price, value_krw=qty * price, source="kis_ccld", ext_id=f"{odno}_{sym}_{side}_{qty}")
    conn.commit()
    return n


def ingest_kis_overseas(conn, *, days: int = 30) -> int:
    """KIS 해외 기간손익(TTTS3039R) → 청산거래별 매수+매도 leg 기록 (원화 환산값)."""
    try:
        import os as _os
        import requests as _rq
        for l in open(".env"):
            if l.strip() and "=" in l and not l.startswith("#"):
                k, v = l.strip().split("=", 1)
                _os.environ.setdefault(k, v)
        from datetime import timedelta
        from deepsignal.live_trading.kis_config import load_kis_config_from_env
        from deepsignal.live_trading.broker.kis_broker import KISBroker
        cfg = load_kis_config_from_env(load_dotenv_file=False)
        b = KISBroker(cfg)
        end = datetime.now(_KST)
        start = end - timedelta(days=days)
    except Exception:
        return 0
    # 페이징은 별도 try — 한 페이지 실패해도 그때까지 받은 행은 보존(예전: 전체 try라 0 반환).
    fk = nk = ""
    rows: list[dict] = []
    for _ in range(10):
        try:
            r = _rq.get(f"{cfg.base_url}/uapi/overseas-stock/v1/trading/inquire-period-profit",
                headers=b._inquire_headers("TTTS3039R"),
                params={"CANO": cfg.account_no.strip(), "ACNT_PRDT_CD": cfg.account_product_code.strip(),
                        "OVRS_EXCG_CD": "", "NATN_CD": "", "CRCY_CD": "USD",
                        "INQR_STRT_DT": start.strftime("%Y%m%d"), "INQR_END_DT": end.strftime("%Y%m%d"),
                        "PDNO": "", "WCRC_FRCR_DVSN_CD": "02", "CTX_AREA_FK200": fk, "CTX_AREA_NK200": nk},
                timeout=12)
            d = r.json()
        except Exception:
            break
        page = d.get("output1") or []
        rows.extend(page)
        new_nk = (d.get("ctx_area_nk200") or "").strip()
        if not page or not new_nk or new_nk == nk:  # 진전 없으면 중단(중복 호출 방지)
            break
        fk = (d.get("ctx_area_fk200") or "").strip()
        nk = new_nk
    n = 0
    for x in rows:
        try:
            qty = float(x.get("slcl_qty") or 0)
            buy_krw = float(x.get("frcr_pchs_amt1") or 0)
            sell_krw = float(x.get("frcr_sll_amt_smtl1") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or buy_krw <= 0:
            continue
        sym = str(x.get("ovrs_pdno") or "")
        name = str(x.get("ovrs_item_name") or sym)
        day = str(x.get("trad_day") or "")
        ts = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else datetime.now().isoformat()
        eid = f"ovs_{day}_{sym}_{qty}"
        n += _ins(conn, ts=ts, asset="overseas", broker="kis", symbol=sym, name=name, side="BUY",
                  qty=qty, price=buy_krw / qty, value_krw=buy_krw, source="kis_overseas", ext_id=eid + "_b")
        if sell_krw > 0:
            n += _ins(conn, ts=ts, asset="overseas", broker="kis", symbol=sym, name=name, side="SELL",
                      qty=qty, price=sell_krw / qty, value_krw=sell_krw, source="kis_overseas", ext_id=eid + "_s")
    conn.commit()
    return n


# ── 실현손익 (FIFO) ───────────────────────────────────────

def realized_pnl(conn) -> dict[str, Any]:
    """FIFO로 (asset,symbol)별 실현손익 계산. 매수/매도 합계·실현손익·승패 집계."""
    rows = conn.execute(
        "SELECT asset_class,symbol,ts,side,quantity,price,value_krw FROM trade_ledger ORDER BY ts"
    ).fetchall()
    fills = defaultdict(list)
    bought = sold = 0.0
    for asset, sym, ts, side, qty, price, val in rows:
        fills[(asset, sym)].append((ts, side, float(qty), float(price)))
        if side == "BUY":
            bought += float(val)
        else:
            sold += float(val)

    closed = []  # (asset, sym, pnl_krw)
    for (asset, sym), fs in fills.items():
        buyq: deque = deque()
        for ts, side, qty, price in fs:
            if side == "BUY":
                buyq.append([qty, price])
            else:  # SELL
                rem = qty
                while rem > 1e-12 and buyq:
                    lot = buyq[0]
                    take = min(rem, lot[0])
                    closed.append((asset, sym, take * (price - lot[1])))
                    lot[0] -= take
                    rem -= take
                    if lot[0] <= 1e-12:
                        buyq.popleft()
    realized_total = sum(p for _, _, p in closed)
    wins = [p for _, _, p in closed if p > 0]
    losses = [p for _, _, p in closed if p < 0]
    by_asset = defaultdict(float)
    for asset, _, p in closed:
        by_asset[asset] += p
    return {
        "bought_krw": round(bought),
        "sold_krw": round(sold),
        "realized_pnl_krw": round(realized_total),
        "gained_krw": round(sum(wins)),
        "lost_krw": round(sum(losses)),
        "closed_lots": len(closed),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "by_asset": {k: round(v) for k, v in by_asset.items()},
    }


def realized_trips(conn, *, asset_class: str | None = None) -> list[dict[str, Any]]:
    """FIFO로 매칭된 완결 round-trip 목록. 각 trip: 진입/청산 시각·가격·수량·손익%·보유분.

    감독관(승률·비대칭·churn) 입력용. asset_class 지정 시 해당 자산만.
    """
    from datetime import datetime as _dt

    def _parse(ts):
        try:
            return _dt.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return None

    q = ("SELECT asset_class,symbol,name,ts,side,quantity,price FROM trade_ledger "
         + ("WHERE asset_class=? " if asset_class else "")
         + "ORDER BY ts")
    rows = conn.execute(q, (asset_class,) if asset_class else ()).fetchall()
    fills: dict[tuple[str, str], list] = defaultdict(list)
    names: dict[tuple[str, str], str] = {}
    for asset, sym, name, ts, side, qty, price in rows:
        fills[(asset, sym)].append((ts, side, float(qty), float(price)))
        if name:
            names[(asset, sym)] = name

    trips: list[dict[str, Any]] = []
    for (asset, sym), fs in fills.items():
        buyq: deque = deque()  # [qty, price, ts]
        for ts, side, qty, price in fs:
            if side == "BUY":
                buyq.append([qty, price, ts])
            else:  # SELL — FIFO 매칭
                rem = qty
                while rem > 1e-12 and buyq:
                    lot = buyq[0]
                    take = min(rem, lot[0])
                    ep, et = lot[1], lot[2]
                    pnl_pct = (price - ep) / ep * 100.0 if ep > 0 else 0.0
                    e_dt, x_dt = _parse(et), _parse(ts)
                    hold_min = (x_dt - e_dt).total_seconds() / 60.0 if (e_dt and x_dt) else None
                    trips.append({
                        "asset": asset, "symbol": sym, "name": names.get((asset, sym), ""),
                        "entry_ts": et, "exit_ts": ts, "qty": take,
                        "entry_price": ep, "exit_price": price,
                        "pnl_krw": take * (price - ep), "pnl_pct": round(pnl_pct, 3),
                        "hold_minutes": round(hold_min, 1) if hold_min is not None else None,
                    })
                    lot[0] -= take
                    rem -= take
                    if lot[0] <= 1e-12:
                        buyq.popleft()
    trips.sort(key=lambda t: str(t["exit_ts"]))
    return trips


def sync_all(*, db_path: str | None = None) -> dict[str, Any]:
    conn = _conn(db_path)
    nc = ingest_crypto(conn)
    nd = ingest_kis_domestic(conn)
    no = ingest_kis_overseas(conn)
    summary = realized_pnl(conn)
    total = conn.execute("SELECT COUNT(*) FROM trade_ledger").fetchone()[0]
    conn.close()
    return {"ingested_crypto": nc, "ingested_domestic": nd, "ingested_overseas": no,
            "ledger_rows": total, **summary}
