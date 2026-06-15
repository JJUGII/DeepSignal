"""해외주식 손실방지 상태 — 트레일링 고점·당일 연속손절 차단.

코인 엔진의 손실방지(고점추적 트레일링 + 당일 손절 종목 재매수 차단)를 해외에 이식.
파일: outputs/OVERSEAS_RISK_STATE.json {date, peaks:{TICKER:usd}, sl_count:{TICKER:n}}
KST 일자가 바뀌면 자동 리셋.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))
_FILE = "OVERSEAS_RISK_STATE.json"


def _today() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d")


def _path(output_dir: str | Path) -> Path:
    return Path(output_dir) / _FILE


def _load(output_dir: str | Path) -> dict[str, Any]:
    try:
        p = _path(output_dir)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8")) or {}
            if d.get("date") == _today():
                return d
    except Exception:
        pass
    return {"date": _today(), "peaks": {}, "sl_count": {}}


def _save(output_dir: str | Path, d: dict[str, Any]) -> None:
    try:
        p = _path(output_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _tick(symbol: str) -> str:
    return str(symbol or "").split(":")[-1].upper()


def trailing_stop_pct() -> float:
    """고점 대비 하락 트레일링 임계 (기본 5%). 0이면 비활성."""
    try:
        return float(os.environ.get("OVERSEAS_TRAILING_STOP_PCT", "5.0") or 5.0)
    except ValueError:
        return 5.0


def max_stop_loss_per_day() -> int:
    """당일 동일 종목 손절 후 재매수 차단 횟수(기본 1 = 1회 손절 시 당일 재매수 금지)."""
    try:
        return int(os.environ.get("OVERSEAS_MAX_STOP_LOSS_PER_DAY", "1") or 1)
    except ValueError:
        return 1


def update_peak(output_dir: str | Path, symbol: str, price: float) -> float:
    """종목 고점 갱신, 갱신된 고점 반환."""
    d = _load(output_dir)
    t = _tick(symbol)
    peak = max(float(d["peaks"].get(t, 0) or 0), float(price or 0))
    d["peaks"][t] = peak
    _save(output_dir, d)
    return peak


def trailing_triggered(output_dir: str | Path, symbol: str, price: float, *, in_profit: bool) -> bool:
    """수익 중이면서 고점 대비 트레일링 임계 이상 하락했는지."""
    pct = trailing_stop_pct()
    if pct <= 0 or not in_profit:
        update_peak(output_dir, symbol, price)
        return False
    peak = update_peak(output_dir, symbol, price)
    if peak <= 0 or price <= 0:
        return False
    drop = (peak - price) / peak * 100.0
    return drop >= pct


def record_stop_loss(output_dir: str | Path, symbol: str) -> None:
    """손절 발생 기록 (당일 카운트 +1)."""
    d = _load(output_dir)
    t = _tick(symbol)
    d["sl_count"][t] = int(d["sl_count"].get(t, 0)) + 1
    d["peaks"].pop(t, None)  # 청산했으니 고점 리셋
    _save(output_dir, d)


def is_rebuy_blocked(output_dir: str | Path, symbol: str) -> bool:
    """당일 손절 횟수가 한도 이상이면 재매수 차단."""
    d = _load(output_dir)
    t = _tick(symbol)
    return int(d["sl_count"].get(t, 0)) >= max_stop_loss_per_day()
