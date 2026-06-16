"""뉴스 촉매 파이프라인 — 종목 ↔ 한국 뉴스 매칭 → LLM 분석 → news_score 저장.

흐름:
  1) 대상 종목 + 한국명 확보 (config 맵 + 당일 급등주 스캔 raw_json 이름)
  2) 각 종목명이 언급된 최근 한국 뉴스(news_items)를 찾음
  3) LLM 촉매 분석기(llm_news_analyzer)로 news_score 산출
  4) news_scores 테이블에 upsert (이후 신호/게이트가 읽음)

env: NEWS_LLM_ENABLED, OPENAI_API_KEY (분석기 쪽). 무키/비활성이면 분석은 skip.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from deepsignal.ai.llm_news_analyzer import analyze_symbol_news, llm_news_enabled

_KST = timezone(timedelta(hours=9))
_KR_SOURCES = ("yna_market", "yna_economy", "hankyung_finance", "mk_stock")

_DDL = """
CREATE TABLE IF NOT EXISTS news_scores (
  symbol TEXT NOT NULL,
  scored_date TEXT NOT NULL,
  scored_at TEXT NOT NULL,
  news_score REAL,
  polarity REAL,
  strength REAL,
  catalyst_type TEXT,
  half_life_hours REAL,
  confidence REAL,
  news_count INTEGER,
  reason TEXT,
  model TEXT,
  PRIMARY KEY (symbol, scored_date)
)
"""


def _db(db_path: str | None) -> sqlite3.Connection:
    from deepsignal.config.settings import load_settings
    p = db_path or load_settings().db_path
    conn = sqlite3.connect(str(Path(p).expanduser().resolve()))
    conn.execute(_DDL)
    return conn


def load_name_map(conn: sqlite3.Connection, *, project_root: str | Path | None = None) -> dict[str, str]:
    """{종목코드: 한국명}. config 맵 + 당일 급등주 스캔(raw_json) 이름 병합."""
    out: dict[str, str] = {}
    # 1) config/symbol_name_map.json
    try:
        root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        p = root / "config" / "symbol_name_map.json"
        if p.is_file():
            for k, v in (json.loads(p.read_text(encoding="utf-8")) or {}).items():
                if str(k).isdigit() and v:
                    out[str(k).zfill(6)] = str(v)
    except Exception:
        pass
    # 2) 당일 kr_movers_v1 신호의 raw_json 이름 (실제 움직이는 종목)
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    try:
        rows = conn.execute(
            "SELECT symbol, raw_json FROM signals WHERE strategy_name='kr_movers_v1' AND signal_date=?",
            (today,),
        ).fetchall()
        for sym, raw in rows:
            try:
                name = (json.loads(raw or "{}") or {}).get("name")
            except Exception:
                name = None
            if name:
                out[str(sym).zfill(6)] = str(name)
    except Exception:
        pass
    return out


def find_matching_news(conn: sqlite3.Connection, name: str, *, hours: int = 48, limit: int = 8) -> list[dict[str, Any]]:
    """종목명이 제목/요약에 포함된 최근 한국 뉴스. (간단 substring 매칭)"""
    if not name or len(name) < 2:
        return []
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    src_ph = ",".join("?" for _ in _KR_SOURCES)
    q = (
        f"SELECT title, summary, body_text, published_at, created_at FROM news_items "
        f"WHERE source IN ({src_ph}) AND created_at >= ? "
        f"AND (title LIKE ? OR summary LIKE ?) ORDER BY created_at DESC LIMIT ?"
    )
    like = f"%{name}%"
    try:
        cur = conn.execute(q, (*_KR_SOURCES, since, like, like, limit))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []


def run_news_catalyst_scan(
    *, db_path: str | None = None, symbols: list[str] | None = None,
    project_root: str | Path | None = None, max_symbols: int = 40,
) -> dict[str, Any]:
    """대상 종목별 뉴스 매칭 → LLM 분석 → news_scores upsert. 요약 dict 반환."""
    conn = _db(db_path)
    name_map = load_name_map(conn, project_root=project_root)
    if symbols:
        targets = [(str(s).zfill(6), name_map.get(str(s).zfill(6))) for s in symbols]
    else:
        targets = list(name_map.items())
    targets = [(s, n) for s, n in targets if n][:max_symbols]

    today = datetime.now(_KST).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).isoformat()
    scored, matched, skipped = 0, 0, 0
    results: list[dict[str, Any]] = []
    for sym, name in targets:
        news = find_matching_news(conn, name)
        if not news:
            skipped += 1
            continue
        matched += 1
        res = analyze_symbol_news(sym, news, name=name)
        if res is None:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO news_scores(symbol,scored_date,scored_at,news_score,polarity,"
            "strength,catalyst_type,half_life_hours,confidence,news_count,reason,model) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, today, now_iso, res.news_score, res.polarity, res.strength, res.catalyst_type,
             res.half_life_hours, res.confidence, res.news_count, res.reason, res.model),
        )
        scored += 1
        results.append({"symbol": sym, "name": name, "news_score": res.news_score,
                        "polarity": res.polarity, "catalyst": res.catalyst_type, "reason": res.reason})
    conn.commit()
    conn.close()
    results.sort(key=lambda r: r["news_score"] or 50, reverse=True)
    return {
        "targets": len(targets), "matched_news": matched, "scored": scored, "skipped_no_news": skipped,
        "llm_enabled": llm_news_enabled(), "top": results[:15],
    }


def latest_news_score(symbol: str, *, db_path: str | None = None, max_age_hours: int = 24) -> dict[str, Any] | None:
    """신호 빌더/게이트가 읽을 최신 news_score (만료 지나면 None)."""
    conn = _db(db_path)
    try:
        row = conn.execute(
            "SELECT news_score,polarity,strength,catalyst_type,confidence,reason,scored_at "
            "FROM news_scores WHERE symbol=? ORDER BY scored_at DESC LIMIT 1",
            (str(symbol).zfill(6),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(row[6])
        if age > timedelta(hours=max_age_hours):
            return None
    except Exception:
        pass
    return {"news_score": row[0], "polarity": row[1], "strength": row[2],
            "catalyst_type": row[3], "confidence": row[4], "reason": row[5]}
