"""코인 주문 실패/취소 이력 — 사유와 함께 append-only 기록.

대기중 주문이 매수벽·스프레드·미체결 등으로 실패/취소될 때 사유를 남겨,
대시보드에서 "왜 안 샀는지"를 토스트가 사라진 뒤에도 확인할 수 있게 한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))
_FILE = "crypto_order_failures.jsonl"
_MAX_LINES = 120  # 너무 커지지 않게 최근 N건만 유지


def _path(output_dir: str | Path) -> Path:
    return Path(output_dir) / _FILE


# 사유를 일반인이 이해할 한국어로 매핑
def _humanize(reason: str) -> str:
    r = str(reason)
    if "매수벽 부족" in r:
        return "매도벽이 커서 보류 (지금 사면 떨어질 위험 — 매도 물량이 매수보다 많음)"
    if "스프레드" in r:
        return "호가 간격(스프레드)이 너무 벌어져 보류 (비싸게 사게 됨)"
    if "미체결" in r or "타임아웃" in r:
        return "지정가에 안 채워져 취소 (매수벽 뒤에서 대기하다 미체결)"
    if "R:R" in r or "rr" in r.lower() or "기대" in r:
        return "기대수익이 비용(스프레드·수수료)보다 작아 보류"
    if "거래량" in r or "volume" in r.lower():
        return "거래량 부족으로 보류"
    if "승률" in r or "P(win)" in r or "win" in r.lower():
        return "ML 예측 승률이 기준보다 낮아 보류"
    if "뉴스 악재" in r or "악재" in r:
        return r  # 이미 사람이 읽을 수 있는 한국어 (이벤트+요약)
    return r


def record_crypto_order_failure(
    output_dir: str | Path,
    *,
    market: str,
    side: str = "buy",
    stage: str,
    reasons: list[str] | str,
    krw: float = 0.0,
    display_name: str | None = None,
) -> None:
    """실패/취소 1건 기록. stage: gate|quality|unfilled|cancel|error."""
    try:
        if isinstance(reasons, str):
            reasons = [reasons]
        reasons = [str(r) for r in (reasons or []) if str(r).strip()]
        if not reasons:
            reasons = ["사유 미상"]
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(_KST).isoformat(timespec="seconds"),
            "market": str(market),
            "display_name": str(display_name or market),
            "side": "매수" if str(side).lower() in ("buy", "bid") else "매도",
            "stage": str(stage),
            "reasons": reasons,
            "reason_kr": "; ".join(_humanize(r) for r in reasons),
            "krw": round(float(krw or 0), 0),
        }
        p = _path(out)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # 파일 캡: 너무 길면 최근 _MAX_LINES만 유지
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
            if len(lines) > _MAX_LINES + 50:
                p.write_text("\n".join(lines[-_MAX_LINES:]) + "\n", encoding="utf-8")
        except OSError:
            pass
    except Exception:
        pass  # 로깅 실패가 거래를 막지 않게


def load_crypto_order_failures(output_dir: str | Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """최근 실패/취소 이력 (최신순)."""
    p = _path(output_dir)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    out.reverse()  # 최신순
    return out[: max(1, int(limit))]


def load_crypto_order_failures_summary(output_dir: str | Path, *, limit: int = 5) -> list[dict[str, Any]]:
    """코인별로 묶어 최신 1건 + 반복횟수만 반환(중복 제거, 최소 표시용)."""
    raw = load_crypto_order_failures(output_dir, limit=_MAX_LINES)
    by_market: dict[str, dict[str, Any]] = {}
    for r in raw:  # 이미 최신순
        mk = r.get("market", "")
        if mk not in by_market:
            by_market[mk] = {**r, "count": 1}
        else:
            by_market[mk]["count"] += 1
    items = list(by_market.values())
    # 최신순 정렬(각 코인 최신 ts 기준)
    items.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return items[: max(1, int(limit))]
