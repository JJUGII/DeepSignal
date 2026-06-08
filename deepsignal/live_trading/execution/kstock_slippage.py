"""KIS 주식 체결오차(슬리피지) 기록 모듈.

크립토의 `record_fill_slippage_feedback()`과 동일 포맷.
국장: KSTOCK_FILL_SLIPPAGE.jsonl
해외: OVERSEAS_FILL_SLIPPAGE.jsonl

각 레코드:
  ts          - ISO UTC 타임스탬프
  symbol      - 종목코드
  side        - buy | sell
  market      - KRW | USD
  limit_price - 주문 가격 (지정가)
  fill_price  - 실제 체결 평균가 (avg_prvs / ft_ccld_unpr3)
  order_krw   - 주문 금액 (원화 환산)
  slippage_bps - 체결오차 (bps)
  order_id    - KIS 주문번호 (odno)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_MAX_SLIPPAGE_BPS = 1000.0   # 1000 bps(10%) 초과 = 데이터 오류 → 제외


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None and str(v).strip() != "" else default
    except (TypeError, ValueError):
        return default


def record_kstock_fill_slippage(
    output_dir: Path | str,
    *,
    symbol: str,
    side: str,               # "buy" | "sell"
    limit_price: float,
    fill_price: float,
    order_krw: float = 0.0,
    order_id: str = "",
    market: str = "KRW",
    filename: str = "KSTOCK_FILL_SLIPPAGE.jsonl",
) -> bool:
    """체결오차 레코드를 JSONL 파일에 추가.

    Args:
        output_dir: 출력 디렉토리 (output/kis_stream/ 또는 output/kis_overseas/)
        symbol: 종목코드
        side: "buy" | "sell"
        limit_price: 지정가 주문 가격
        fill_price: 실제 체결 평균가
        order_krw: 주문 금액 (원화)
        order_id: KIS 주문번호
        market: "KRW" (국장) | "USD" (해외)
        filename: 출력 파일명

    Returns:
        True if recorded, False if skipped (invalid data)
    """
    if limit_price <= 0 or fill_price <= 0:
        return False

    slip_bps = abs(fill_price - limit_price) / limit_price * 10_000.0
    if slip_bps > _MAX_SLIPPAGE_BPS:
        return False

    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side,
        "market": market,
        "limit_price": round(limit_price, 4),
        "fill_price": round(fill_price, 4),
        "order_krw": round(order_krw, 0),
        "slippage_bps": round(slip_bps, 2),
        "order_id": order_id,
    }

    path = Path(output_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    return True


def load_slippage_entries(
    output_dir: Path | str,
    filename: str,
    limit: int = 100,
) -> dict[str, Any]:
    """슬리피지 JSONL 파일 로드 → UI/API 응답 형식으로 반환."""
    path = Path(output_dir) / filename
    if not path.is_file():
        return {"entries": [], "exists": False, "total": 0}

    try:
        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        entries: list[dict] = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        # 최신 순 정렬
        entries.reverse()

        # 요약 통계
        bps_list = [float(e.get("slippage_bps") or 0) for e in entries if e.get("slippage_bps") is not None]
        avg_bps = round(sum(bps_list) / len(bps_list), 2) if bps_list else None
        max_bps = round(max(bps_list), 2) if bps_list else None
        p90_bps = None
        if bps_list:
            sorted_bps = sorted(bps_list)
            p90_idx = int(len(sorted_bps) * 0.9)
            p90_bps = round(sorted_bps[min(p90_idx, len(sorted_bps) - 1)], 2)

        return {
            "entries": entries,
            "exists": True,
            "total": len(lines),
            "stats": {
                "avg_slippage_bps": avg_bps,
                "max_slippage_bps": max_bps,
                "p90_slippage_bps": p90_bps,
                "sample_count": len(bps_list),
            },
        }
    except Exception as e:
        return {"entries": [], "exists": False, "error": str(e), "total": 0}


def compute_slippage_from_kis_row(row: dict[str, Any]) -> float | None:
    """KIS inquire-daily-ccld output1 행에서 슬리피지(bps) 계산.

    ord_unpr = 지정가
    avg_prvs = 평균체결가
    """
    ord_unpr = _safe_float(row.get("ord_unpr") or row.get("ORD_UNPR"))
    avg_prvs = _safe_float(row.get("avg_prvs") or row.get("AVG_PRVS"))
    if ord_unpr <= 0 or avg_prvs <= 0:
        return None
    slip = abs(avg_prvs - ord_unpr) / ord_unpr * 10_000.0
    return round(slip, 2) if slip <= _MAX_SLIPPAGE_BPS else None


def compute_slippage_from_overseas_row(row: dict[str, Any]) -> float | None:
    """KIS JTTT3001R output 행에서 슬리피지(bps) 계산.

    ft_ord_unpr3  = 원주문가격 (USD)
    ft_ccld_unpr3 = 체결단가 (USD)
    """
    ord_unpr = _safe_float(row.get("ft_ord_unpr3") or row.get("FT_ORD_UNPR3"))
    fill_unpr = _safe_float(row.get("ft_ccld_unpr3") or row.get("FT_CCLD_UNPR3"))
    if ord_unpr <= 0 or fill_unpr <= 0:
        return None
    slip = abs(fill_unpr - ord_unpr) / ord_unpr * 10_000.0
    return round(slip, 2) if slip <= _MAX_SLIPPAGE_BPS else None
