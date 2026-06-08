"""Telegram approval + execute for crypto orders (separate from KIS telegram_approval)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

from deepsignal.crypto_trading.crypto_order_plan import CRYPTO_PLAN_JSON, CryptoOrderPlan, load_crypto_plan
from deepsignal.crypto_trading.upbit_broker import UpbitBroker, UpbitOrderResult
from deepsignal.live_trading.time_utils import now_kst, now_kst_iso, parse_datetime_with_default_tz

CRYPTO_APPROVAL_JSON = "crypto_telegram_approval_request.json"
CRYPTO_APPROVAL_AUDIT_JSON_PREFIX = "crypto_telegram_approval_audit_"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"

ACTION_APPROVE = "crypto_approve"
ACTION_REJECT = "crypto_reject"


@dataclass
class CryptoTelegramConfig:
    output_dir: str = "outputs"
    bot_token: str | None = None
    allowed_chat_id: str | None = None
    expires_minutes: int = 240
    max_orders_per_day: int = 3
    timeout_seconds: float = 10.0
    send: bool = False
    poll: bool = False
    poll_interval: float = 3.0
    wait_fill_seconds: float = 0.0
    fill_poll_interval: float = 3.0


@dataclass
class CryptoApprovalRequest:
    token: str
    plan_path: str
    plan_hash: str
    status: str
    created_at: str
    expires_at: str
    market: str
    display_name: str
    krw_amount: float
    current_price: float
    reason: str
    message_text: str = ""
    telegram_result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_crypto_telegram_config_from_env(*, output_dir: str = "outputs") -> CryptoTelegramConfig:
    expires = int(os.environ.get("DEEPSIGNAL_APPROVAL_EXPIRES_MINUTES") or 240)
    return CryptoTelegramConfig(
        output_dir=output_dir,
        bot_token=(os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN") or "").strip() or None,
        allowed_chat_id=(os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID") or "").strip() or None,
        expires_minutes=expires,
    )


def _plan_hash(plan_path: Path) -> str:
    return hashlib.sha256(plan_path.read_bytes()).hexdigest()


def format_approval_message(plan: CryptoOrderPlan) -> str:
    if plan.side.lower() == "sell":
        trigger = (plan.sell_trigger or "").lower()
        tp = float(plan.take_profit_pct or 0)
        if trigger == "near_take_profit" and tp > 0:
            return "\n".join(
                [
                    "[DeepSignal 코인 익절 근접 매도 승인]",
                    f"{plan.display_name} 매도 추천",
                    f"• 수익률: {plan.pnl_pct:+.2f}%",
                    f"• 익절 기준: {tp:+.2f}%",
                    "• 현재 익절 기준에 거의 도달했습니다.",
                    "• 일부 또는 전량 매도를 검토합니다.",
                    "",
                    "승인 시 업비트 매도 주문이 실행됩니다.",
                ]
            )
        if trigger == "near_stop_loss":
            sl = float(plan.stop_loss_pct or 0)
            return "\n".join(
                [
                    "[DeepSignal 코인 손절 근접 매도 승인]",
                    f"{plan.display_name} 매도 추천",
                    f"• 수익률: {plan.pnl_pct:+.2f}%",
                    f"• 손절 기준: {sl:+.2f}%",
                    "• 손절 기준에 근접했습니다.",
                    "• 일부 또는 전량 매도를 검토합니다.",
                    "",
                    "승인 시 업비트 매도 주문이 실행됩니다.",
                ]
            )
        header = (
            "[DeepSignal 코인 손절 승인]"
            if plan.pnl_pct < 0
            else "[DeepSignal 코인 매도 승인]"
        )
        cur = plan.market.split("-")[-1] if "-" in plan.market else plan.market
        return "\n".join(
            [
                header,
                f"{plan.display_name} 매도 추천",
                f"• 보유수량: {plan.volume} {cur}",
                f"• 평균매수가: {plan.avg_buy_price:,.0f}원",
                f"• 현재가: {plan.limit_price:,.0f}원",
                f"• 수익률: {plan.pnl_pct:+.2f}%",
                f"• 예상금액: 약 {plan.krw_amount:,.0f}원",
                f"• 이유: {plan.reason}",
                "",
                "승인 시 업비트 매도 주문이 실행됩니다.",
            ]
        )
    score_lines: list[str] = []
    if plan.final_score is not None:
        score_lines.append(f"• final score: {plan.final_score:+.1f}")
    if plan.technical_score is not None:
        score_lines.append(f"• technical: {plan.technical_score:+.1f}")
    if plan.macro_score is not None:
        score_lines.append(f"• macro: {plan.macro_score:+.1f}")
    if plan.macro_regime:
        score_lines.append(f"• regime: {plan.macro_regime}")
    gates = plan.quality_gates if isinstance(plan.quality_gates, dict) else {}
    if gates:
        score_lines.append(
            "• gates: "
            + ", ".join(f"{k}={v}" for k, v in sorted(gates.items()) if k in ("validation", "liquidity"))
        )
    return "\n".join(
        [
            "[DeepSignal 코인 매매 승인]",
            f"{plan.display_name} 매수 추천",
            f"• 마켓: {plan.market}",
            f"• 주문금액: {plan.krw_amount:,.0f}원",
            f"• 현재가: {plan.limit_price:,.0f}원",
            *score_lines,
            f"• 이유: {plan.reason}",
            "",
            "승인 시에만 업비트 매수 주문이 실행됩니다.",
        ]
    )


def _inline_keyboard(token: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "승인", "callback_data": f"{ACTION_APPROVE}:{token}"},
                {"text": "거부", "callback_data": f"{ACTION_REJECT}:{token}"},
            ]
        ]
    }


def telegram_send_message(
    cfg: CryptoTelegramConfig,
    text: str,
    *,
    token: str,
    plain: bool = False,
) -> dict[str, Any]:
    if not cfg.bot_token or not cfg.allowed_chat_id:
        return {"ok": False, "error": "telegram not configured"}
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": cfg.allowed_chat_id,
        "text": text,
    }
    if not plain:
        payload["reply_markup"] = json.dumps(_inline_keyboard(token), ensure_ascii=False)
    resp = requests.post(url, data=payload, timeout=cfg.timeout_seconds)
    try:
        return resp.json()
    except Exception:
        return {"ok": False, "status_code": resp.status_code, "text": resp.text[:300]}


def telegram_send_plain(cfg: CryptoTelegramConfig, text: str) -> dict[str, Any]:
    return telegram_send_message(cfg, text, token="", plain=True)


def telegram_send_html(cfg: CryptoTelegramConfig, text: str) -> dict[str, Any]:
    """HTML 파싱 모드로 전송 (체결 알림 등 <b> 태그 포함 메시지용)."""
    if not cfg.bot_token or not cfg.allowed_chat_id:
        return {"ok": False, "error": "telegram not configured"}
    import requests as _req
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    resp = _req.post(url, data={
        "chat_id": cfg.allowed_chat_id,
        "text": text,
        "parse_mode": "HTML",
    }, timeout=cfg.timeout_seconds)
    try:
        return resp.json()
    except Exception:
        return {"ok": False, "status_code": resp.status_code}


def create_crypto_approval_request(
    plan: CryptoOrderPlan,
    *,
    cfg: CryptoTelegramConfig,
    plan_path: Path,
) -> CryptoApprovalRequest:
    token = secrets.token_urlsafe(16)
    now = now_kst()
    expires = now + __import__("datetime").timedelta(minutes=int(cfg.expires_minutes))
    req = CryptoApprovalRequest(
        token=token,
        plan_path=plan_path.as_posix(),
        plan_hash=_plan_hash(plan_path),
        status=STATUS_PENDING,
        created_at=now_kst_iso(),
        expires_at=expires.isoformat(),
        market=plan.market,
        display_name=plan.display_name,
        krw_amount=plan.krw_amount,
        current_price=plan.limit_price,
        reason=plan.reason,
        message_text=format_approval_message(plan),
    )
    out = Path(cfg.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / CRYPTO_APPROVAL_JSON
    path.write_text(json.dumps(req.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if cfg.send:
        req.telegram_result = telegram_send_message(cfg, req.message_text, token=token)
    return req


def load_crypto_approval_request(output_dir: str | Path) -> CryptoApprovalRequest | None:
    path = Path(output_dir) / CRYPTO_APPROVAL_JSON
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return CryptoApprovalRequest(
        token=str(data.get("token", "")),
        plan_path=str(data.get("plan_path", "")),
        plan_hash=str(data.get("plan_hash", "")),
        status=str(data.get("status", STATUS_PENDING)),
        created_at=str(data.get("created_at", "")),
        expires_at=str(data.get("expires_at", "")),
        market=str(data.get("market", "")),
        display_name=str(data.get("display_name", "")),
        krw_amount=float(data.get("krw_amount", 0) or 0),
        current_price=float(data.get("current_price", 0) or 0),
        reason=str(data.get("reason", "")),
        message_text=str(data.get("message_text", "")),
        telegram_result=dict(data.get("telegram_result") or {}),
    )


def _save_request(output_dir: str | Path, req: CryptoApprovalRequest) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    (Path(output_dir) / CRYPTO_APPROVAL_JSON).write_text(
        json.dumps(req.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_audit(output_dir: str | Path, payload: dict[str, Any]) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = now_kst().strftime("%Y%m%d_%H%M%S")
    path = out / f"{CRYPTO_APPROVAL_AUDIT_JSON_PREFIX}{ts}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def execute_approved_crypto_order(
    broker: UpbitBroker,
    plan: CryptoOrderPlan,
    *,
    execute: bool,
    output_dir: str | Path = "outputs",
    runner_state: dict[str, Any] | None = None,
    sell_volume_fraction: float = 1.0,
) -> UpbitOrderResult:
    from pathlib import Path

    from deepsignal.crypto_trading.crypto_execution_engine import (
        CryptoExecutionEngine,
        execution_engine_enabled,
        mark_partial_taken,
        clear_position_execution,
    )

    do_exec = execute and not broker.config.dry_run and not broker.config.paper_mode
    if do_exec:
        from deepsignal.crypto_trading.crypto_paper_mode import require_live_trading_allowed

        require_live_trading_allowed(context="execute_approved_crypto_order")
    out_dir = Path(output_dir)

    if execution_engine_enabled():
        engine = CryptoExecutionEngine(broker, output_dir=out_dir)
        if plan.side.lower() == "sell":
            vol = float(plan.volume or 0)
            if vol <= 0:
                raise ValueError("SELL plan requires volume > 0")
            result = engine.execute_sell(
                plan,
                execute=do_exec,
                volume_fraction=float(sell_volume_fraction),
            )
            trigger = str(plan.sell_trigger or "").lower()
            if runner_state is not None:
                if trigger == "partial_take_profit":
                    mark_partial_taken(runner_state, plan.market)
                elif float(sell_volume_fraction) >= 0.99:
                    clear_position_execution(runner_state, plan.market)
            return result

        buy_out = engine.execute_buy(plan, execute=do_exec, runner_state=runner_state)
        if not buy_out.success:
            raise ValueError("; ".join(buy_out.reasons) or "execution_engine_buy_failed")
        if buy_out.order is None:
            raise ValueError("execution_engine_buy_no_order")
        return buy_out.order

    if plan.side.lower() == "sell":
        vol = float(plan.volume or 0)
        if vol <= 0:
            raise ValueError("SELL plan requires volume > 0")
        return broker.place_limit_sell(
            market=plan.market,
            volume=vol,
            price=plan.limit_price,
            execute=do_exec,
        )
    from deepsignal.crypto_trading.crypto_execution_quality import (
        evaluate_pre_trade,
        place_limit_buy_with_requote,
        should_block_entry_by_execution_quality,
    )

    eq = evaluate_pre_trade(
        broker,
        market=plan.market,
        side="buy",
        order_krw=float(plan.krw_amount),
        limit_price=float(plan.limit_price or 0) or None,
        take_profit_pct=float(plan.take_profit_pct or 0) or None,
        stop_loss_pct=float(plan.stop_loss_pct or 0) or None,
    )
    if should_block_entry_by_execution_quality(eq):
        raise ValueError("; ".join(eq.reasons) or "execution_quality_blocked")
    krw = float(eq.effective_order_krw)
    limit_px = float(eq.limit_price or plan.limit_price)
    result, _steps = place_limit_buy_with_requote(
        broker,
        market=plan.market,
        krw_amount=krw,
        price=limit_px,
        execute=do_exec,
    )
    return result


def format_execution_report(plan: CryptoOrderPlan, result: UpbitOrderResult, *,
                             skip_if_fill_follows: bool = False) -> str:
    """주문 접수 알림.
    skip_if_fill_follows=True 이면 체결 폴링이 뒤따를 경우 빈 문자열을 반환해
    첫 번째 메시지를 생략하고 체결 결과 메시지만 전송합니다.
    """
    has_fill_follow = bool(result.uuid)
    if skip_if_fill_follows and has_fill_follow:
        return ""   # 체결 완료 메시지로 대체

    blocked = result.status in ("UPBIT_DRY_RUN_BLOCKED", "CRYPTO_PAPER_MODE_BLOCKED")
    if blocked:
        return ""   # 페이퍼 모드는 알림 생략

    is_sell = plan.side.lower() == "sell"
    icon    = "🔴" if is_sell else "🟢"
    side_ko = "매도" if is_sell else "매수"
    coin    = plan.market.upper().split("-")[-1] if "-" in plan.market else plan.market
    lines = [
        f"{icon} {side_ko} 접수  ·  Upbit",
        f"*{plan.market}*",
    ]
    if result.price:
        lines.append(f"지정가 {result.price:,.0f}원  ·  {result.volume or ''} {coin}")
    if result.krw_amount:
        lines.append(f"주문액 {result.krw_amount:,.0f}원")
    return "\n".join(lines)


def follow_up_order_fill(
    cfg: CryptoTelegramConfig,
    broker: UpbitBroker,
    plan: CryptoOrderPlan,
    result: UpbitOrderResult,
) -> dict[str, Any]:
    """Poll order by uuid and send Telegram + audit. No new orders."""
    from deepsignal.crypto_trading.crypto_order_fill import (
        build_fill_audit,
        format_fill_message_for_outcome,
        poll_order_fill,
        write_order_status_audit,
    )

    wait_sec = float(cfg.wait_fill_seconds or 0)
    if wait_sec <= 0 or not result.uuid:
        return {"fill_follow_up": "skipped", "reason": "no uuid or wait_fill_seconds=0"}

    status, outcome = poll_order_fill(
        broker,
        str(result.uuid),
        wait_fill_seconds=wait_sec,
        fill_poll_interval=float(cfg.fill_poll_interval or 3.0),
    )
    audit_payload = build_fill_audit(plan, result, status, outcome, output_dir=cfg.output_dir)
    audit_path = write_order_status_audit(cfg.output_dir, audit_payload)
    report: dict[str, Any] = {
        "fill_outcome": outcome,
        "order_status": status,
        "audit_path": audit_path.as_posix(),
    }
    if cfg.bot_token and cfg.allowed_chat_id and status:
        msg = format_fill_message_for_outcome(plan, status, outcome)
        report["telegram"] = telegram_send_html(cfg, msg)
    from deepsignal.crypto_trading.crypto_recommendation_outcomes import apply_crypto_trade_pipeline

    report["outcome_tracking"] = apply_crypto_trade_pipeline(
        plan,
        result,
        outcomes_db=cfg.output_dir,
        fill_status=status,
        fill_outcome=outcome,
    )
    return report


def telegram_get_updates(cfg: CryptoTelegramConfig, *, offset: int | None = None) -> list[dict[str, Any]]:
    if not cfg.bot_token:
        return []
    params: dict[str, Any] = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    url = f"https://api.telegram.org/bot{cfg.bot_token}/getUpdates"
    resp = requests.get(url, params=params, timeout=cfg.timeout_seconds)
    data = resp.json()
    if not data.get("ok"):
        return []
    return list(data.get("result") or [])


def process_crypto_telegram_update(
    update: dict[str, Any],
    *,
    cfg: CryptoTelegramConfig,
    broker: UpbitBroker,
    execute_on_approve: bool = True,
    follow_fill: bool = True,
) -> dict[str, Any] | None:
    cb = update.get("callback_query") or {}
    data = str(cb.get("data") or "")
    if not data.startswith(ACTION_APPROVE) and not data.startswith(ACTION_REJECT):
        return None
    chat = (cb.get("message") or {}).get("chat") or {}
    chat_id = str(chat.get("id", ""))
    if cfg.allowed_chat_id and chat_id != str(cfg.allowed_chat_id):
        return {"ignored": True, "reason": "chat_id mismatch"}

    req = load_crypto_approval_request(cfg.output_dir)
    if req is None:
        return {"error": "no pending crypto approval"}

    action, _, token = data.partition(":")
    if token != req.token:
        return {"error": "token mismatch"}

    expires = parse_datetime_with_default_tz(req.expires_at)
    if now_kst() > expires:
        req.status = STATUS_EXPIRED
        _save_request(cfg.output_dir, req)
        return {"status": STATUS_EXPIRED}

    plan_path = Path(req.plan_path)
    if not plan_path.is_file():
        plan_path = Path(cfg.output_dir) / CRYPTO_PLAN_JSON
    if _plan_hash(plan_path) != req.plan_hash:
        return {"error": "plan hash mismatch"}

    plan = load_crypto_plan(plan_path)

    if action == ACTION_REJECT:
        req.status = STATUS_REJECTED
        _save_request(cfg.output_dir, req)
        audit = {"status": STATUS_REJECTED, "plan": plan.to_dict()}
        _write_audit(cfg.output_dir, audit)
        if cfg.bot_token and cfg.allowed_chat_id:
            telegram_send_message(cfg, f"[DeepSignal] {plan.display_name} 매수 거부됨.", token=req.token)
        return audit

    req.status = STATUS_APPROVED
    _save_request(cfg.output_dir, req)
    do_execute = execute_on_approve and not broker.config.dry_run and not broker.config.paper_mode
    from deepsignal.crypto_trading.crypto_auto_runner import load_runner_state, save_runner_state

    runner_state = load_runner_state(cfg.output_dir)
    frac = 1.0
    bd = plan.score_breakdown if isinstance(plan.score_breakdown, dict) else {}
    if bd.get("sell_volume_fraction") is not None:
        try:
            frac = float(bd["sell_volume_fraction"])
        except (TypeError, ValueError):
            frac = 1.0
    result = execute_approved_crypto_order(
        broker,
        plan,
        execute=do_execute,
        output_dir=cfg.output_dir,
        runner_state=runner_state,
        sell_volume_fraction=frac,
    )
    save_runner_state(cfg.output_dir, runner_state)
    report = format_execution_report(plan, result)
    from deepsignal.crypto_trading.crypto_recommendation_outcomes import apply_crypto_trade_pipeline

    audit = {
        "status": STATUS_APPROVED,
        "executed": do_execute,
        "plan": plan.to_dict(),
        "result": asdict(result),
    }
    audit["outcome_tracking"] = apply_crypto_trade_pipeline(
        plan,
        result,
        outcomes_db=cfg.output_dir,
    )
    _write_audit(cfg.output_dir, audit)
    if cfg.bot_token and cfg.allowed_chat_id:
        telegram_send_message(cfg, report, token=req.token)
    if follow_fill and cfg.wait_fill_seconds > 0 and result.uuid:
        ff = follow_up_order_fill(cfg, broker, plan, result)
        audit["fill_follow_up"] = ff
        if isinstance(ff.get("outcome_tracking"), dict):
            audit["outcome_tracking"] = ff["outcome_tracking"]
    return audit


def poll_crypto_telegram_until_done(
    cfg: CryptoTelegramConfig,
    broker: UpbitBroker,
    *,
    max_wait_seconds: float = 600.0,
    runner_cfg: Any | None = None,
) -> dict[str, Any]:
    from deepsignal.crypto_trading.crypto_telegram_menu import poll_telegram_updates_once

    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        summary = poll_telegram_updates_once(
            cfg,
            broker,
            runner_cfg=runner_cfg,
            process_approvals=True,
        )
        for out in summary.get("callbacks") or []:
            if out.get("status") in (STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED):
                return out
        req = load_crypto_approval_request(cfg.output_dir)
        if req and req.status in (STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED):
            return req.to_dict()
        time.sleep(max(cfg.poll_interval, 1.0))
    return {"status": "POLL_TIMEOUT"}
