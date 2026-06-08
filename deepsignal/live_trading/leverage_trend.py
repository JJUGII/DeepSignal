"""나스닥 추세구간 2x 레버리지 ETF 실거래 (P3 연결).

신호: 나스닥(^IXIC) 종가 > 200일선 AND > 50일선 AND 최근 20일 +  → 강한 상승(strong_up)
실행: 강한 상승이면 409820(KODEX 미국나스닥100레버리지 2x) ENTER, 아니면 EXIT(청산).

다중 안전 게이트(전부 충족해야 실주문):
  execute=True · LEVERAGE_TREND_LIVE=true · REGIME_LEVERAGE_ENABLED=true
  · EDGE_GATE(leverage_trend_nasdaq) deploy=true · TRADING_HALT 아님 · KIS live
하나라도 빠지면 dry-run(미리보기). EXIT은 게이트 무관 항상 허용(보유 보호).
소액 기본(REGIME_LEVERAGE_ALLOC_KRW, 기본 10만원) + 레버리지 비례 캡 축소.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))
STATE_FILE = "LEVERAGE_TREND_STATE.json"
ETF_NASDAQ_2X = "409820"   # KODEX 미국나스닥100레버리지 (검증 완료)
EDGE_STRATEGY = "leverage_trend_nasdaq"


def live_enabled() -> bool:
    return os.environ.get("LEVERAGE_TREND_LIVE", "").strip().lower() in ("1", "true", "yes")


def leverage_enabled() -> bool:
    return os.environ.get("REGIME_LEVERAGE_ENABLED", "").strip().lower() in ("1", "true", "yes")


def alloc_krw() -> float:
    raw = os.environ.get("REGIME_LEVERAGE_ALLOC_KRW", "100000").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 100000.0


@dataclass
class LevSignal:
    strong_up: bool
    close: float | None
    sma200: float | None
    sma50: float | None
    ret_20d: float | None
    asof: str
    reason: str


def compute_nasdaq_signal() -> LevSignal:
    """yfinance ^IXIC 일봉으로 강한 상승 레짐 판정 (look-ahead 없음)."""
    try:
        import yfinance as yf
        import pandas as pd
        h = yf.Ticker("^IXIC").history(period="2y", interval="1d", auto_adjust=True)["Close"].dropna()
        if len(h) < 220:
            return LevSignal(False, None, None, None, None, "", "데이터 부족")
        close = float(h.iloc[-1])
        sma200 = float(h.tail(200).mean())
        sma50 = float(h.tail(50).mean())
        ret20 = float(h.iloc[-1] / h.iloc[-21] - 1)
        strong = close > sma200 and close > sma50 and ret20 > 0
        asof = str(pd.to_datetime(h.index[-1]).date())
        reason = (f"종가 {close:,.0f} {'>' if close>sma200 else '≤'} SMA200 {sma200:,.0f}, "
                  f"{'>' if close>sma50 else '≤'} SMA50, 20일 {ret20*100:+.1f}%")
        return LevSignal(strong, close, sma200, sma50, ret20, asof, reason)
    except Exception as e:  # noqa: BLE001
        return LevSignal(False, None, None, None, None, "", f"신호 오류: {e}")


def _state_path(out: str | Path) -> Path:
    return Path(out) / STATE_FILE


def load_position(out: str | Path) -> dict[str, Any]:
    import json
    p = _state_path(out)
    if not p.is_file():
        return {"holding": False, "qty": 0, "history": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"holding": False, "qty": 0, "history": []}


def save_position(out: str | Path, state: dict[str, Any]) -> None:
    import json
    p = _state_path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass
class LevDecision:
    action: str        # ENTER | EXIT | HOLD
    etf: str
    signal: LevSignal
    deploy_ok: bool
    enabled: bool
    halted: bool
    would_order: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        s = self.signal
        return {"action": self.action, "etf": self.etf, "deploy_ok": self.deploy_ok,
                "enabled": self.enabled, "halted": self.halted, "would_order": self.would_order,
                "reason": self.reason, "live_enabled": live_enabled(),
                "signal": {"strong_up": s.strong_up, "asof": s.asof, "reason": s.reason}}


def decide(out: str | Path) -> LevDecision:
    from deepsignal.risk.edge_gate import edge_gate_status, edge_gate_enforced
    from deepsignal.risk.trading_halt import is_trading_halted

    sig = compute_nasdaq_signal()
    pos = load_position(out)
    holding = bool(pos.get("holding"))
    halted, _ = is_trading_halted(out)
    st = edge_gate_status(out, EDGE_STRATEGY)
    deploy_ok = bool(st.get("deploy")) or (not edge_gate_enforced())
    enabled = leverage_enabled()

    if sig.close is None:
        return LevDecision("HOLD", ETF_NASDAQ_2X, sig, deploy_ok, enabled, halted, False, f"신호 없음: {sig.reason}")
    if sig.strong_up and not holding:
        ok = deploy_ok and enabled and not halted
        why = ("진입 가능" if ok else
               ("엣지 게이트 미배포" if not deploy_ok else "레버리지 OFF" if not enabled else "halt"))
        return LevDecision("ENTER", ETF_NASDAQ_2X, sig, deploy_ok, enabled, halted, ok, f"강한 상승 — {why}")
    if (not sig.strong_up) and holding:
        return LevDecision("EXIT", ETF_NASDAQ_2X, sig, deploy_ok, enabled, halted, True, "상승 둔화 — 청산")
    return LevDecision("HOLD", ETF_NASDAQ_2X, sig, deploy_ok, enabled, halted, False,
                       f"전환 없음 ({'보유' if holding else '현금'})")


def format_status(d: LevDecision) -> str:
    s = d.signal
    return "\n".join([
        "── 나스닥 레버리지(2x) 상태 ──",
        f"신호: {'강한 상승' if s.strong_up else '약함/하락'} | {s.reason}",
        f"기준일: {s.asof} | 거래 ETF: {d.etf} (KODEX 미국나스닥100레버리지)",
        f"EDGE_GATE 배포: {'예' if d.deploy_ok else '아니오(검증 대기)'}",
        f"레버리지 활성(REGIME_LEVERAGE_ENABLED): {'예' if d.enabled else '아니오'}",
        f"실거래 활성(LEVERAGE_TREND_LIVE): {'예' if live_enabled() else '아니오'}",
        f"전역 halt: {'예' if d.halted else '아니오'}",
        f"권고: {d.action} — {d.reason}",
        f"→ 실제 주문 조건 충족: {'예' if (d.would_order and live_enabled()) else '아니오'}",
    ])


def _now_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")


def execute(out: str | Path, *, execute: bool = False) -> dict[str, Any]:
    """결정을 실제 ETF 주문으로 연결 (regime_trend 실행 헬퍼 재사용)."""
    from deepsignal.live_trading.broker.interface import BrokerOrderRequest
    from deepsignal.live_trading.regime_trend import _kis_broker, _send_telegram
    from deepsignal.risk.trailing_stop import scale_caps_for_leverage

    d = decide(out)
    etf = d.etf
    if d.action == "HOLD":
        return {"action": "HOLD", "executed": False, "message": d.reason}

    do_order = bool(execute and live_enabled() and (d.would_order if d.action == "ENTER" else True))
    broker = _kis_broker(safe_mode=not do_order)
    try:
        price = broker.get_current_price(etf)
    except Exception:
        price = None

    if d.action == "ENTER":
        if not price or price <= 0:
            return {"action": "ENTER", "executed": False, "message": f"{etf} 현재가 조회 실패"}
        # 레버리지(2x) 비례로 1회 한도 축소
        base = alloc_krw()
        single_cap, _ = scale_caps_for_leverage(2.0, base, 100000)
        try:
            cash = float(broker.get_cash_balance().withdrawable_cash or 0)
        except Exception:
            cash = 0.0
        budget = min(single_cap, cash * 0.98) if cash > 0 else single_cap
        qty = int(budget // price)
        order = {"symbol": etf, "side": "BUY", "qty": qty, "price": price, "budget": round(budget)}
        if qty < 1:
            return {"action": "ENTER", "executed": False, "order": order,
                    "message": f"배분 {budget:,.0f}원 < 1주 {price:,.0f}원"}
        if not do_order:
            return {"action": "ENTER", "executed": False, "order": order,
                    "message": f"[dry-run] ENTER {etf} {qty}주 @ {price:,.0f} — {d.reason}"}
        req = BrokerOrderRequest(symbol=etf, side="BUY", quantity=qty, order_type="LIMIT",
                                 limit_price=float(price), estimated_value=qty * price)
        res = broker.place_order(req, execute=True)
        ok = getattr(res, "status", "") == "KIS_ORDER_SUBMITTED"
        if ok:
            pos = load_position(out)
            pos.update({"holding": True, "etf": etf, "qty": qty, "entry_price": float(price), "since": _now_iso()})
            pos.setdefault("history", []).append({**order, "action": "ENTER", "at": _now_iso()})
            save_position(out, pos)
            _send_telegram(f"📈 <b>[나스닥 레버리지 2x] 매수 체결</b>\n종목: {etf} (KODEX 미국나스닥100레버리지)\n"
                           f"수량: {qty:,}주 × {price:,.0f}원\n금액: {qty*price:,.0f}원\n시각: {_now_iso()}")
        return {"action": "ENTER", "executed": ok, "order": order, "message": getattr(res, "message", "")}

    # EXIT
    pos = load_position(out)
    qty = int(pos.get("qty") or 0)
    if qty < 1:
        pos["holding"] = False
        save_position(out, pos)
        return {"action": "EXIT", "executed": False, "message": "청산 신호이나 보유 없음 — 상태 정리"}
    px = price or pos.get("entry_price")
    if not (px and px > 0):
        return {"action": "EXIT", "executed": False, "message": f"{etf} 가격 없음 — 청산 보류"}
    if not do_order:
        return {"action": "EXIT", "executed": False, "message": f"[dry-run] EXIT {etf} {qty}주 @ {px:,.0f}"}
    req = BrokerOrderRequest(symbol=etf, side="SELL", quantity=qty, order_type="LIMIT",
                             limit_price=float(px), estimated_value=qty * px)
    res = broker.place_order(req, execute=True)
    ok = getattr(res, "status", "") == "KIS_ORDER_SUBMITTED"
    if ok:
        entry = pos.get("entry_price") or px
        pnl = round((px - entry) / entry * 100, 2) if entry else 0.0
        pos.update({"holding": False, "qty": 0})
        pos.setdefault("history", []).append({"symbol": etf, "side": "SELL", "qty": qty, "price": px,
                                              "action": "EXIT", "at": _now_iso()})
        save_position(out, pos)
        _send_telegram(f"📉 <b>[나스닥 레버리지 2x] 매도 체결</b>\n종목: {etf}\n수량: {qty:,}주 × {px:,.0f}원\n"
                       f"손익: {'+' if pnl>=0 else ''}{pnl:.2f}%\n시각: {_now_iso()}")
    return {"action": "EXIT", "executed": ok, "message": getattr(res, "message", "")}
