"""Telegram menu bot: KIS + crypto holdings and on-demand recommendations."""

from __future__ import annotations

import json
from datetime import datetime
from dataclasses import replace
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_holdings import holding_to_dict
from deepsignal.crypto_trading.crypto_order_plan import CRYPTO_PLAN_JSON, build_plan_from_recommendation, save_crypto_plan
from deepsignal.crypto_trading.crypto_quality import CryptoBuyQualityConfig
from deepsignal.crypto_trading.crypto_recommendation import build_daily_crypto_recommendation
from deepsignal.crypto_trading.crypto_recommendation_diagnostics import (
    build_crypto_recommendation_diagnostics,
    save_crypto_no_recommendation_artifacts,
)
from deepsignal.crypto_trading.crypto_telegram_flow import CryptoTelegramConfig, telegram_get_updates
from deepsignal.crypto_trading.crypto_telegram_offset import (
    acknowledge_update,
    advance_offset_from_updates,
    load_telegram_offset,
)
from deepsignal.live_trading.telegram_progress_notify import (
    prepare_menu_scan_lock,
    record_progress_notify,
    release_menu_scan_lock,
    should_send_progress_notify,
)

SCAN_LOCK_KIS = "kis_recommend"
SCAN_LOCK_CRYPTO = "crypto_recommend"
from deepsignal.crypto_trading.upbit_broker import UpbitBroker

MENU_TEXT_HOLDINGS = "현재 내 자산 보기"
MENU_TEXT_RECOMMEND = "현재 추천 보기"
MENU_TEXT_RECOMMEND_KIS = "현재 추천 보기 — 국내(KIS)"
MENU_TEXT_RECOMMEND_CRYPTO = "현재 추천 보기 — 코인"
MENU_TEXT_MAIN = "◀ 메인 메뉴"
MENU_TEXT_RUNNER_STOP = "러너 정지"
MENU_TEXT_RUNNER_START = "러너 시작"
MENU_TEXT_RUNNER_STATUS = "러너 상태"
RUNNER_STATE_FILE = "CRYPTO_AUTO_RUNNER_STATE.json"

MENU_PROMPT = (
    "[DeepSignal 메뉴]\n"
    "아래 버튼을 선택하세요.\n"
    "• 현재 내 자산 보기 — 국내주식(KIS) + 코인(Upbit)\n"
    "• 현재 추천 보기 — 국내(KIS) / 코인 선택\n"
    "• 러너 정지/시작 — 자동 분석 루프 제어"
)


def normalize_menu_text(text: str) -> str:
    return " ".join(str(text or "").split())


def log_menu_event(event: str, **fields: Any) -> None:
    from deepsignal.live_trading.telegram_user_format import menu_verbose_logging

    is_error = "fail" in event.lower() or "error" in event.lower() or bool(fields.get("error"))
    if not menu_verbose_logging() and not is_error:
        return
    payload = {"event": event, **fields}
    print(f"[menu] {event} {json.dumps(payload, ensure_ascii=False)}", flush=True)


def should_send_menu_scan_progress(output_dir: str, key: str) -> bool:
    from deepsignal.live_trading.telegram_progress_notify import (
        progress_notify_enabled,
        should_send_progress_notify,
    )
    from deepsignal.live_trading.telegram_user_format import menu_scan_progress_enabled

    if not menu_scan_progress_enabled() or not progress_notify_enabled():
        return False
    return should_send_progress_notify(output_dir, key)


