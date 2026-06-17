"""신규상장 이벤트 영구 기록(append-only JSONL).

현재 시스템은 신규상장 *시각* 이력을 보존하지 않는다(스냅샷 차집합으로 즉시성만 판단).
그래서 상장 후 가격 거동 백테스트를 할 때 상장일을 프록시(짧은 캔들 길이)로 추정해야
했고 표본이 부족했다. 스캐너가 신규상장을 감지할 때마다 코인·거래소·시각을 여기 남겨
두면, 이후 정확한 상장일로 백테스트(눌림목 가설 등) 표본을 키울 수 있다.

비치명: 어떤 예외도 스캔/알림을 막지 않도록 호출측에서 try/except로 감싼다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


def append_listing_events(
    output_dir: str | Path,
    new_bithumb: Iterable[str] | None,
    new_upbit: Iterable[str] | None,
    *,
    names: Mapping[str, str] | None = None,
) -> int:
    """신규상장 마켓을 LISTING_EVENTS.jsonl에 한 줄씩 누적. 기록 건수 반환."""
    names = names or {}
    ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for m in (new_bithumb or []):
        rows.append({"ts": ts, "exchange": "bithumb", "market": str(m), "name": names.get(m, "")})
    for m in (new_upbit or []):
        rows.append({"ts": ts, "exchange": "upbit", "market": str(m), "name": names.get(m, "")})
    if not rows:
        return 0
    path = Path(output_dir) / "LISTING_EVENTS.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)
