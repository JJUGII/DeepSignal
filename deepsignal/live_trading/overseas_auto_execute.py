"""해외주식(미국장) 자동 실행 정책 + plan 실행.

안전 게이트 (모두 기본 OFF — 사용자가 .env에서 명시적으로 켜야 실주문):
  OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL=true  — 승인 없이 자동 매수 실행
  OVERSEAS_MAX_SINGLE_ORDER_USD=300            — 단일 주문 USD 상한
  OVERSEAS_MAX_ORDERS_PER_RUN=3                — 1회 실행 최대 주문 수

게이트가 꺼져 있으면 plan 실행은 dry-run(접수 미발송)으로만 동작한다.
실주문은 게이트 ON + KISBroker(safe_mode=False) + execute=True 3중 조건을 모두 만족할 때만.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")

# 미국 티커 한국어 이름
_US_NAME_KR: dict[str, str] = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "NVDA": "엔비디아", "GOOGL": "알파벳",
    "GOOG": "알파벳", "AMZN": "아마존", "META": "메타", "TSLA": "테슬라", "AMD": "AMD",
    "AVGO": "브로드컴", "NFLX": "넷플릭스", "ADBE": "어도비", "CRM": "세일즈포스",
    "ORCL": "오라클", "INTC": "인텔", "QCOM": "퀄컴", "CSCO": "시스코", "TXN": "텍사스인스트루먼트",
    "AMAT": "어플라이드머티어리얼즈", "MU": "마이크론", "PYPL": "페이팔",
    "SPY": "S&P500 ETF", "QQQ": "나스닥100 ETF", "IWM": "러셀2000 ETF",
    "GLD": "금 ETF", "TLT": "장기국채 ETF", "SOXL": "반도체3X ETF",
}


def _us_name_kr(symbol: str) -> str:
    """'NVDA' → '엔비디아', 없으면 티커 그대로."""
    sym = (symbol or "").split(":")[-1].strip().upper()
    return _US_NAME_KR.get(sym, sym)


def _now_kst_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def is_runner_paused(output_dir: str | Path) -> bool:
    """대시보드 '전체 일시정지' 토글 상태 (코인·국내주식과 공유).

    CRYPTO_AUTO_RUNNER_STATE.json 의 runner_paused 플래그를 읽는다.
    """
    try:
        p = Path(output_dir) / "CRYPTO_AUTO_RUNNER_STATE.json"
        if not p.is_file():
            return False
        st = json.loads(p.read_text(encoding="utf-8"))
        return bool(st.get("runner_paused", False))
    except Exception:
        return False


def is_overseas_auto_execute_without_approval() -> bool:
    """OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL 게이트 (기본 False)."""
    return _truthy(os.environ.get("OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL"))


def _max_orders_per_run() -> int:
    try:
        return int(os.environ.get("OVERSEAS_MAX_ORDERS_PER_RUN", "3") or 3)
    except Exception:
        return 3


def _max_single_order_usd() -> float:
    try:
        return float(os.environ.get("OVERSEAS_MAX_SINGLE_ORDER_USD", "300") or 300)
    except Exception:
        return 300.0


@dataclass
class OverseasExecResult:
    symbol: str
    side: str
    quantity: int
    limit_price_usd: float
    status: str
    success: bool
    message: str
    dry_run: bool
    order_id: str | None = None


def execute_overseas_plan(
    output_dir: str | Path,
    *,
    plan: dict[str, Any] | None = None,
    force_execute: bool = False,
) -> list[OverseasExecResult]:
    """해외 plan의 BUY 주문을 실행한다.

    실주문 조건 (전부 충족 시에만):
      1) force_execute=True 또는 OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL=true
      2) KIS_ENV가 live (모의는 모의서버로 안전 전송)
      3) KISBroker(safe_mode=False) + place_order_overseas(execute=True)

    게이트 OFF면 모든 주문을 dry-run(safe_mode=True)으로 미리보기만.
    """
    out = Path(output_dir)
    # plan 로드
    if plan is None:
        from deepsignal.live_trading.overseas_plan import OVERSEAS_PLAN_LATEST
        p = out / OVERSEAS_PLAN_LATEST
        if not p.is_file():
            return []
        try:
            plan = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []

    orders = (plan or {}).get("orders") or []
    if not orders:
        return []

    gate_on = force_execute or is_overseas_auto_execute_without_approval()

    # ── 안전장치: 전역 킬스위치 + EDGE_GATE (#E/#F) ──────────────────
    # halt 중이거나 해외(미국주식) 전략 엣지 미검증이면 실매수를 dry-run으로 강등.
    # 매도/청산(auto_sell_overseas)은 별도 경로라 영향받지 않는다.
    if gate_on:
        from deepsignal.risk.edge_gate import edge_gate_allows_buy, strategy_for_live
        from deepsignal.risk.trading_halt import is_trading_halted

        _halted, _hr = is_trading_halted(output_dir)
        _eg_ok, _er = edge_gate_allows_buy(output_dir, strategy_for_live("overseas"))
        if _halted or not _eg_ok:
            logger.warning("해외 실매수 차단(dry-run 강등): %s", _hr if _halted else _er)
            gate_on = False

    max_orders = _max_orders_per_run()
    max_single = _max_single_order_usd()

    # 브로커 — 게이트 ON일 때만 safe_mode=False (실주문 가능)
    from deepsignal.live_trading.broker.kis_broker import KISBroker
    from deepsignal.live_trading.broker.kis_config import load_kis_config_from_env
    cfg = load_kis_config_from_env()
    broker = KISBroker(cfg, safe_mode=not gate_on)

    results: list[OverseasExecResult] = []
    submitted = 0
    for o in orders:
        if submitted >= max_orders:
            break
        symbol = str(o.get("symbol") or "")
        qty = int(o.get("quantity") or 0)
        px = float(o.get("estimated_price_usd") or 0)
        side = str(o.get("side") or "BUY").upper()
        if not symbol or qty <= 0 or px <= 0:
            continue
        # 단일 주문 상한 재확인
        if qty * px > max_single:
            qty = max(1, int(max_single // px))
        # 실행 (게이트 ON이면 execute=True, 아니면 dry-run)
        res = broker.place_order_overseas(
            symbol, side, qty, px, execute=gate_on,
        )
        ok = res.status in ("KIS_ORDER_SUBMITTED",) or (not gate_on)
        results.append(OverseasExecResult(
            symbol=symbol, side=side, quantity=qty, limit_price_usd=px,
            status=res.status, success=ok, message=res.message,
            dry_run=not gate_on, order_id=res.broker_order_id,
        ))
        if gate_on and res.status == "KIS_ORDER_SUBMITTED":
            submitted += 1

    # 실행 감사 로그
    try:
        now = datetime.now(_KST)
        audit = {
            "ts": now.isoformat(timespec="seconds"),
            "gate_on": gate_on,
            "kis_env": cfg.env,
            "results": [r.__dict__ for r in results],
        }
        (out / f"overseas_execute_audit_{now.strftime('%Y%m%d_%H%M%S')}.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return results


def auto_sell_overseas(
    output_dir: str | Path,
    *,
    force_execute: bool = False,
) -> list[OverseasExecResult]:
    """해외 보유 포지션의 TP/SL 도달 시 자동 LIMIT SELL.

    게이트:
      KIS_AUTO_SELL_TAKE_PROFIT / KIS_AUTO_SELL_STOP_LOSS (국내와 공유)
      + OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL (실주문 허용)
    TP/SL은 동적 ATR 기반(_compute_tpsl_for_position), 실패 시 기본값(+5%/-3%).
    """
    from deepsignal.live_trading.risk.auto_sell_executor import (
        _is_auto_sell_take_profit, _is_auto_sell_stop_loss, _compute_tpsl_for_position,
    )
    tp_on = _is_auto_sell_take_profit()
    sl_on = _is_auto_sell_stop_loss()
    if not tp_on and not sl_on:
        return []

    gate_on = force_execute or is_overseas_auto_execute_without_approval()
    from deepsignal.live_trading.broker.kis_broker import KISBroker
    from deepsignal.live_trading.broker.kis_config import load_kis_config_from_env
    cfg = load_kis_config_from_env()
    broker = KISBroker(cfg, safe_mode=not gate_on)

    results: list[OverseasExecResult] = []
    try:
        positions = broker.get_positions_overseas()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[OverseasSell] 포지션 조회 실패: %s", exc)
        return []

    for p in positions:
        avg = float(p.avg_price or 0)
        cur = float(p.current_price or 0)
        qty = int(p.quantity or 0)
        if avg <= 0 or cur <= 0 or qty <= 0:
            continue
        pnl_pct = (cur - avg) / avg
        ticker = p.symbol.split(":")[-1]
        tpsl = _compute_tpsl_for_position(ticker)
        tp = (tpsl.tp_pct if tpsl else None) or 0.05
        sl = (tpsl.sl_pct if tpsl else None) or -0.03
        trigger = None
        if tp_on and pnl_pct >= tp:
            trigger = "TAKE_PROFIT"
        elif sl_on and pnl_pct <= sl:
            trigger = "STOP_LOSS"
        if not trigger:
            continue
        res = broker.place_order_overseas(p.symbol, "SELL", qty, cur, execute=gate_on)
        ok = res.status == "KIS_ORDER_SUBMITTED" or (not gate_on)
        results.append(OverseasExecResult(
            symbol=p.symbol, side="SELL", quantity=qty, limit_price_usd=cur,
            status=f"{trigger}:{res.status}", success=ok, message=res.message,
            dry_run=not gate_on, order_id=res.broker_order_id,
        ))
    return results


def format_overseas_exec_telegram(results: list[OverseasExecResult]) -> str | None:
    """실행 결과 텔레그램 메시지 (성공·실주문만)."""
    shown = [r for r in results if r.success and not r.dry_run]
    if not shown:
        return None
    msgs = []
    for r in shown:
        ticker  = r.symbol.split(":")[-1].strip().upper()
        kr_name = _us_name_kr(r.symbol)
        is_sell = r.side == "SELL"
        icon    = "📉" if is_sell else "📈"
        side_ko = "매도" if is_sell else "매수"
        amount  = r.quantity * r.limit_price_usd
        name_str = f"{ticker} ({kr_name})" if kr_name != ticker else ticker
        msgs.append(
            f"{icon} <b>[해외주식] {side_ko} 체결</b>\n"
            f"종목: {name_str}\n"
            f"수량: {r.quantity:,}주 × ${r.limit_price_usd:,.2f}\n"
            f"금액: ${amount:,.2f}\n"
            f"시각: {_now_kst_iso()}"
        )
    return "\n\n".join(msgs)


def format_overseas_fail_telegram(symbol: str, side: str, reason: str) -> str:
    """해외주식 실패 알림."""
    ticker  = (symbol or "").split(":")[-1].strip().upper()
    kr_name = _us_name_kr(symbol)
    is_sell = side == "SELL"
    side_ko = "매도" if is_sell else "매수"
    name_str = f"{ticker} ({kr_name})" if kr_name != ticker else ticker
    return (
        f"⚠️ <b>[해외주식] {side_ko} 실패</b>\n"
        f"종목: {name_str}\n"
        f"사유: {reason}\n"
        f"시각: {_now_kst_iso()}"
    )


def is_us_market_open() -> bool:
    """미국 정규장(22:30~05:00 KST, 평일) 여부."""
    from datetime import datetime
    now = datetime.now(_KST)
    h, m, wd = now.hour, now.minute, now.weekday()
    in_time = (h == 22 and m >= 30) or (h == 23) or (0 <= h < 5) or (h == 5 and m == 0)
    if h >= 22:
        return in_time and wd not in (5, 6)   # 금·토 밤 제외
    return in_time and wd not in (6, 0)        # 일·월 새벽 제외


def run_overseas_auto_tick(output_dir: str | Path, *, tg_notify=None,
                           force_market: bool = False) -> dict[str, Any]:
    """무인 러너 1틱: 분석→plan→매수→TP/SL매도. 미국 장시간 + 일시정지 체크.

    Args:
        tg_notify: 텔레그램 전송 콜백 (text) → None. 없으면 알림 생략.
        force_market: True면 미국 장시간 게이트를 우회 (검증/테스트용).
                      실주문 게이트(OVERSEAS_AUTO_EXECUTE_WITHOUT_APPROVAL)는
                      그대로 적용되므로, 게이트 OFF면 dry-run만 수행.
    Returns:
        실행 요약 dict.
    """
    out = Path(output_dir)
    # 1) 미국 장시간 체크 (force_market이면 우회)
    if not force_market and not is_us_market_open():
        return {"skipped": "market_closed"}
    # 2) 전체 일시정지 체크 (코인·국내와 공유)
    if is_runner_paused(out):
        return {"skipped": "paused"}

    summary: dict[str, Any] = {"buy": [], "sell": []}

    # 3) TP/SL 자동매도 먼저 (리스크 관리 우선)
    try:
        sells = auto_sell_overseas(out)
        summary["sell"] = [r.__dict__ for r in sells]
        msg = format_overseas_exec_telegram(sells)
        if msg and tg_notify:
            tg_notify(msg)
    except Exception as exc:
        summary["sell_error"] = str(exc)

    # 4) plan 생성 + 매수 실행
    try:
        from deepsignal.live_trading.overseas_plan import build_overseas_order_plan
        usd_rate = 1350.0
        try:
            from deepsignal.live_trading.broker.kis_broker import KISBroker
            from deepsignal.live_trading.broker.kis_config import load_kis_config_from_env
            _br = KISBroker(load_kis_config_from_env(), safe_mode=True)
            _bal = _br.get_overseas_cash_balance()
            _avail = float(_bal.cash) if _bal.cash else None
        except Exception:
            _avail = None
        build_overseas_order_plan(out, usd_rate=usd_rate, available_cash_usd=_avail)
        buys = execute_overseas_plan(out)
        summary["buy"] = [r.__dict__ for r in buys]
        msg = format_overseas_exec_telegram(buys)
        if msg and tg_notify:
            tg_notify(msg)
    except Exception as exc:
        summary["buy_error"] = str(exc)

    return summary
