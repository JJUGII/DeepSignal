"""Crypto order plan JSON (separate from KIS live_order_plan)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_recommendation import CryptoRecommendation
from deepsignal.live_trading.time_utils import now_kst_iso, stamp_daily_ai_payload

CRYPTO_PLAN_JSON = "CRYPTO_ORDER_PLAN.json"
CRYPTO_PLAN_MD = "CRYPTO_DAILY_TRADE_PLAN.md"


@dataclass
class CryptoOrderPlan:
    broker: str = "upbit"
    market: str = ""
    side: str = "buy"
    order_type: str = "limit"
    krw_amount: float = 0.0
    volume: float = 0.0
    limit_price: float = 0.0
    avg_buy_price: float = 0.0
    pnl_pct: float = 0.0
    display_name: str = ""
    reason: str = ""
    status: str = "CRYPTO_PLAN_READY"
    created_at: str = ""
    warnings: list[str] = field(default_factory=list)
    sell_trigger: str = ""
    take_profit_pct: float = 0.0
    stop_loss_pct: float = 0.0
    technical_score: float | None = None
    macro_score: float | None = None
    final_score: float | None = None
    macro_regime: str = ""
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    quality_gates: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def order_count(self) -> int:
        if not self.market:
            return 0
        if self.side.lower() == "sell":
            return 1 if self.volume > 0 else 0
        return 1 if self.krw_amount > 0 else 0


def build_plan_from_recommendation(rec: CryptoRecommendation, *, broker: str = "upbit") -> CryptoOrderPlan:
    return CryptoOrderPlan(
        broker=broker,
        market=rec.market,
        side=rec.side,
        order_type="limit",
        krw_amount=rec.krw_amount,
        volume=float(rec.volume or 0),
        limit_price=rec.current_price,
        display_name=rec.display_name,
        reason=rec.reason,
        avg_buy_price=float(rec.avg_buy_price or 0),
        pnl_pct=float(rec.pnl_pct or 0),
        status="CRYPTO_PLAN_READY",
        created_at=now_kst_iso(),
        sell_trigger=str(rec.sell_trigger or ""),
        take_profit_pct=float(rec.take_profit_pct or 0),
        stop_loss_pct=float(rec.stop_loss_pct or 0),
        technical_score=rec.technical_score,
        macro_score=rec.macro_score,
        final_score=rec.final_score,
        macro_regime=str(rec.macro_regime or ""),
        score_breakdown=dict(rec.score_breakdown or {}),
        quality_gates=dict(rec.quality_gates or {}),
    )


def save_crypto_plan(output_dir: str | Path, plan: CryptoOrderPlan) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = stamp_daily_ai_payload(plan.to_dict())
    json_path = root / CRYPTO_PLAN_JSON
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = root / CRYPTO_PLAN_MD
    _CRYPTO_STATUS_KO: dict[str, str] = {
        "CRYPTO_PLAN_READY": "✅ 매매계획 준비됨",
        "CRYPTO_PLAN_NO_ACTION": "관망 (매매 조건 없음)",
        "CRYPTO_PLAN_FAILED": "❌ 계획 생성 실패",
        "CRYPTO_PLAN_BLOCKED": "차단됨",
    }
    _SIDE_KO = {"buy": "매수", "sell": "매도"}
    _ORDER_TYPE_KO = {"limit": "지정가", "market": "시장가", "best": "최우선"}

    status_ko = _CRYPTO_STATUS_KO.get(str(plan.status), str(plan.status))
    side_ko = _SIDE_KO.get(str(plan.side).lower(), str(plan.side))
    order_type_ko = _ORDER_TYPE_KO.get(str(plan.order_type).lower(), str(plan.order_type))

    md_lines = [
        "# DeepSignal — 코인 매매계획",
        "",
        f"- 상태: {status_ko}",
        f"- 종목: {plan.market} ({plan.display_name})",
        f"- 매매 방향: {side_ko}",
        f"- 주문 유형: {order_type_ko}",
        f"- 주문금액: {plan.krw_amount:,.0f}원",
        f"- 수량: {plan.volume}",
        f"- 지정가: {plan.limit_price:,.0f}원",
        f"- 사유: {plan.reason}",
        "",
    ]
    if plan.side == "sell":
        md_lines.insert(-2, f"- 수익률: {plan.pnl_pct:+.2f}%")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return json_path, md_path


def load_crypto_plan(path: str | Path) -> CryptoOrderPlan:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return CryptoOrderPlan(
        broker=str(data.get("broker", "upbit")),
        market=str(data.get("market", "")),
        side=str(data.get("side", "buy")),
        order_type=str(data.get("order_type", "limit")),
        krw_amount=float(data.get("krw_amount", 0) or 0),
        volume=float(data.get("volume", 0) or 0),
        limit_price=float(data.get("limit_price", 0) or 0),
        avg_buy_price=float(data.get("avg_buy_price", 0) or 0),
        pnl_pct=float(data.get("pnl_pct", 0) or 0),
        display_name=str(data.get("display_name", "")),
        reason=str(data.get("reason", "")),
        status=str(data.get("status", "")),
        created_at=str(data.get("created_at", "")),
        warnings=list(data.get("warnings") or []),
        sell_trigger=str(data.get("sell_trigger", "")),
        take_profit_pct=float(data.get("take_profit_pct", 0) or 0),
        stop_loss_pct=float(data.get("stop_loss_pct", 0) or 0),
    )
