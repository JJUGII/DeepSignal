"""DART 전자공시 수집 — 한국 소형주는 공시 한 줄에 급등. RSS보다 빠르고 구조적.

opendart.fss.or.kr 공개 API(무료, 키 필요). env DART_API_KEY 없으면 graceful skip([]).
공시 목록을 news_items 호환 dict로 변환해 LLM 촉매 분석기로 흘려보낸다.

키 발급: https://opendart.fss.or.kr → 인증키 신청(무료) → .env DART_API_KEY=...
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

_KST = timezone(timedelta(hours=9))
_LIST_URL = "https://opendart.fss.or.kr/api/list.json"


def dart_enabled() -> bool:
    return bool((os.environ.get("DART_API_KEY") or "").strip())


def fetch_recent_disclosures(*, days: int = 1, max_pages: int = 3, timeout: float = 12.0) -> list[dict[str, Any]]:
    """최근 days일 전체 공시 목록. news_items 호환 dict 리스트 반환. 무키/실패 → []."""
    key = (os.environ.get("DART_API_KEY") or "").strip()
    if not key:
        return []
    end = datetime.now(_KST)
    bgn = end - timedelta(days=max(1, days))
    out: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        try:
            r = requests.get(_LIST_URL, params={
                "crtfc_key": key,
                "bgn_de": bgn.strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "page_no": page,
                "page_count": 100,
            }, timeout=timeout)
            d = r.json()
        except Exception:
            break
        if str(d.get("status")) != "000":   # 000 = 정상
            break
        rows = d.get("list") or []
        for row in rows:
            stock_code = str(row.get("stock_code") or "").strip()
            if not stock_code or not stock_code.isdigit():
                continue  # 상장사(종목코드 있는) 공시만
            corp = str(row.get("corp_name") or "").strip()
            report = str(row.get("report_nm") or "").strip()
            rcept = str(row.get("rcept_dt") or "")  # YYYYMMDD
            try:
                pub = datetime.strptime(rcept, "%Y%m%d").replace(tzinfo=_KST).isoformat()
            except Exception:
                pub = ""
            out.append({
                "source": "dart",
                "symbol": stock_code.zfill(6),
                "title": f"[공시] {corp}: {report}",
                "summary": report,
                "published_at": pub,
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={row.get('rcept_no','')}",
                "corp_name": corp,
            })
        total_page = int(d.get("total_page") or 1)
        if page >= total_page:
            break
    return out


def store_disclosures(disclosures: list[dict[str, Any]], *, db_path: str | None = None) -> int:
    """공시를 news_items에 저장(중복 무시). 저장 건수 반환."""
    if not disclosures:
        return 0
    import hashlib
    import json as _json
    import sqlite3
    from pathlib import Path

    from deepsignal.config.settings import load_settings
    p = db_path or load_settings().db_path
    conn = sqlite3.connect(str(Path(p).expanduser().resolve()))
    n = 0
    now = datetime.now(timezone.utc).isoformat()
    for x in disclosures:
        h = hashlib.sha256((x["source"] + x["url"] + x["title"]).encode()).hexdigest()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO news_items(created_at,source,source_hash,url,title,summary,"
                "published_at,symbol,raw_json) VALUES(?,?,?,?,?,?,?,?,?)",
                (now, x["source"], h, x["url"], x["title"], x["summary"], x.get("published_at", ""),
                 x.get("symbol"), _json.dumps({"corp_name": x.get("corp_name")}, ensure_ascii=False)),
            )
            n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        except Exception:
            pass
    conn.commit()
    conn.close()
    return n
