"""추세추종(regime trend) 실거래 경로.

전체 엣지 리서치에서 **유일하게 robust한 엣지**(S&P500 200일선 추세추종, 98년 OOS
Sharpe 0.73 vs 0.42, 생존편향 없음)를 실제 운용으로 연결한다.

신호: 지수(S&P500) 종가 > 200일 이동평균 → 보유(in-market), 아니면 현금(out).
실행: 지수를 추종하는 ETF를 매수/매도. 한국 상장 S&P500 ETF(예: 360750 TIGER 미국S&P500)를
      KIS 국내 경로로 거래하면 기존 가드(가격괴리·취소·order_guard)를 그대로 재사용한다.

**안전(기본 닫힘):**
- EDGE_GATE의 `regime_trend_sp500`이 deploy=true(엣지 연속 검증)일 때만 신규 진입 허용.
- 전역 킬스위치(TRADING_HALT) 적용.
- `REGIME_TREND_LIVE=true` env가 없으면 status/plan만(주문 없음).
신호 전환(out→in 매수 / in→out 매도)이 있을 때만 1회 주문. 데이/주간 단위로 드물게 발생.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))
STATE_FILE = "REGIME_TREND_STATE.json"

# 기본 거래 ETF: 한국 상장 S&P500 추종 (KIS 국내 6자리). env로 변경 가능.
_DEFAULT_ETF = "360750"  # TIGER 미국S&P500


@dataclass
class RegimeTrendSignal:
    in_market: bool
    index_close: float | None
    sma200: float | None
    asof: str
    source: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "in_market": self.in_market, "index_close": self.index_close,
            "sma200": self.sma200, "asof": self.asof, "source": self.source,
            "reason": self.reason,
        }


def compute_trend_signal(db_path: str | None = None, *, sma_window: int = 200) -> RegimeTrendSignal:
    """economic_indicators의 S&P500 일봉으로 200일선 추세 신호 계산.

    look-ahead 없음(최신 종가와 그 시점까지의 SMA200 비교).
    """
    import sqlite3

    from deepsignal.config.settings import load_settings

    path = db_path or load_settings().db_path
    try:
        conn = sqlite3.connect(str(path))
        rows = conn.execute(
            "SELECT indicator_date, value FROM economic_indicators "
            "WHERE indicator_name='SP500' AND value IS NOT NULL "
            "ORDER BY indicator_date DESC LIMIT ?", (sma_window + 5,)).fetchall()
        conn.close()
    except sqlite3.Error as e:  # noqa: BLE001
        return RegimeTrendSignal(False, None, None, "", "error", f"DB 오류: {e}")
    if len(rows) < sma_window:
        return RegimeTrendSignal(False, None, None, "", "insufficient",
                                 f"SP500 봉 부족 ({len(rows)}<{sma_window})")
    rows = rows[::-1]  # 오름차순
    closes = [float(r[1]) for r in rows]
    last_close = closes[-1]
    sma = sum(closes[-sma_window:]) / sma_window
    in_mkt = last_close > sma
    return RegimeTrendSignal(
        in_market=in_mkt, index_close=last_close, sma200=sma,
        asof=str(rows[-1][0])[:10], source="economic_indicators.SP500",
        reason=f"종가 {last_close:,.1f} {'>' if in_mkt else '≤'} SMA{sma_window} {sma:,.1f}",
    )


def _state_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / STATE_FILE


def load_position(output_dir: str | Path) -> dict[str, Any]:
    p = _state_path(output_dir)
    if not p.is_file():
        return {"holding": False, "etf": "", "since": "", "history": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"holding": False, "etf": "", "since": "", "history": []}


def save_position(output_dir: str | Path, state: dict[str, Any]) -> None:
    p = _state_path(output_dir)
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


def regime_trend_etf() -> str:
    return os.environ.get("REGIME_TREND_ETF", _DEFAULT_ETF).strip() or _DEFAULT_ETF


def regime_trend_live_enabled() -> bool:
    return os.environ.get("REGIME_TREND_LIVE", "").strip().lower() in ("1", "true", "yes")


@dataclass
class RegimeTrendDecision:
    action: str            # ENTER | EXIT | HOLD
    etf: str
    signal: RegimeTrendSignal
    deploy_ok: bool
    halted: bool
    reason: str
    would_order: bool      # 실제 주문 조건 충족(전환+허가)인지
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action, "etf": self.etf, "signal": self.signal.to_dict(),
            "deploy_ok": self.deploy_ok, "halted": self.halted, "reason": self.reason,
            "would_order": self.would_order, "live_enabled": regime_trend_live_enabled(),
            **self.extra,
        }


def decide_regime_trend(output_dir: str | Path, *, db_path: str | None = None) -> RegimeTrendDecision:
    """신호 + 보유상태 + 게이트로 다음 행동 결정 (주문은 하지 않음).

    - in-market 인데 미보유 → ENTER (EDGE_GATE deploy=true & halt 아님일 때만 would_order)
    - out 인데 보유 → EXIT (청산은 게이트 무관, halt 무관 — 항상 허용)
    - 그 외 → HOLD
    """
    from deepsignal.risk.edge_gate import edge_gate_allows_buy, strategy_for_live
    from deepsignal.risk.trading_halt import is_trading_halted

    sig = compute_trend_signal(db_path)
    pos = load_position(output_dir)
    holding = bool(pos.get("holding"))
    etf = regime_trend_etf()
    halted, hreason = is_trading_halted(output_dir)
    deploy_ok, ereason = edge_gate_allows_buy(output_dir, strategy_for_live("regime_trend"))

    if sig.source in ("error", "insufficient"):
        return RegimeTrendDecision("HOLD", etf, sig, deploy_ok, halted,
                                   f"신호 없음: {sig.reason}", False)

    if sig.in_market and not holding:
        ok = deploy_ok and not halted
        reason = ("진입 신호 — " + ("주문 가능" if ok else
                  ("엣지 게이트 차단" if not deploy_ok else "halt 중")))
        return RegimeTrendDecision("ENTER", etf, sig, deploy_ok, halted, reason, ok)
    if (not sig.in_market) and holding:
        # 청산은 항상 허용(보유분 보호)
        return RegimeTrendDecision("EXIT", etf, sig, deploy_ok, halted, "이탈 신호 — 청산", True)
    state = "보유 유지" if holding else "현금 유지"
    return RegimeTrendDecision("HOLD", etf, sig, deploy_ok, halted, f"전환 없음 ({state})", False)


def _now_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")


def _send_telegram(text: str) -> None:
    """추세추종 체결 알림 — .env의 Telegram 봇/채팅 ID로 발송 (실패 시 조용히 무시)."""
    try:
        from pathlib import Path as _P
        from dotenv import load_dotenv as _ld
        env_path = _P(__file__).parents[3] / ".env"
        _ld(str(env_path), override=False)
        token   = os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            return
        from deepsignal.live_trading.telegram.approval import telegram_api_post
        telegram_api_post(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            bot_token=token,
        )
    except Exception:  # noqa: BLE001
        pass


def regime_trend_alloc_krw() -> float:
    """진입 1회 배분 금액(KRW). env REGIME_TREND_ALLOC_KRW (기본 30만)."""
    raw = os.environ.get("REGIME_TREND_ALLOC_KRW", "300000").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 300000.0


@dataclass
class RegimeTrendExecResult:
    action: str
    executed: bool      # 실제 KIS POST 성공
    dry_run: bool
    order: dict[str, Any] | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "executed": self.executed, "dry_run": self.dry_run,
                "order": self.order, "message": self.message}


def _kis_broker(*, safe_mode: bool):
    from deepsignal.live_trading.broker.kis_broker import KISBroker
    from deepsignal.live_trading.broker.kis_config import load_kis_config_from_env

    return KISBroker(load_kis_config_from_env(), safe_mode=safe_mode)


def execute_regime_trend(
    output_dir: str | Path, *, execute: bool = False, db_path: str | None = None
) -> RegimeTrendExecResult:
    """추세추종 결정을 실제 ETF 주문으로 연결.

    실주문 조건(전부 충족): execute=True · REGIME_TREND_LIVE=true ·
    (ENTER면 EDGE_GATE deploy & halt 아님) · KIS_ENV=live(브로커가 강제).
    하나라도 빠지면 dry-run(미리보기). EXIT(청산)는 게이트 무관 항상 시도.
    """
    from deepsignal.live_trading.broker.interface import BrokerOrderRequest

    decision = decide_regime_trend(output_dir, db_path=db_path)
    etf = decision.etf
    live = regime_trend_live_enabled()

    if decision.action == "HOLD":
        return RegimeTrendExecResult("HOLD", False, True, None, decision.reason)

    do_order = bool(execute and live and (decision.would_order if decision.action == "ENTER" else True))
    broker = _kis_broker(safe_mode=not do_order)

    # 현재가 (KIS 실시간 호가)
    price = None
    try:
        price = broker.get_current_price(etf)
    except Exception:  # noqa: BLE001
        price = None

    if decision.action == "ENTER":
        if not price or price <= 0:
            return RegimeTrendExecResult("ENTER", False, True, None,
                                         f"{etf} 현재가 조회 실패 — 주문 보류 ({decision.reason})")
        alloc = regime_trend_alloc_krw()
        # 가용 KIS 현금으로 상한(전액 모드: alloc을 크게 두면 현금이 한도). 2% 버퍼.
        try:
            _cash = float(broker.get_cash_balance().withdrawable_cash or 0)
        except Exception:  # noqa: BLE001
            _cash = 0.0
        effective = min(alloc, _cash * 0.98) if _cash > 0 else alloc
        qty = int(effective // price)
        order = {"symbol": etf, "side": "BUY", "qty": qty, "price": price,
                 "alloc_krw": alloc, "cash_krw": _cash, "effective_krw": round(effective)}
        if qty < 1:
            return RegimeTrendExecResult("ENTER", False, True, order,
                                         f"배분금 {alloc:,.0f}원 < 1주 가격 {price:,.0f}원")
        if not do_order:
            why = "엣지 게이트 차단" if not decision.would_order else ("LIVE 미설정" if not live else "execute 미지정")
            return RegimeTrendExecResult("ENTER", False, True, order,
                                         f"[dry-run] ENTER {etf} {qty}주 @ {price:,.0f}원 — {why}")
        req = BrokerOrderRequest(symbol=etf, side="BUY", quantity=qty, order_type="LIMIT",
                                 limit_price=float(price), estimated_value=qty * price)
        res = broker.place_order(req, execute=True)
        ok = getattr(res, "status", "") == "KIS_ORDER_SUBMITTED"
        if ok:
            pos = load_position(output_dir)
            remaining_cash = max(0.0, _cash - qty * float(price))
            pos.update({"holding": True, "etf": etf, "qty": qty, "since": _now_iso(),
                        "entry_price": float(price),
                        "cash_after_buy": round(remaining_cash, 0)})
            pos.setdefault("history", []).append({**order, "action": "ENTER", "at": _now_iso()})
            save_position(output_dir, pos)
            _send_telegram(
                f"📈 <b>[추세추종] ETF 매수 체결</b>\n"
                f"종목: {etf} (TIGER 미국S&P500)\n"
                f"수량: {qty:,}주 × {price:,.0f}원\n"
                f"금액: {qty * price:,.0f}원\n"
                f"신호: S&P500 {decision.signal.index_close:,.1f} > SMA200 {decision.signal.sma200:,.1f}\n"
                f"시각: {_now_iso()}"
            )
        else:
            _send_telegram(
                f"⚠️ <b>[추세추종] ETF 매수 실패</b>\n"
                f"종목: {etf} | {qty}주 @ {price:,.0f}원\n"
                f"응답: {getattr(res, 'message', '?')}"
            )
        return RegimeTrendExecResult("ENTER", ok, False, order, getattr(res, "message", ""))

    # EXIT (청산)
    pos = load_position(output_dir)
    qty = int(pos.get("qty") or 0)
    if qty < 1:
        pos["holding"] = False
        save_position(output_dir, pos)
        return RegimeTrendExecResult("EXIT", False, True, None,
                                     "청산 신호이나 보유 수량 기록 없음 — 상태만 정리")
    px = price or pos.get("entry_price")
    order = {"symbol": etf, "side": "SELL", "qty": qty, "price": px}
    if not (px and px > 0):
        return RegimeTrendExecResult("EXIT", False, True, order, f"{etf} 가격 없음 — 청산 보류")
    if not do_order:
        return RegimeTrendExecResult("EXIT", False, True, order,
                                     f"[dry-run] EXIT {etf} {qty}주 @ {px:,.0f}원 (LIVE/execute 미설정)")
    req = BrokerOrderRequest(symbol=etf, side="SELL", quantity=qty, order_type="LIMIT",
                             limit_price=float(px), estimated_value=qty * px)
    res = broker.place_order(req, execute=True)
    ok = getattr(res, "status", "") == "KIS_ORDER_SUBMITTED"
    entry_px = pos.get("entry_price") or px
    pnl_pct = round((px - entry_px) / entry_px * 100, 2) if entry_px and entry_px > 0 else None
    if ok:
        pos.update({"holding": False, "qty": 0})
        pos.setdefault("history", []).append({**order, "action": "EXIT", "at": _now_iso()})
        save_position(output_dir, pos)
        pnl_str = f"손익: {'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%\n" if pnl_pct is not None else ""
        _send_telegram(
            f"📉 <b>[추세추종] ETF 매도 체결</b>\n"
            f"종목: {etf} (TIGER 미국S&P500)\n"
            f"수량: {qty:,}주 × {px:,.0f}원\n"
            f"금액: {qty * px:,.0f}원\n"
            f"{pnl_str}"
            f"신호: S&P500 200일선 하향 돌파\n"
            f"시각: {_now_iso()}"
        )
    else:
        _send_telegram(
            f"⚠️ <b>[추세추종] ETF 매도 실패</b>\n"
            f"종목: {etf} | {qty}주 @ {px:,.0f}원\n"
            f"응답: {getattr(res, 'message', '?')}"
        )
    return RegimeTrendExecResult("EXIT", ok, False, order, getattr(res, "message", ""))


def format_status(decision: RegimeTrendDecision) -> str:
    s = decision.signal
    lines = [
        "── 추세추종(regime_trend) 상태 ──",
        f"신호: {'IN-MARKET(보유)' if s.in_market else 'OUT(현금)'} | {s.reason}",
        f"기준일: {s.asof} | 소스: {s.source}",
        f"거래 ETF: {decision.etf}",
        f"EDGE_GATE 배포: {'예' if decision.deploy_ok else '아니오(검증 대기/차단)'}",
        f"전역 halt: {'예' if decision.halted else '아니오'}",
        f"권고 행동: {decision.action} — {decision.reason}",
        f"실거래 활성(REGIME_TREND_LIVE): {'예' if regime_trend_live_enabled() else '아니오(상태만)'}",
        f"→ 실제 주문 조건 충족: {'예' if (decision.would_order and regime_trend_live_enabled()) else '아니오'}",
    ]
    return "\n".join(lines)
