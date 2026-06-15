"""국내주식 매수 후보 영속 블록리스트 — 계좌 권한·거래불가 종목 자동 학습.

매수 계획에서 특정 종목이 KIS에 반복 거부될 때(예: 파생ETF 미신청 계좌의
레버리지/인버스 ETF), 그 종목을 파일에 기록해 다음 계획부터 후보에서 제외한다.
이름 기반 필터는 누락이 잦아, '거부 사유 학습' 방식을 1차로 쓴다.

시드: 대표 파생ETF(레버리지/인버스) 코드 — 첫 거부 전에도 선제 차단.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))
_FILE = "KSTOCK_SYMBOL_BLOCKLIST.json"

# 대표 파생ETF(레버리지·인버스·곱버스) — 파생ETF 미신청 계좌는 전부 거부됨.
# 자동 학습이 채우기 전에도 흔한 종목을 선차단.
_SEED_DERIV_ETF = {
    "122630",  # KODEX 레버리지
    "233740",  # KODEX 코스닥150레버리지
    "252670",  # KODEX 200선물인버스2X (곱버스)
    "251340",  # KODEX 코스닥150선물인버스
    "114800",  # KODEX 인버스
    "123320",  # TIGER 레버리지
    "267770",  # TIGER 200선물인버스2X
    "225130",  # KOSEF 200선물레버리지
    "291630",  # KODEX 코스닥150레버리지(구)
}

# 거부 사유에 이 문자열이 포함되면 자동 블록 (계좌 권한·종목 자체 거래불가)
_AUTO_BLOCK_REASON_KEYS = ("파생ETF", "선택확인서", "거래가 불가", "거래 불가", "매매거래정지")


def _path(output_dir: str | Path = "outputs") -> Path:
    return Path(output_dir) / _FILE


def load_blocklist(output_dir: str | Path = "outputs") -> set[str]:
    """차단 종목코드 집합 (시드 + 학습분)."""
    blocked = set(_SEED_DERIV_ETF)
    try:
        p = _path(output_dir)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            for sym in (data.get("symbols") or {}):
                blocked.add(str(sym).strip().zfill(6))
    except Exception:
        pass
    return blocked


def is_blocked(symbol: str, output_dir: str | Path = "outputs") -> bool:
    sym = str(symbol or "").split(":")[-1].strip()
    if sym.isdigit():
        sym = sym.zfill(6)
    return sym in load_blocklist(output_dir)


def add_to_blocklist(symbol: str, reason: str = "", output_dir: str | Path = "outputs") -> bool:
    """종목을 학습 블록리스트에 기록 (사유·시각 포함). 신규면 True."""
    sym = str(symbol or "").split(":")[-1].strip()
    if not sym:
        return False
    if sym.isdigit():
        sym = sym.zfill(6)
    p = _path(output_dir)
    try:
        data: dict[str, Any] = {}
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8")) or {}
        symbols = dict(data.get("symbols") or {})
        is_new = sym not in symbols
        symbols[sym] = {"reason": str(reason)[:120],
                        "added_at": datetime.now(_KST).isoformat(timespec="seconds")}
        data["symbols"] = symbols
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return is_new
    except Exception:
        return False


def maybe_block_from_rejection(symbol: str, reason: str, output_dir: str | Path = "outputs") -> bool:
    """거부 사유가 영속 차단 대상이면 블록리스트에 추가. 추가했으면 True."""
    r = str(reason or "")
    if any(k in r for k in _AUTO_BLOCK_REASON_KEYS):
        return add_to_blocklist(symbol, reason=r, output_dir=output_dir)
    return False
