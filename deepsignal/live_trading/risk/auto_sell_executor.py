"""자동 매도 실행 모듈 (C — 손절·익절 트리거).

환경 변수 게이트:
  KIS_AUTO_SELL_TAKE_PROFIT=true   — ATR 기반 TP 도달 시 자동 LIMIT SELL
  KIS_AUTO_SELL_STOP_LOSS=true     — ATR 기반 SL 도달 시 자동 LIMIT SELL

두 플래그가 모두 false(기본)면 이 모듈은 아무 것도 실행하지 않는다.
매도 방식: LIMIT 주문 (current_price 기준, 시장가 사용 안 함)
재실행 방지: 당일 이미 매도 주문이 있으면 건너뜀.

TP/SL 기준: 고정값(+15%/-7%) 대신 동적 ATR 기반 계산
  - ATR × 등급배수 × 시장상태배수 × EV배수
  - 종목별로 자동 계산 (삼성전자 TP≈2.4%, SOXL TP≈7.2% 등)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENV_AUTO_SELL_TAKE = "KIS_AUTO_SELL_TAKE_PROFIT"
_ENV_AUTO_SELL_STOP = "KIS_AUTO_SELL_STOP_LOSS"

# 프로젝트 루트: auto_sell_executor.py 위치 기준
# deepsignal/live_trading/risk/ → parents[3] = project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _is_auto_sell_take_profit() -> bool:
    return os.getenv(_ENV_AUTO_SELL_TAKE, "").strip().lower() in ("1", "true", "yes")


def _is_auto_sell_stop_loss() -> bool:
    return os.getenv(_ENV_AUTO_SELL_STOP, "").strip().lower() in ("1", "true", "yes")


def is_any_auto_sell_enabled() -> bool:
    return _is_auto_sell_take_profit() or _is_auto_sell_stop_loss()


# 손절 미체결 방어 (#6): STOP_LOSS는 현재가보다 N bps 낮은 공격적 지정가로 제출해
# 급락장에서도 빠르게 체결되게 한다(시장가 미사용, LIMIT 유지). 0=현재가 그대로.
_ENV_SL_AGGRESSIVE_BPS = "KIS_AUTO_SELL_STOP_LOSS_AGGRESSIVE_BPS"


def _stop_loss_aggressive_bps() -> float:
    raw = os.getenv(_ENV_SL_AGGRESSIVE_BPS, "50").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 50.0


@dataclass
class AutoSellResult:
    symbol: str
    trigger: str              # "TAKE_PROFIT" | "STOP_LOSS"
    quantity: int
    limit_price: float
    success: bool
    dry_run: bool
    message: str
    broker_order_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # 동적 TP/SL 정보 (Telegram 알림용)
    tp_pct: float | None = None
    sl_pct: float | None = None
    atr_pct: float | None = None
    grade: str | None = None
    market_state: str | None = None


def _already_sold_today(db_path: str, symbol: str) -> bool:
    """당일 해당 종목 SELL 주문 성공 이력이 있으면 True (중복 방지).

    FAILED 주문은 제외 — 실패한 주문은 재시도 허용.
    DB 오류 시 True 반환 (안전 방향: 확인 불가면 중복 차단).
    """
    try:
        import sqlite3
        today = datetime.now().strftime("%Y-%m-%d")
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM real_order_history "
                "WHERE symbol=? AND side='SELL' AND status='SUBMITTED' AND created_at>=?",
                (symbol, today),
            )
            row = cur.fetchone()
            return (row[0] if row else 0) > 0
    except Exception:
        return True  # DB 확인 불가 → 안전하게 중복 매도 차단


def _build_kis_broker(db_path: str) -> Any:
    """KISBroker 인스턴스 생성 (auto-sell 전용: safe_mode=False로 실 주문 허용)."""
    from deepsignal.live_trading.broker.kis_broker import KISBroker
    from deepsignal.live_trading.broker.kis_config import load_kis_config_from_env
    cfg = load_kis_config_from_env()
    return KISBroker(cfg, safe_mode=False)


def _record_sell_order(db_path: str, symbol: str, result: AutoSellResult) -> None:
    """real_order_history에 SELL 주문 기록."""
    try:
        import sqlite3
        now = datetime.now().isoformat(timespec="seconds")
        raw_json = json.dumps(result.raw, ensure_ascii=False)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO real_order_history "
                "(created_at, broker, symbol, side, quantity, limit_price, "
                "estimated_order_value, status, order_id, raw_json) "
                "VALUES (?, 'kis', ?, 'SELL', ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    symbol,
                    result.quantity,
                    result.limit_price,
                    result.quantity * result.limit_price,
                    "SUBMITTED" if result.success else "FAILED",
                    result.broker_order_id or "",
                    raw_json,
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("SELL 주문 이력 기록 실패: %s", exc)


def _detect_asset_class(symbol: str) -> str:
    """6자리 숫자 → kis_stock, 나머지(NVDA, NASD:NVDA 등) → kis_overseas."""
    clean = symbol.split(":")[-1]
    return "kis_stock" if clean.isdigit() else "kis_overseas"


def _compute_tpsl_for_position(symbol: str) -> "Any":
    """종목 심볼에 대한 동적 TP/SL 계산 (ATR 일간 스케일 기준)."""
    try:
        from deepsignal.risk.dynamic_tpsl import (
            compute_dynamic_tpsl,
            load_bars_for_symbol,
        )
        asset_class = _detect_asset_class(symbol)
        bars, tf_min = load_bars_for_symbol(symbol, asset_class, _PROJECT_ROOT)
        return compute_dynamic_tpsl(
            symbol, asset_class,
            bars or None,
            timeframe_min=tf_min,
        )
    except Exception as exc:
        logger.debug("동적 TP/SL 계산 실패 (%s), 기본값 사용: %s", symbol, exc)
        return None


def try_auto_sell_on_risk_alert(
    db_path: str,
    *,
    output_dir: str | Path = "outputs",
    execute: bool = True,
    dry_run: bool = False,
) -> list[AutoSellResult]:
    """포지션별 동적 TP/SL 기반 자동 LIMIT SELL 주문.

    TP/SL 결정 방식:
      - ATR × 등급배수 × 시장상태배수 → 종목마다 자동 계산
      - 예) 삼성전자: TP≈+2.4%, SL≈-1.2% / SOXL: TP≈+7.2%, SL≈-4.2%
      - 바 데이터 없으면 등급별 기본값 사용

    Returns:
        실행한 AutoSellResult 목록 (없으면 빈 리스트).
    """
    take_profit_on = _is_auto_sell_take_profit()
    stop_loss_on   = _is_auto_sell_stop_loss()

    if not take_profit_on and not stop_loss_on:
        return []

    results: list[AutoSellResult] = []

    try:
        from deepsignal.live_trading.risk.risk_guard import (
            RISK_STATUS_TAKE_PROFIT,
            RISK_STATUS_STOP_LOSS,
            RiskGuardPolicy,
            evaluate_position_risk,
            load_positions_from_db,
        )
        rows, snap, equity = load_positions_from_db(db_path)
        if not rows:
            return []
    except Exception as exc:
        logger.warning("포지션 로드 실패: %s", exc)
        return []

    # Fix-3: DB 스냅샷 current_price를 KIS API 실시간 현재가로 갱신
    # (스냅샷은 09:05 1회 기준이라 장중 가격 변동이 반영되지 않음)
    try:
        _live_broker = _build_kis_broker(db_path)
        _live_positions = {
            p.symbol: p.current_price
            for p in _live_broker.get_positions()
            if p.current_price and p.current_price > 0
        }
        for _row in rows:
            _sym = str(_row.get("symbol") or "")
            if _sym in _live_positions:
                _old_px = _row.get("current_price")
                _row["current_price"] = _live_positions[_sym]
                logger.debug(
                    "[AutoSell] %s 현재가 갱신 (DB→실시간): %.0f → %.0f",
                    _sym, _old_px or 0, _live_positions[_sym],
                )
    except Exception as _exc:
        logger.warning("[AutoSell] 실시간 현재가 조회 실패, DB 스냅샷 사용: %s", _exc)

    for row in rows:
        sym = str(row.get("symbol") or "")
        if not sym:
            continue

        # 1. 종목별 동적 TP/SL 계산
        tpsl = _compute_tpsl_for_position(sym)
        if tpsl is not None:
            policy = RiskGuardPolicy(**tpsl.as_policy_kwargs())
            logger.debug(
                "[AutoSell] %s 동적 TP/SL: %s",
                sym, tpsl.summary_str(),
            )
        else:
            # 폴백: 기존 고정값
            policy = RiskGuardPolicy()

        # 2. 리스크 평가
        try:
            from deepsignal.live_trading.risk.risk_guard import evaluate_position_risk
            pos_risk = evaluate_position_risk(row, policy=policy)
        except Exception as exc:
            logger.warning("%s 리스크 평가 실패: %s", sym, exc)
            continue

        rl = pos_risk.risk_level

        if rl == RISK_STATUS_TAKE_PROFIT and not take_profit_on:
            logger.info("%s 익절 조건이나 KIS_AUTO_SELL_TAKE_PROFIT=false — 건너뜀", sym)
            continue
        if rl == RISK_STATUS_STOP_LOSS and not stop_loss_on:
            logger.info("%s 손절 조건이나 KIS_AUTO_SELL_STOP_LOSS=false — 건너뜀", sym)
            continue
        if rl not in (RISK_STATUS_TAKE_PROFIT, RISK_STATUS_STOP_LOSS):
            continue

        qty    = pos_risk.quantity
        cur_px = pos_risk.current_price
        trigger = "TAKE_PROFIT" if rl == RISK_STATUS_TAKE_PROFIT else "STOP_LOSS"

        if qty <= 0 or cur_px is None or cur_px <= 0:
            logger.warning("%s qty=%s price=%s — 매도 불가 (수량/가격 없음)", sym, qty, cur_px)
            continue

        # 당일 이미 매도했는지 확인
        if _already_sold_today(db_path, sym):
            logger.info("%s 당일 이미 SELL 주문 존재 — 건너뜀", sym)
            continue

        limit_price = float(cur_px)
        # 손절 미체결 방어 (#6): STOP_LOSS는 현재가보다 N bps 낮은 공격적 지정가로
        # 제출해 급락 중에도 체결 확률을 높인다. TAKE_PROFIT은 현재가 유지.
        if trigger == "STOP_LOSS":
            _aggr_bps = _stop_loss_aggressive_bps()
            if _aggr_bps > 0:
                _aggr = float(cur_px) * (1.0 - _aggr_bps / 10000.0)
                if _aggr > 0:
                    limit_price = _aggr

        pnl_pct_str = (
            f"{pos_risk.unrealized_pnl_pct * 100:+.2f}%"
            if pos_risk.unrealized_pnl_pct is not None else "n/a"
        )

        # 동적 TP/SL 정보 (로그·알림용)
        tp_str = f"+{tpsl.tp_pct * 100:.1f}%" if tpsl else "+15.0%(기본)"
        sl_str = f"{tpsl.sl_pct * 100:.1f}%" if tpsl else "-7.0%(기본)"
        grade_str = tpsl.grade.value if tpsl else "?"
        mkt_str   = tpsl.market_state.value if tpsl else "?"

        logger.info(
            "[AutoSell] %s trigger=%s qty=%d price=%.0f pnl=%s "
            "TP=%s SL=%s grade=%s market=%s dry_run=%s",
            sym, trigger, qty, limit_price, pnl_pct_str,
            tp_str, sl_str, grade_str, mkt_str, dry_run,
        )

        if dry_run:
            results.append(AutoSellResult(
                symbol=sym,
                trigger=trigger,
                quantity=qty,
                limit_price=limit_price,
                success=True,
                dry_run=True,
                message=(
                    f"dry_run: {trigger} 조건 충족 (pnl={pnl_pct_str}) "
                    f"TP={tp_str} SL={sl_str} [{grade_str}/{mkt_str}]"
                ),
                tp_pct=tpsl.tp_pct if tpsl else None,
                sl_pct=tpsl.sl_pct if tpsl else None,
                atr_pct=tpsl.atr_pct if tpsl else None,
                grade=grade_str,
                market_state=mkt_str,
            ))
            continue

        # 실 주문
        try:
            from deepsignal.live_trading.broker.interface import BrokerOrderRequest
            broker = _build_kis_broker(db_path)
            req = BrokerOrderRequest(
                symbol=sym,
                side="SELL",
                order_type="LIMIT",
                quantity=qty,
                limit_price=limit_price,
            )
            order_result = broker.place_order(req, execute=execute)
            ok = getattr(order_result, "status", "") in (
                "KIS_ORDER_SUBMITTED", "KIS_ORDER_SEND_BLOCKED_PHASE3"
            ) or (not execute)
            sell_res = AutoSellResult(
                symbol=sym,
                trigger=trigger,
                quantity=qty,
                limit_price=limit_price,
                success=ok,
                dry_run=dry_run,
                message=getattr(order_result, "message", ""),
                broker_order_id=getattr(order_result, "broker_order_id", None),
                raw=dict(getattr(order_result, "raw", {})),
                tp_pct=tpsl.tp_pct if tpsl else None,
                sl_pct=tpsl.sl_pct if tpsl else None,
                atr_pct=tpsl.atr_pct if tpsl else None,
                grade=grade_str,
                market_state=mkt_str,
            )
            results.append(sell_res)
            if execute:
                _record_sell_order(db_path, sym, sell_res)
            logger.info(
                "[AutoSell] %s %s → %s order_id=%s",
                sym, trigger, order_result.status, order_result.broker_order_id,
            )
        except Exception as exc:
            logger.error("[AutoSell] %s 주문 실패: %s", sym, exc, exc_info=True)
            results.append(AutoSellResult(
                symbol=sym,
                trigger=trigger,
                quantity=qty,
                limit_price=limit_price,
                success=False,
                dry_run=dry_run,
                message=f"주문 예외: {exc}",
            ))

    return results


def format_auto_sell_telegram(results: list[AutoSellResult]) -> str | None:
    """Telegram 알림 텍스트 생성 — 성공 건만 표시, 깔끔한 단일 카드 형식."""
    if not results:
        return None

    # 성공한 것만 알림 (실패는 로그로만)
    shown = [r for r in results if r.success and not r.dry_run]
    if not shown:
        return None

    msgs = []
    for r in shown:
        trigger_ko = "익절" if r.trigger == "TAKE_PROFIT" else "손절"
        icon = "🔴" if r.trigger == "TAKE_PROFIT" else "🟠"
        # 동적 TP/SL 한 줄 요약
        tpsl_line = ""
        if r.tp_pct is not None and r.sl_pct is not None:
            tpsl_line = (
                f"TP +{r.tp_pct * 100:.1f}%  ·  SL {r.sl_pct * 100:.1f}%"
                f"  [{r.grade} / {r.market_state}]"
            )
        lines = [
            f"{icon} {trigger_ko} 체결  ·  KIS",
            f"*{r.symbol}*",
            f"지정가 {r.limit_price:,.0f}원  ·  {r.quantity}주",
        ]
        if tpsl_line:
            lines.append(tpsl_line)
        msgs.append("\n".join(lines))

    return "\n\n".join(msgs) if msgs else None
