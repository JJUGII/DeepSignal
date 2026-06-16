"""뉴스 촉매 러너 — 수집(RSS 한국 + DART) → LLM 촉매 분석 → news_scores 저장.

한 번 실행(launchd용) 또는 --loop. 모든 단계 비치명(실패해도 다음 단계 진행).

사용:
  ./.venv/bin/python scripts/news_catalyst_runner.py            # 1회
  ./.venv/bin/python scripts/news_catalyst_runner.py --loop 600 # 600초마다
env: NEWS_LLM_ENABLED=true, OPENAI_API_KEY (분석), DART_API_KEY (공시·선택)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# 파일로 실행 시 프로젝트 루트를 import 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_UA = {"User-Agent": "Mozilla/5.0 (DeepSignal news catalyst)"}
_KR_FEEDS = ("yna_market", "yna_economy", "hankyung_finance", "mk_stock")


def _db_path() -> str:
    from deepsignal.config.settings import load_settings
    return str(Path(load_settings().db_path).expanduser().resolve())


def collect_korean_rss(limit_per_feed: int = 40) -> int:
    """한국 RSS 피드 수집 → news_items. 저장 건수."""
    import feedparser

    from deepsignal.collector.news.rss_feeds import DEFAULT_RSS_FEEDS
    feeds = [(n, u) for n, u in DEFAULT_RSS_FEEDS if n in _KR_FEEDS]
    conn = sqlite3.connect(_db_path())
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for name, url in feeds:
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=10).read()
            parsed = feedparser.parse(data)
        except Exception as exc:
            print(f"  [RSS] {name} 실패: {exc}")
            continue
        for e in parsed.entries[:limit_per_feed]:
            title = getattr(e, "title", "")
            summ = (getattr(e, "summary", "") or "")[:500]
            link = getattr(e, "link", "")
            pub = getattr(e, "published", "") or ""
            h = hashlib.sha256((name + link + title).encode()).hexdigest()
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO news_items(created_at,source,source_hash,url,title,summary,"
                    "published_at,raw_json) VALUES(?,?,?,?,?,?,?,?)",
                    (now, name, h, link, title, summ, pub, json.dumps({"feed": name}, ensure_ascii=False)),
                )
                n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            except Exception:
                pass
    conn.commit()
    conn.close()
    return n


def run_once() -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    rss_n = collect_korean_rss()
    # DART 공시 (키 있을 때만)
    dart_n = 0
    try:
        from deepsignal.collector.news.dart_collector import dart_enabled, fetch_recent_disclosures, store_disclosures
        if dart_enabled():
            dart_n = store_disclosures(fetch_recent_disclosures(days=1))
    except Exception as exc:
        print(f"  [DART] 실패: {exc}")
    # 촉매 스캔
    from deepsignal.ai.news_catalyst_pipeline import run_news_catalyst_scan
    scan = run_news_catalyst_scan(max_symbols=40)
    print(f"[{ts}] 수집 RSS+{rss_n} DART+{dart_n} | 촉매 대상 {scan['targets']}·매칭 {scan['matched_news']}·채점 {scan['scored']}"
          f" (LLM={'on' if scan['llm_enabled'] else 'off'})")
    for x in scan["top"][:5]:
        print(f"    {x['symbol']} {x['name']}: score={x['news_score']} [{x['catalyst']}] {x['reason']}")
    return {"rss": rss_n, "dart": dart_n, **scan}


def main() -> None:
    loop = None
    if len(sys.argv) > 2 and sys.argv[1] == "--loop":
        loop = max(60, int(sys.argv[2]))
    if loop:
        print(f"[news_catalyst_runner] loop {loop}s")
        while True:
            t0 = time.monotonic()
            try:
                run_once()
            except Exception as exc:
                print(f"  run_once 오류(비치명): {exc}")
            sleep = loop - (time.monotonic() - t0)
            if sleep > 0:
                time.sleep(sleep)
    else:
        run_once()


if __name__ == "__main__":
    main()