def main_menu_reply_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            [{"text": MENU_TEXT_HOLDINGS}],
            [{"text": MENU_TEXT_RECOMMEND_KIS}],
            [{"text": MENU_TEXT_RECOMMEND_CRYPTO}],
            [{"text": MENU_TEXT_RUNNER_STOP}, {"text": MENU_TEXT_RUNNER_START}],
            [{"text": MENU_TEXT_RUNNER_STATUS}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def recommend_choice_reply_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            [{"text": MENU_TEXT_RECOMMEND_KIS}],
            [{"text": MENU_TEXT_RECOMMEND_CRYPTO}],
            [{"text": MENU_TEXT_RUNNER_STOP}, {"text": MENU_TEXT_RUNNER_START}],
            [{"text": MENU_TEXT_MAIN}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def _runner_state_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / RUNNER_STATE_FILE


def _load_runner_state_for_control(output_dir: str | Path) -> dict[str, Any]:
    p = _runner_state_path(output_dir)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_runner_state_for_control(output_dir: str | Path, state: dict[str, Any]) -> None:
    p = _runner_state_path(output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _send_menu_text(cfg: CryptoTelegramConfig, text: str, *, keyboard: dict[str, Any] | None = None) -> dict[str, Any]:
    import requests

    if not cfg.bot_token or not cfg.allowed_chat_id:
        return {"ok": False, "error": "telegram not configured"}
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    markup = keyboard if keyboard is not None else main_menu_reply_keyboard()
    payload: dict[str, Any] = {
        "chat_id": cfg.allowed_chat_id,
        "text": text[:4000],
        "reply_markup": json.dumps(markup, ensure_ascii=False),
    }
    resp = requests.post(url, data=payload, timeout=cfg.timeout_seconds)
    try:
        return resp.json()
    except Exception:
        return {"ok": False, "status_code": resp.status_code, "text": resp.text[:300]}


def telegram_send_menu_message(cfg: CryptoTelegramConfig, text: str | None = None) -> dict[str, Any]:
    return _send_menu_text(cfg, text or MENU_PROMPT, keyboard=main_menu_reply_keyboard())


def format_kis_holdings_telegram(db_path: str) -> list[str]:
    from deepsignal.storage.database import load_latest_real_account_snapshot, load_latest_real_positions

    lines: list[str] = ["=== 국내주식 (KIS) ==="]
    snap = load_latest_real_account_snapshot(db_path, broker="kis")
    positions = load_latest_real_positions(db_path, broker="kis")
    if snap:
        st = snap.get("snapshot_time") or "n/a"
        cash = snap.get("cash")
        equity = snap.get("total_equity")
        lines.append(f"스냅샷: {st}")
        if cash is not None:
            lines.append(f"현금: {float(cash):,.0f}원")
        if equity is not None:
            lines.append(f"총자산(추정): {float(equity):,.0f}원")
    else:
        lines.append("DB에 KIS 계좌 스냅샷 없음 — live-sync-account --network 실행 권장")

    if not positions:
        lines.append("보유 종목 없음 (또는 미동기화)")
    else:
        lines.append("")
        for p in positions:
            sym = str(p.get("symbol") or "")
            qty = float(p.get("quantity") or 0)
            avg = float(p.get("avg_price") or 0)
            cur = float(p.get("current_price") or 0)
            mv = float(p.get("market_value") or 0)
            if mv <= 0 and qty > 0 and cur > 0:
                mv = qty * cur
            pnl = ((cur - avg) / avg * 100.0) if avg > 0 else 0.0
            lines.append(
                f"  {sym}: {qty:,.0f}주, 평단 {avg:,.0f}, 현재 {cur:,.0f}, "
                f"평가 {mv:,.0f}원, 수익률 {pnl:+.2f}%"
            )
    return lines


def kis_holdings_totals(db_path: str) -> dict[str, float]:
    """Cost basis and market value for KIS stock positions (excludes cash)."""
    from deepsignal.storage.database import load_latest_real_account_snapshot, load_latest_real_positions

    positions = load_latest_real_positions(db_path, broker="kis")
    cost = 0.0
    value = 0.0
    for p in positions:
        qty = float(p.get("quantity") or 0)
        avg = float(p.get("avg_price") or 0)
        cur = float(p.get("current_price") or 0)
        mv = float(p.get("market_value") or 0)
        if mv <= 0 and qty > 0 and cur > 0:
            mv = qty * cur
        cost += qty * avg
        value += mv
    snap = load_latest_real_account_snapshot(db_path, broker="kis") or {}
    cash = float(snap.get("cash") or 0)
    return {"cost_krw": cost, "value_krw": value, "cash_krw": cash}


def crypto_holdings_totals(holdings: list[Any]) -> dict[str, float]:
    """Cost basis and market value for Upbit crypto positions (excludes KRW cash)."""
    cost = 0.0
    value = 0.0
    for h in holdings:
        value += float(h.valuation_krw or 0)
        cost += float(h.valuation_krw or 0) - float(h.pnl_krw or 0)
    return {"cost_krw": cost, "value_krw": value}


def format_combined_holdings_summary(
    *,
    kis: dict[str, float],
    crypto: dict[str, float],
    upbit_krw: float,
) -> list[str]:
    cost = float(kis.get("cost_krw") or 0) + float(crypto.get("cost_krw") or 0)
    value = float(kis.get("value_krw") or 0) + float(crypto.get("value_krw") or 0)
    pnl_krw = value - cost
    pnl_pct = (pnl_krw / cost * 100.0) if cost > 0 else 0.0
    kis_cash = float(kis.get("cash_krw") or 0)
    cash_total = kis_cash + float(upbit_krw or 0)
    total_assets = value + cash_total
    return [
        "=== 전체 요약 (국내주식 + 코인) ===",
        f"투자금액: {cost:,.0f}원",
        f"현재 평가: {value:,.0f}원",
        f"손익: {pnl_krw:+,.0f}원 ({pnl_pct:+.2f}%)",
        f"현금(KIS {kis_cash:,.0f} + Upbit {float(upbit_krw):,.0f}): {cash_total:,.0f}원",
        f"총자산(평가+현금): {total_assets:,.0f}원",
    ]


def format_holdings_telegram(broker: UpbitBroker, *, db_path: str | None = None) -> str:
    from deepsignal.live_trading.telegram_user_format import format_holdings_telegram_brief

    holdings = broker.get_crypto_holdings()
    krw = broker.get_krw_available()
    if db_path:
        kis_totals = kis_holdings_totals(db_path)
        crypto_totals = crypto_holdings_totals(holdings)
        summary = format_combined_holdings_summary(kis=kis_totals, crypto=crypto_totals, upbit_krw=krw)
        kis_lines = format_kis_holdings_telegram(db_path)
    else:
        summary = ["(국내주식 DB 미연결)"]
        kis_lines = ["=== 국내주식 (KIS) ===", "DB 경로 없음"]

    crypto_lines = ["=== 코인 (Upbit) ==="]
    if not holdings:
        crypto_lines.append("보유 없음")
    else:
        for h in holdings:
            d = holding_to_dict(h)
            crypto_lines.append(
                f"· {d.get('market')}: {d.get('pnl_pct'):+.1f}% · "
                f"{float(d.get('valuation_krw', 0)):,.0f}원"
            )

    return format_holdings_telegram_brief(
        summary_lines=summary,
        kis_lines=kis_lines,
        crypto_lines=crypto_lines,
        upbit_krw=krw,
    )


def handle_menu_kis_recommend(
    cfg: CryptoTelegramConfig,
    *,
    db_path: str,
    output_dir: str,
    network: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Run KIS daily plan; send Telegram 승인/거부 when order_count > 0."""
    from deepsignal.live_trading.telegram_menu_cache import format_kis_recommendation_from_cache

    if not force_refresh:
        cached = format_kis_recommendation_from_cache(output_dir, max_age_minutes=120.0)
        if cached:
            return {
                "body": str(cached["body"]),
                "approval_sent": False,
                "has_orders": int(cached.get("order_count") or 0) > 0,
                "from_cache": True,
            }

    from deepsignal.live_trading.daily_ai_trading_workflow import run_daily_ai_trade_plan
    from deepsignal.live_trading.telegram_approval import (
        APPROVAL_STATUS_PENDING,
        TelegramApprovalConfig,
        create_telegram_approval_request,
    )

    approval_sent = False
    lock_state = prepare_menu_scan_lock(output_dir, SCAN_LOCK_KIS)
    if lock_state == "in_progress":
        cached = format_kis_recommendation_from_cache(output_dir, max_age_minutes=120.0)
        if cached:
            return {
                "body": str(cached["body"]),
                "approval_sent": False,
                "has_orders": int(cached.get("order_count") or 0) > 0,
                "from_cache": True,
            }
        return {
            "body": (
                "⏳ 국내주식 분석이 진행 중입니다.\n"
                "1~2분 후 「현재 추천 보기 — 국내(KIS)」를 다시 눌러 주세요."
            ),
            "approval_sent": False,
            "has_orders": False,
            "from_cache": False,
            "scan_in_progress": True,
        }
    try:
        if (
            not force_refresh
            and cfg.bot_token
            and cfg.allowed_chat_id
            and should_send_menu_scan_progress(output_dir, SCAN_LOCK_KIS)
        ):
            _send_menu_text(
                cfg,
                "⏳ 국내주식 분석 중…",
                keyboard=main_menu_reply_keyboard(),
            )
            record_progress_notify(output_dir, SCAN_LOCK_KIS)
        result = run_daily_ai_trade_plan(
            db_path,
            broker="kis",
            network=network,
            output_dir=output_dir,
        )
        from deepsignal.live_trading.telegram_user_format import format_kis_recommendation_telegram

        lines = format_kis_recommendation_telegram(
            status=str(result.status),
            recommendation_count=int(result.recommendation_count),
            order_count=int(result.order_count),
            total_order_value=float(result.total_order_value),
        )
        if int(result.order_count) > 0 and result.latest_order_plan_json:
            plan_path = Path(result.latest_order_plan_json)
            if plan_path.is_file() and cfg.bot_token and cfg.allowed_chat_id:
                tg_kis = TelegramApprovalConfig(
                    output_dir=output_dir,
                    bot_token=cfg.bot_token,
                    allowed_chat_id=cfg.allowed_chat_id,
                    send=True,
                )
                req, _, _ = create_telegram_approval_request(plan_path, tg_kis)
                if req.status == APPROVAL_STATUS_PENDING:
                    approval_sent = True
                    lines = format_kis_recommendation_telegram(
                        status=str(result.status),
                        recommendation_count=int(result.recommendation_count),
                        order_count=int(result.order_count),
                        total_order_value=float(result.total_order_value),
                        approval_sent=True,
                    )
                else:
                    lines.append("")
                    lines.append("주문안은 있으나 승인 요청이 차단됐습니다.")
            else:
                lines.append("")
                lines.append("Telegram 미설정 — 승인 버튼을 보낼 수 없습니다.")
        else:
            pass
        return {
            "body": "\n".join(lines)[:4000],
            "approval_sent": approval_sent,
            "has_orders": int(result.order_count) > 0,
            "from_cache": False,
        }
    except Exception as exc:
        lines.append(f"분석 실패: {exc}")
        return {"body": "\n".join(lines)[:4000], "approval_sent": False, "has_orders": False}
    finally:
        release_menu_scan_lock(output_dir, SCAN_LOCK_KIS)


def run_kis_recommendation_analysis_telegram(
    *,
    db_path: str,
    output_dir: str,
    network: bool = False,
) -> str:
    cfg = CryptoTelegramConfig(output_dir=output_dir)
    return str(handle_menu_kis_recommend(cfg, db_path=db_path, output_dir=output_dir, network=network)["body"])


def resend_crypto_approval_from_saved_plan(cfg: CryptoTelegramConfig) -> bool:
    """Re-send 승인/거부 for latest CRYPTO_ORDER_PLAN.json (Telegram menu only)."""
    from deepsignal.crypto_trading.crypto_order_plan import CRYPTO_PLAN_JSON, load_crypto_plan
    from deepsignal.crypto_trading.crypto_telegram_flow import (
        STATUS_PENDING,
        _plan_hash,
        create_crypto_approval_request,
        load_crypto_approval_request,
        telegram_send_message,
    )

    if not cfg.bot_token or not cfg.allowed_chat_id:
        return False
    plan_path = Path(cfg.output_dir) / CRYPTO_PLAN_JSON
    if not plan_path.is_file():
        return False
    try:
        plan = load_crypto_plan(plan_path)
    except Exception:
        return False
    if not str(plan.market or "").strip() or not str(plan.side or "").strip():
        return False
    plan_hash = _plan_hash(plan_path)
    existing = load_crypto_approval_request(cfg.output_dir)
    if existing and str(existing.status) == STATUS_PENDING and str(existing.plan_hash) == plan_hash:
        telegram_send_message(cfg, existing.message_text, token=existing.token)
        return True
    approval_cfg = replace(cfg, send=True)
    create_crypto_approval_request(plan, cfg=approval_cfg, plan_path=plan_path)
    return True


def handle_menu_crypto_recommend(
    broker: UpbitBroker,
    cfg: CryptoTelegramConfig,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    take_profit_buffer_pct: float,
    stop_loss_buffer_pct: float,
    min_volume_ratio: float,
    max_order_value: float,
    force_refresh: bool = False,
    from_telegram_menu: bool = True,
) -> dict[str, Any]:
    """Run crypto recommendation; Telegram menu always sends 승인/거부 when a plan exists."""
    from deepsignal.live_trading.telegram_menu_cache import format_crypto_recommendation_from_cache

    if not force_refresh:
        cached = format_crypto_recommendation_from_cache(cfg.output_dir, max_age_minutes=15.0)
        if cached:
            return {
                "body": str(cached["body"]),
                "approval_sent": False,
                "has_recommendation": True,
                "from_cache": True,
            }

    from dataclasses import dataclass

    from deepsignal.config.settings import load_settings
    from deepsignal.crypto_trading.crypto_outcome_threshold_tuning import apply_active_thresholds_to_runner
    from deepsignal.crypto_trading.crypto_recommendation_outcomes import record_crypto_recommendation
    from deepsignal.crypto_trading.crypto_telegram_flow import create_crypto_approval_request
    from deepsignal.crypto_trading.crypto_universe import CryptoUniverseConfig
    from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS
    from deepsignal.storage.database import init_database

    _crypto_thr = DEFAULT_ANALYSIS_CONDITIONS.crypto

    lock_state = prepare_menu_scan_lock(cfg.output_dir, SCAN_LOCK_CRYPTO)
    if lock_state == "in_progress":
        cached = format_crypto_recommendation_from_cache(cfg.output_dir, max_age_minutes=15.0)
        if cached:
            return {
                "body": str(cached["body"]),
                "approval_sent": False,
                "has_recommendation": True,
                "from_cache": True,
            }
        return {
            "body": (
                "⏳ 코인 분석이 진행 중입니다.\n"
                "1~2분 후 「현재 추천 보기 — 코인」을 다시 눌러 주세요."
            ),
            "approval_sent": False,
            "has_recommendation": False,
            "from_cache": False,
            "scan_in_progress": True,
        }

    @dataclass
    class _Thr:
        take_profit_pct: float
        stop_loss_pct: float
        take_profit_buffer_pct: float
        stop_loss_buffer_pct: float
        min_volume_ratio: float

    thr = _Thr(
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_buffer_pct=take_profit_buffer_pct,
        stop_loss_buffer_pct=stop_loss_buffer_pct,
        min_volume_ratio=min_volume_ratio,
    )
    try:
        if (
            not force_refresh
            and cfg.bot_token
            and cfg.allowed_chat_id
            and should_send_menu_scan_progress(cfg.output_dir, SCAN_LOCK_CRYPTO)
        ):
            _send_menu_text(
                cfg,
                "⏳ 코인 분석 중…",
                keyboard=main_menu_reply_keyboard(),
            )
            record_progress_notify(cfg.output_dir, SCAN_LOCK_CRYPTO)
        apply_active_thresholds_to_runner(thr, cfg.output_dir)
        macro_db = str(init_database(load_settings().db_path))
        from deepsignal.crypto_trading.crypto_signal_scorer import load_crypto_macro_context
        from deepsignal.crypto_trading.crypto_position_sizing import (
            apply_runtime_sizing_to_runner,
            resolve_crypto_runtime_sizing,
        )

        macro_ctx = load_crypto_macro_context(macro_db)
        sizing = resolve_crypto_runtime_sizing(
            broker,
            output_dir=cfg.output_dir,
            macro_regime=str(macro_ctx.get("market_regime") or "neutral"),
        )
        apply_runtime_sizing_to_runner(thr, sizing)
        thr.take_profit_pct = sizing.take_profit_pct
        thr.stop_loss_pct = sizing.stop_loss_pct
        thr.take_profit_buffer_pct = sizing.take_profit_buffer_pct
        thr.stop_loss_buffer_pct = sizing.stop_loss_buffer_pct
        thr.min_volume_ratio = sizing.min_volume_ratio
        buy_quality = CryptoBuyQualityConfig(min_volume_ratio=thr.min_volume_ratio)
        max_order_value = float(sizing.max_order_krw)
        universe_cfg = CryptoUniverseConfig(
            universe=str(_crypto_thr.market_universe),
            max_buy_scan_markets=int(_crypto_thr.max_buy_scan_markets),
        )
        rec = build_daily_crypto_recommendation(
            broker,
            take_profit_pct=thr.take_profit_pct,
            stop_loss_pct=thr.stop_loss_pct,
            take_profit_buffer_pct=thr.take_profit_buffer_pct,
            stop_loss_buffer_pct=thr.stop_loss_buffer_pct,
            max_order_value=max_order_value,
            buy_quality=buy_quality,
            universe_config=universe_cfg,
            macro_db_path=macro_db,
            output_dir=cfg.output_dir,
        )
        from deepsignal.live_trading.telegram_user_format import (
            format_crypto_no_recommendation_telegram,
            format_crypto_recommendation_telegram,
        )

        lines: list[str] = []
        approval_sent = False
        if rec is not None:
            plan = build_plan_from_recommendation(rec)
            jpath, mpath = save_crypto_plan(cfg.output_dir, plan)
            record_crypto_recommendation(plan, outcomes_db=cfg.output_dir, rec=rec)
            if cfg.bot_token and cfg.allowed_chat_id:
                approval_cfg = replace(cfg, send=True)
                create_crypto_approval_request(plan, cfg=approval_cfg, plan_path=jpath)
                approval_sent = True
            lines = format_crypto_recommendation_telegram(
                display_name=str(rec.display_name),
                market=str(rec.market),
                side=str(rec.side),
                reason=str(rec.reason),
                pnl_pct=float(rec.pnl_pct),
                current_price=float(rec.current_price),
                sell_trigger=str(rec.sell_trigger) if rec.sell_trigger else None,
                approval_sent=approval_sent,
                max_order_krw=float(sizing.max_order_krw),
                max_orders_per_day=int(sizing.max_orders_per_day),
            )
            if not approval_sent:
                lines.append("")
                lines.append("Telegram 미설정 — 승인 버튼을 보낼 수 없습니다.")
        else:
            diag = build_crypto_recommendation_diagnostics(
                broker,
                take_profit_pct=thr.take_profit_pct,
                stop_loss_pct=thr.stop_loss_pct,
                take_profit_buffer_pct=thr.take_profit_buffer_pct,
                stop_loss_buffer_pct=thr.stop_loss_buffer_pct,
                max_order_value=max_order_value,
                buy_quality=buy_quality,
                universe_config=universe_cfg,
                macro_db_path=macro_db,
                output_dir=cfg.output_dir,
            )
            save_crypto_no_recommendation_artifacts(cfg.output_dir, diag)
            lines = format_crypto_no_recommendation_telegram(diag)
            if from_telegram_menu and resend_crypto_approval_from_saved_plan(cfg):
                approval_sent = True
                lines.extend(["", "저장된 주문안 — 승인/거부 버튼을 다시 보냈습니다."])
        return {
            "body": "\n".join(lines)[:4000],
            "approval_sent": approval_sent,
            "has_recommendation": rec is not None,
            "from_cache": False,
        }
    except Exception as exc:
        log_menu_event("crypto recommend failed", error=str(exc))
        return {
            "body": f"[DeepSignal 코인 — 추천 분석]\n\n분석 중 오류가 발생했습니다.\n{exc!s}"[:4000],
            "approval_sent": False,
            "has_recommendation": False,
            "from_cache": False,
        }
    finally:
        release_menu_scan_lock(cfg.output_dir, SCAN_LOCK_CRYPTO)


def run_recommendation_analysis_telegram(
    broker: UpbitBroker,
    cfg: CryptoTelegramConfig,
    *,
    take_profit_pct: float,
    stop_loss_pct: float,
    take_profit_buffer_pct: float,
    stop_loss_buffer_pct: float,
    min_volume_ratio: float,
    max_order_value: float,
) -> str:
    out = handle_menu_crypto_recommend(
        broker,
        cfg,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_buffer_pct=take_profit_buffer_pct,
        stop_loss_buffer_pct=stop_loss_buffer_pct,
        min_volume_ratio=min_volume_ratio,
        max_order_value=max_order_value,
    )
    return str(out["body"])


def _resolve_db_path(db_path: str | None) -> str:
    if db_path:
        return db_path
    from deepsignal.config.settings import load_settings
    from deepsignal.storage.database import init_database

    return str(init_database(load_settings().db_path))


def process_crypto_telegram_menu_message(
    update: dict[str, Any],
    *,
    cfg: CryptoTelegramConfig,
    broker: UpbitBroker,
    runner_cfg: Any | None = None,
    db_path: str | None = None,
    network: bool = False,
) -> dict[str, Any] | None:
    """Handle text messages (menu). Returns None if not a menu message."""
    if update.get("callback_query"):
        return None
    msg = update.get("message") or {}
    text = normalize_menu_text(str(msg.get("text") or ""))
    if not text:
        log_menu_event("menu command ignored", reason="empty_text", update_id=update.get("update_id"))
        return None
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    if cfg.allowed_chat_id and chat_id != str(cfg.allowed_chat_id):
        log_menu_event(
            "menu command ignored",
            reason="chat_id_mismatch",
            update_id=update.get("update_id"),
            chat_id=chat_id,
        )
        return {"ignored": True, "reason": "chat_id mismatch"}

    log_menu_event("menu update received", update_id=update.get("update_id"), text=text)

    from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

    _crypto_thr = DEFAULT_ANALYSIS_CONDITIONS.crypto
    rc = runner_cfg
    tp = float(getattr(rc, "take_profit_pct", _crypto_thr.take_profit_pct) or _crypto_thr.take_profit_pct) if rc else float(
        _crypto_thr.take_profit_pct
    )
    sl = float(getattr(rc, "stop_loss_pct", _crypto_thr.stop_loss_pct) or _crypto_thr.stop_loss_pct) if rc else float(
        _crypto_thr.stop_loss_pct
    )
    tp_buf = float(
        getattr(rc, "take_profit_buffer_pct", _crypto_thr.take_profit_buffer_pct) or _crypto_thr.take_profit_buffer_pct
    ) if rc else float(_crypto_thr.take_profit_buffer_pct)
    sl_buf = float(
        getattr(rc, "stop_loss_buffer_pct", _crypto_thr.stop_loss_buffer_pct) or _crypto_thr.stop_loss_buffer_pct
    ) if rc else float(_crypto_thr.stop_loss_buffer_pct)
    mvr = float(getattr(rc, "min_volume_ratio", _crypto_thr.min_volume_ratio) or _crypto_thr.min_volume_ratio) if rc else float(
        _crypto_thr.min_volume_ratio
    )
    max_val = float(getattr(rc, "max_order_value", 0.0) if hasattr(rc, "max_order_value") else 0.0) if rc else 0.0
    resolved_db = _resolve_db_path(db_path)

    if text == MENU_TEXT_HOLDINGS:
        log_menu_event("menu command matched", command="holdings", update_id=update.get("update_id"))
        body = format_holdings_telegram(broker, db_path=resolved_db)
        result = _send_menu_text(cfg, body, keyboard=main_menu_reply_keyboard())
        log_menu_event(
            "menu response sent",
            command="holdings",
            ok=bool(result.get("ok")),
            update_id=update.get("update_id"),
        )
        return {"action": "holdings", "telegram": result}

    if text == MENU_TEXT_RECOMMEND:
        log_menu_event("menu command matched", command="recommend_choice", update_id=update.get("update_id"))
        body = (
            "[DeepSignal — 추천 분석 선택]\n\n"
            "아래에서 분석할 시장을 선택하세요.\n"
            f"• {MENU_TEXT_RECOMMEND_KIS}\n"
            f"• {MENU_TEXT_RECOMMEND_CRYPTO}"
        )
        result = _send_menu_text(cfg, body, keyboard=recommend_choice_reply_keyboard())
        log_menu_event("menu response sent", command="recommend_choice", ok=bool(result.get("ok")))
        return {"action": "recommend_choice", "telegram": result}

    if text == MENU_TEXT_RECOMMEND_KIS:
        log_menu_event("menu command matched", command="recommend_kis", update_id=update.get("update_id"))
        kis_out = handle_menu_kis_recommend(
            cfg,
            db_path=resolved_db,
            output_dir=cfg.output_dir,
            network=network,
        )
        result = _send_menu_text(cfg, str(kis_out["body"]), keyboard=main_menu_reply_keyboard())
        log_menu_event(
            "menu response sent",
            command="recommend_kis",
            ok=bool(result.get("ok")),
            approval_sent=bool(kis_out.get("approval_sent")),
        )
        return {
            "action": "recommend_kis",
            "telegram": result,
            "approval_sent": kis_out.get("approval_sent"),
            "has_orders": kis_out.get("has_orders"),
        }

    if text == MENU_TEXT_RECOMMEND_CRYPTO:
        log_menu_event("menu command matched", command="recommend_crypto", update_id=update.get("update_id"))
        try:
            crypto_out = handle_menu_crypto_recommend(
                broker,
                cfg,
                take_profit_pct=tp,
                stop_loss_pct=sl,
                take_profit_buffer_pct=tp_buf,
                stop_loss_buffer_pct=sl_buf,
                min_volume_ratio=mvr,
                max_order_value=max_val,
                force_refresh=True,
            )
        except Exception as exc:
            log_menu_event("recommend_crypto handler error", error=str(exc))
            crypto_out = {
                "body": f"[DeepSignal 코인]\n처리 실패: {exc!s}",
                "approval_sent": False,
                "has_recommendation": False,
            }
        result = _send_menu_text(cfg, str(crypto_out["body"]), keyboard=main_menu_reply_keyboard())
        log_menu_event(
            "menu response sent",
            command="recommend_crypto",
            ok=bool(result.get("ok")),
            approval_sent=bool(crypto_out.get("approval_sent")),
        )
        return {
            "action": "recommend_crypto",
            "telegram": result,
            "approval_sent": crypto_out.get("approval_sent"),
            "has_recommendation": crypto_out.get("has_recommendation"),
        }

    if text == MENU_TEXT_MAIN:
        log_menu_event("menu command matched", command="main_menu", update_id=update.get("update_id"))
        result = telegram_send_menu_message(cfg)
        log_menu_event("menu response sent", command="main_menu", ok=bool(result.get("ok")))
        return {"action": "main_menu", "telegram": result}

    if text == MENU_TEXT_RUNNER_STOP:
        log_menu_event("menu command matched", command="runner_stop", update_id=update.get("update_id"))
        st = _load_runner_state_for_control(cfg.output_dir)
        st["runner_paused"] = True
        st["runner_pause_reason"] = "telegram_menu_stop"
        st["runner_paused_at"] = datetime.now().isoformat()
        _save_runner_state_for_control(cfg.output_dir, st)
        body = "[DeepSignal 코인 러너]\n자동 분석/주문 루프를 정지(PAUSE)로 전환했습니다."
        result = _send_menu_text(cfg, body, keyboard=main_menu_reply_keyboard())
        return {"action": "runner_stop", "telegram": result}

    if text == MENU_TEXT_RUNNER_START:
        log_menu_event("menu command matched", command="runner_start", update_id=update.get("update_id"))
        st = _load_runner_state_for_control(cfg.output_dir)
        st["runner_paused"] = False
        st["runner_pause_reason"] = ""
        st["runner_resumed_at"] = datetime.now().isoformat()
        _save_runner_state_for_control(cfg.output_dir, st)
        body = "[DeepSignal 코인 러너]\n자동 분석/주문 루프를 시작(RESUME)으로 전환했습니다."
        result = _send_menu_text(cfg, body, keyboard=main_menu_reply_keyboard())
        return {"action": "runner_start", "telegram": result}

    if text == MENU_TEXT_RUNNER_STATUS:
        log_menu_event("menu command matched", command="runner_status", update_id=update.get("update_id"))
        st = _load_runner_state_for_control(cfg.output_dir)
        paused = bool(st.get("runner_paused", False))
        body = (
            "[DeepSignal 코인 러너 상태]\n"
            f"상태: {'PAUSED(정지)' if paused else 'RUNNING(동작)'}\n"
            f"사유: {st.get('runner_pause_reason') or '-'}"
        )
        result = _send_menu_text(cfg, body, keyboard=main_menu_reply_keyboard())
        return {"action": "runner_status", "telegram": result}

    log_menu_event("menu command ignored", reason="unknown_text", text=text, update_id=update.get("update_id"))
    result = telegram_send_menu_message(cfg)
    return {"action": "menu", "telegram": result}


def poll_telegram_updates_once(
    cfg: CryptoTelegramConfig,
    broker: UpbitBroker,
    *,
    runner_cfg: Any | None = None,
    db_path: str | None = None,
    network: bool = False,
    process_approvals: bool = True,
) -> dict[str, Any]:
    """Fetch updates once; process menu text and approval callbacks; advance unified offset."""
    from deepsignal.crypto_trading.crypto_telegram_flow import (
        STATUS_APPROVED,
        STATUS_EXPIRED,
        STATUS_REJECTED,
        load_crypto_approval_request,
        process_crypto_telegram_update,
    )

    offset = load_telegram_offset(cfg.output_dir)
    updates = telegram_get_updates(cfg, offset=offset)
    summary: dict[str, Any] = {
        "updates": len(updates),
        "menu": [],
        "callbacks": [],
        "offset_before": offset,
    }
    if not updates:
        return summary

    for upd in updates:
        uid = upd.get("update_id")
        if uid is not None:
            acknowledge_update(cfg.output_dir, int(uid))
        if upd.get("callback_query"):
            log_menu_event("menu update received", update_id=uid, kind="callback")
            if not process_approvals:
                log_menu_event("menu command ignored", reason="approvals_disabled", update_id=uid)
                continue
            out = process_crypto_telegram_update(upd, cfg=cfg, broker=broker)
            if out:
                summary["callbacks"].append(out)
                if out.get("status") in (STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED):
                    log_menu_event("callback processed", update_id=uid, status=out.get("status"))
            continue

        out = process_crypto_telegram_menu_message(
            upd,
            cfg=cfg,
            broker=broker,
            runner_cfg=runner_cfg,
            db_path=db_path,
            network=network,
        )
        if out:
            summary["menu"].append(out)

    summary["offset_after"] = advance_offset_from_updates(cfg.output_dir, updates)
    return summary


def poll_crypto_telegram_menu_once(
    cfg: CryptoTelegramConfig,
    broker: UpbitBroker,
    *,
    runner_cfg: Any | None = None,
    db_path: str | None = None,
    network: bool = False,
) -> list[dict[str, Any]]:
    """Backward-compatible alias: full update poll (menu + callbacks)."""
    summary = poll_telegram_updates_once(
        cfg,
        broker,
        runner_cfg=runner_cfg,
        db_path=db_path,
        network=network,
        process_approvals=True,
    )
    return list(summary.get("menu") or [])
