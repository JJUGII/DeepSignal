"""해외주식(미국장) 주문 plan 생성기.

흐름:
  1. outputs/kis_overseas/bars/*_1m.jsonl → K-GSQS 실시간 채점
  2. BUY 후보(action≠HOLD, total_score ≥ 임계값) 선별
  3. compute_kstock_sizing(asset_class='kis_overseas')로 USD 사이징
  4. LiveOrderPlan 호환 JSON 생성 → outputs/ 저장

실주문 없음. plan 생성만 한다. 실제 주문은 해외 자동 러너가 게이트 통과 시
KISBroker.place_order_overseas(execute=True) 로 수행한다 (safe_mode=False 필요).

symbol은 'NASD:NVDA' 형식으로 거래소를 보존하므로, plan을 받은 실행 단계가
place_order_overseas 에 그대로 넘기면 거래소가 자동 분리된다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

OVERSEAS_PLAN_LATEST = "live_order_plan_overseas_latest.json"


def compute_overseas_scores(output_dir: str | Path) -> list[dict[str, Any]]:
    """outputs/kis_overseas/bars/*_1m.jsonl → K-GSQS 채점 결과 목록.

    server._get_overseas_stream 과 동일 로직 (CLI/러너에서 web_ui 의존 없이 사용).
    symbol은 거래소 prefix 포함('NASD:NVDA').
    """
    from deepsignal.market_data.kis_stream.feature_engine import StockFeatureEngine
    from deepsignal.market_data.kis_stream.models import KisOhlcvBar
    from deepsignal.scoring.kstock_scorer import compute_kgsqs

    os_dir = Path(output_dir) / "kis_overseas"
    bars_dir = os_dir / "bars"

    eng = StockFeatureEngine()
    scores: list[dict[str, Any]] = []
    if not bars_dir.exists():
        bars_dir = None  # 봉 없음 — 스캐너 병합만으로 진행
    for bar_file in (sorted(bars_dir.glob("*_1m.jsonl")) if bars_dir else []):
        sym = bar_file.name.replace("_1m.jsonl", "")
        try:
            lines = bar_file.read_text(encoding="utf-8").strip().splitlines()
            if not lines:
                continue
            for line in lines[-30:]:
                eng.on_bar(KisOhlcvBar.from_dict(json.loads(line)))
            features = eng.build_features(sym)
            if features is None:
                continue
            signal = compute_kgsqs(features)
            scores.append({
                "symbol": sym,
                "price": features.price,            # USD
                "total_score": signal.total_score,
                "action": signal.action,
                "hard_blocked": signal.hard_blocked,
                "sub_scores": signal.sub_scores,
            })
        except Exception:
            pass
    # ── 전 시장 급등주 스캐너 병합 (다이얼 L9-10) ──
    # 스트림 봉이 없는 급등주를 KIS 조건검색 캐시에서 끌어와 스코어 풀에 합친다.
    # 점수는 등락률 주도(국내 스캐너와 동일 스케일). 기존 종목과 중복 시 미추가.
    try:
        if os.environ.get("OVERSEAS_SCANNER_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"):
            from deepsignal.live_trading.overseas_market_scanner import load_overseas_movers
            have = {s["symbol"] for s in scores}
            for m in load_overseas_movers(output_dir):
                if m["symbol"] in have:
                    continue
                chg = float(m.get("change_pct") or 0)
                sc = round(min(97.0, 28.0 + min(60.0, chg * 4.0)
                               + (6.0 if float(m.get("turnover_usd") or 0) >= 5e7 else 0.0)), 1)
                scores.append({
                    "symbol": m["symbol"], "price": float(m["price"]),
                    "total_score": sc, "action": "BUY", "hard_blocked": False,
                    "sub_scores": {}, "name": m.get("name"),
                    "reason": f"전시장 급등 스캔: {m.get('name')} {chg:+.1f}%",
                })
    except Exception:
        pass

    scores.sort(key=lambda x: x["total_score"], reverse=True)
    return scores


def _overseas_capital_usd() -> float:
    """해외 매수 자본 (USD). .env OVERSEAS_CAPITAL_USD, 기본 1000."""
    try:
        return float(os.environ.get("OVERSEAS_CAPITAL_USD", "1000") or 1000)
    except Exception:
        return 1000.0


def _max_single_order_usd() -> float:
    try:
        return float(os.environ.get("OVERSEAS_MAX_SINGLE_ORDER_USD", "300") or 300)
    except Exception:
        return 300.0


def build_overseas_order_plan(
    output_dir: str | Path,
    *,
    capital_usd: float | None = None,
    usd_rate: float = 1350.0,
    max_positions: int = 3,
    available_cash_usd: float | None = None,
) -> dict[str, Any]:
    """해외 K-GSQS 스코어 → 사이징 → 주문 plan JSON 생성·저장.

    Args:
        output_dir: outputs 디렉토리
        capital_usd: 총 투자 자본 (USD). None이면 .env/기본값.
        usd_rate: USD→KRW 환율 (사이징 내부 KRW 환산용).
        max_positions: 최대 동시 매수 종목 수.
        available_cash_usd: 가용 현금 (USD). None이면 capital_usd 사용.

    Returns:
        plan dict (저장도 수행).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cap_usd = float(capital_usd if capital_usd is not None else _overseas_capital_usd())
    avail_usd = float(available_cash_usd if available_cash_usd is not None else cap_usd)
    max_single_usd = _max_single_order_usd()
    now = datetime.now(_KST)

    scores = compute_overseas_scores(out)

    # 사이징은 KRW 기준이므로 USD price를 KRW로 환산해 입력
    scores_krw = []
    for s in scores:
        s2 = dict(s)
        s2["price"] = float(s.get("price") or 0) * usd_rate
        scores_krw.append(s2)

    orders: list[dict[str, Any]] = []
    warnings: list[str] = ["AI 추천 plan — 실주문 없음. 실행은 게이트 통과 시에만."]

    try:
        from deepsignal.live_trading.execution.kstock_sizing import compute_kstock_sizing
        result = compute_kstock_sizing(
            available_cash=avail_usd * usd_rate,
            total_equity=cap_usd * usd_rate,
            scores=scores_krw,
            asset_class="kis_overseas",
            asset_label="해외주식",
            kis_env=os.environ.get("KIS_ENV", "paper"),
            max_positions=max_positions,
        )
        for rec in result.recommendations:
            if rec.blocked or rec.recommended_shares <= 0:
                continue
            # KRW 환산가 → 원래 USD 단가 복원
            price_usd = (rec.current_price or 0) / usd_rate if usd_rate else 0
            if price_usd <= 0:
                continue
            qty = int(rec.recommended_shares)
            est_usd = round(qty * price_usd, 2)
            # 단일 주문 상한
            while qty > 1 and est_usd > max_single_usd:
                qty -= 1
                est_usd = round(qty * price_usd, 2)
            if qty <= 0:
                continue
            orders.append({
                "symbol": rec.symbol,                  # 'NASD:NVDA' 형식
                "side": "BUY",
                "quantity": qty,
                "estimated_price_usd": round(price_usd, 2),
                "estimated_order_value_usd": est_usd,
                "score": round(rec.score, 1),
                "score_label": rec.score_label,
                "tp_pct": rec.tp_pct,
                "sl_pct": rec.sl_pct,
                "reason": f"K-GSQS {rec.score:.1f} ({rec.score_label})",
            })
    except Exception as exc:
        warnings.append(f"사이징 실패: {exc}")

    if not scores:
        warnings.append("해외 스코어 없음 — 미국 장외 시간이거나 데이터 미수집.")

    plan = {
        "date": now.strftime("%Y-%m-%d"),
        "created_at": now.isoformat(timespec="seconds"),
        "broker": "kis_overseas",
        "asset_class": "kis_overseas",
        "currency": "USD",
        "usd_rate": usd_rate,
        "capital_usd": cap_usd,
        "status": "PENDING_APPROVAL" if orders else "NO_ORDERS",
        "approval_required": True,
        "dry_run": True,
        "order_count": len(orders),
        "orders": orders,
        "scanned": len(scores),
        "warnings": warnings,
        "safety_boundary": {
            "live_approve_called": False,
            "execute_called": False,
            "market_orders_allowed": False,
            "human_final_approval_required": True,
        },
    }

    # 저장 (latest + 타임스탬프)
    latest_path = out / OVERSEAS_PLAN_LATEST
    ts_path = out / f"live_order_plan_overseas_{now.strftime('%Y%m%d_%H%M%S')}.json"
    body = json.dumps(plan, ensure_ascii=False, indent=2)
    latest_path.write_text(body, encoding="utf-8")
    ts_path.write_text(body, encoding="utf-8")
    plan["plan_path"] = str(latest_path)
    return plan
