"""FastAPI 서버 — 러너 제어 / 설정 / 상태 / 로그 스트리밍."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from deepsignal.web_ui.auth import (
    get_auth_config,
    create_session_token,
    verify_init_data,
    verify_session_token,
)
from deepsignal.web_ui.event_bus import bus as _event_bus
from deepsignal.web_ui.runner_manager import (
    get_runner_status,
    restart_runner,
    set_pause_state,
    start_runner,
    stop_runner,
)
from deepsignal.web_ui.settings_manager import read_settings, write_settings

# ──────────────────────────────────────────
# 경로 설정
# ──────────────────────────────────────────
_HERE = Path(__file__).parent
_STATIC = _HERE / "static"

@asynccontextmanager
async def _lifespan(app: FastAPI):
    from deepsignal.web_ui.state_watcher import watch_loop
    task = asyncio.create_task(watch_loop(_OUTPUT_DIR, _event_bus))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="DeepSignal Web UI", docs_url=None, redoc_url=None, lifespan=_lifespan)

# 전역 설정 (run_web_ui()에서 주입; 직접 uvicorn 실행 시 __file__ 기준 경로 사용)
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
_OUTPUT_DIR: Path = _PROJECT_ROOT / "outputs"
_ENV_PATH: Path = Path(".env")

# ──────────────────────────────────────────
# 인증 미들웨어
# ──────────────────────────────────────────

_AUTH_BYPASS_PREFIXES = ("/static/", "/auth/", "/ws/")
_AUTH_BYPASS_EXACT   = {"/", "/favicon.ico"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    from dotenv import load_dotenv
    load_dotenv(str(_ENV_PATH), override=False)
    cfg = get_auth_config()

    # 인증 불필요: 로컬 또는 REQUIRE_AUTH=false
    client_host = (request.client.host if request.client else "") or ""
    is_local = client_host in ("127.0.0.1", "::1", "localhost")
    if not cfg["require_auth"] or is_local:
        return await call_next(request)

    # 인증 우회 경로
    path = request.url.path
    if path in _AUTH_BYPASS_EXACT or any(path.startswith(p) for p in _AUTH_BYPASS_PREFIXES):
        return await call_next(request)

    # 세션 토큰 확인 (헤더 우선, 쿼리 파라미터 폴백)
    token = request.headers.get("X-Session-Token") or request.query_params.get("token")
    if token and cfg["bot_token"]:
        user_id = verify_session_token(token, cfg["bot_token"])
        if user_id and (cfg["allowed_id"] == 0 or user_id == cfg["allowed_id"]):
            return await call_next(request)

    # SPA 경로(페이지)는 index.html 반환 → JS에서 인증
    # no-cache: 텔레그램 미니앱이 옛 index.html(옛 app.js 버전)을 캐시하지 않도록
    if not path.startswith("/api/") and not path.startswith("/ws/"):
        return FileResponse(
            str(_STATIC / "index.html"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
        )

    return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)


# ──────────────────────────────────────────
# 텔레그램 알림 헬퍼
# ──────────────────────────────────────────

def _telegram_notify_sync(text: str) -> None:
    """텔레그램으로 단순 메시지 발송 (블로킹, to_thread 에서 호출)."""
    from dotenv import load_dotenv
    load_dotenv(str(_ENV_PATH), override=False)
    token = os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    try:
        from deepsignal.live_trading.telegram.approval import telegram_api_post
        telegram_api_post(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            bot_token=token,
        )
    except Exception:
        pass


async def _telegram_notify(text: str) -> None:
    await asyncio.to_thread(_telegram_notify_sync, text)


# ──────────────────────────────────────────
# 대시보드 데이터 헬퍼
# ──────────────────────────────────────────

def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _make_broker():
    """환경변수 로드 후 UpbitBroker 생성."""
    from dotenv import load_dotenv
    load_dotenv(str(_ENV_PATH), override=False)
    from deepsignal.crypto_trading.upbit_config import load_upbit_config_from_env
    from deepsignal.crypto_trading.upbit_broker import UpbitBroker
    cfg = load_upbit_config_from_env()
    return UpbitBroker(cfg)


def _get_holdings() -> list[dict[str, Any]]:
    """Upbit 보유 코인 조회."""
    try:
        broker = _make_broker()
        holdings = broker.get_crypto_holdings()
        return [
            {
                "market": h.market,
                "quantity": round(float(h.total_quantity), 6),
                "avg_buy_price": round(float(h.avg_buy_price or 0), 2),
                "current_price": round(float(h.current_price or 0), 2),
                "pnl_pct": round(float(h.pnl_pct or 0), 2),
                "valuation_krw": round(float(h.valuation_krw or 0), 0),
            }
            for h in holdings
        ]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("holdings fetch failed: %s", e)
        return []


def _get_balance() -> dict[str, float]:
    try:
        broker = _make_broker()
        for b in broker.get_balances():
            if b.currency.upper() == "KRW":
                available = float(b.balance or 0)
                locked    = float(b.locked or 0)
                return {"available": available, "total": available + locked}
        return {"available": 0.0, "total": 0.0}
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("balance fetch failed: %s", e)
        return {"available": 0.0, "total": 0.0}


def _make_kis_broker(safe_mode: bool = True):
    """환경변수 로드 후 KISBroker 생성. 취소 등 실주문엔 safe_mode=False."""
    from dotenv import load_dotenv
    load_dotenv(str(_ENV_PATH), override=False)
    from deepsignal.live_trading.kis_config import load_kis_config_from_env
    from deepsignal.live_trading.kis_broker import KISBroker
    cfg = load_kis_config_from_env(load_dotenv_file=False)
    return KISBroker(cfg, safe_mode=safe_mode)


# ── KIS 잔고 캐시 (rate-limit 시 마지막 성공값 재사용) ────────────────
import threading as _threading
import time as _time

_stock_cache_lock = _threading.Lock()
_stock_cache: dict[str, Any] = {}   # keys: result, balance, ts
_STOCK_CACHE_TTL = 20               # 20초 이내면 캐시 재사용 (KIS 초당한도 보호 + 빠른 대시보드)


def _get_stock_cache() -> tuple[list | None, dict | None]:
    with _stock_cache_lock:
        if _stock_cache and (_time.monotonic() - _stock_cache.get("ts", 0)) < _STOCK_CACHE_TTL:
            return _stock_cache.get("result"), _stock_cache.get("balance")
    return None, None


def _set_stock_cache(result: list, balance: dict) -> None:
    with _stock_cache_lock:
        _stock_cache["result"]  = result
        _stock_cache["balance"] = balance
        _stock_cache["ts"]      = _time.monotonic()


_etf_price_cache: dict[str, Any] = {}  # {price, ts}
_ETF_PRICE_TTL = 120  # 2분


def _fetch_etf_price_yf(ticker_kr: str) -> float | None:
    """yfinance로 한국 ETF 현재가 조회 (KIS rate-limit 무관). 360750 → '360750.KS'."""
    try:
        import yfinance as _yf  # type: ignore[import]
        yf_sym = ticker_kr.strip() + ".KS"
        now = _time.monotonic()
        cached = _etf_price_cache.get(yf_sym)
        if cached and (now - cached["ts"]) < _ETF_PRICE_TTL:
            return cached["price"]
        hist = _yf.Ticker(yf_sym).history(period="1d", interval="1m")
        if hist.empty:
            return None
        price = float(hist["Close"].iloc[-1])
        _etf_price_cache[yf_sym] = {"price": price, "ts": now}
        return price
    except Exception:
        return None


def _get_regime_trend_position() -> dict[str, Any] | None:
    """REGIME_TREND_STATE.json에서 ETF 보유분 읽기.

    현재가는 yfinance로 실시간 조회 (KIS rate-limit 무관, 2분 캐시).
    yfinance 실패 시 진입가로 대체.
    """
    try:
        state_file = _OUTPUT_DIR / "REGIME_TREND_STATE.json"
        if not state_file.is_file():
            return None
        import json as _json
        # 계좌 스냅샷과 대조해 외부 매도된 유령 포지션은 자동 정리
        try:
            from deepsignal.live_trading.regime_trend import reconcile_position_with_account
            state = reconcile_position_with_account(_OUTPUT_DIR)
        except Exception:
            state = _json.loads(state_file.read_text(encoding="utf-8"))
        if not state.get("holding"):
            return None
        etf = state.get("etf", "360750")
        qty = int(state.get("qty") or 0)
        entry_price = float(state.get("entry_price") or 0)
        cash_after_buy = float(state.get("cash_after_buy") or 0)
        if qty <= 0 or not etf:
            return None
        cur_price = _fetch_etf_price_yf(etf) or entry_price
        pnl_pct = round((cur_price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0.0
        return {
            "symbol": etf,
            "name": "TIGER 미국S&P500 (추세추종)",
            "quantity": qty,
            "avg_price": round(entry_price, 0),
            "current_price": round(cur_price, 0),
            "market_value": round(qty * cur_price, 0),
            "pnl_pct": pnl_pct,
            "_from_state": True,
            "_cash_after_buy": cash_after_buy,   # 총자산 계산용 현금 잔고
        }
    except Exception:
        return None


def _get_all_stock_data() -> tuple[list[dict[str, Any]], dict[str, float]]:
    """KIS 잔고·포지션을 단일 API 호출로 조회 (rate-limit + 캐싱).

    - get_positions() 하나로 output1(종목)+output2(잔고요약) 동시 취득
    - KIS API 초당 한도 초과(rt_cd=1) 시 마지막 성공 캐시(60초 TTL) 반환
    - 추세추종 ETF(360750)가 output1에 없으면 REGIME_TREND_STATE.json으로 보완
    """
    log = __import__("logging").getLogger(__name__)
    # 정상 경로도 시간 캐시 우선 (대시보드 빠른 폴링 시 KIS 호출 폭주 방지)
    c_r, c_b = _get_stock_cache()
    if c_r is not None:
        return c_r, c_b
    try:
        broker = _make_kis_broker()
        positions = broker.get_positions()  # output1+output2 한 번에 가져옴

        # ── rate-limit 감지 ─────────────────────────────────
        body = broker.last_balance_response_body or {}
        if str(body.get("rt_cd", "0")) != "0":
            # API 실패 → 캐시 반환
            cached_result, cached_balance = _get_stock_cache()
            if cached_result is not None:
                log.debug("KIS rate-limited, using cache")
                return cached_result, cached_balance
            # 캐시도 없으면 state 파일 fallback (yfinance 현재가 + 매수후 현금)
            rt = _get_regime_trend_position()
            if rt:
                cash_rem = rt.get("_cash_after_buy", 0.0)
                etf_val  = rt["market_value"]
                return [rt], {"balance": cash_rem, "withdrawable": cash_rem,
                               "total_equity": cash_rem + etf_val}
            return [], {"balance": 0.0, "withdrawable": 0.0, "total_equity": 0.0}

        # ── 포지션 리스트 ──────────────────────────────
        result: list[dict[str, Any]] = []
        seen_symbols: set[str] = set()
        for p in positions:
            avg = float(p.avg_price or 0)
            cur = float(p.current_price or 0)
            pnl_pct = round((cur - avg) / avg * 100, 2) if avg > 0 else None
            name = (p.raw.get("prdt_abrv_name") or p.raw.get("prdt_name") or "").strip()
            result.append({
                "symbol": p.symbol,
                "name": name or p.symbol,
                "quantity": p.quantity,
                "avg_price": round(avg, 0),
                "current_price": round(cur, 0),
                "market_value": round(float(p.market_value or 0), 0),
                "pnl_pct": pnl_pct,
            })
            seen_symbols.add(str(p.symbol))
        # 추세추종 ETF가 누락된 경우 state 파일로 보완
        rt = _get_regime_trend_position()
        if rt and rt["symbol"] not in seen_symbols:
            # 보유 포지션에서 현재가 업데이트 시도
            result.append(rt)

        # ── 잔고 요약 (output2 재사용, 추가 API 호출 없음) ────────────
        out2 = (body.get("output2") or [{}])[0]
        # KIS output2 주요 필드:
        #   tot_evlu_amt     = 총평가금액 (예수금+유가증권 합계, 한투 앱 총자산과 일치)
        #   dnca_tot_amt     = 예수금총금액 (현금성 잔고)
        #   prvs_rcdl_excc_amt = 가수도정산금액 (T+2 미수)
        #   nxdy_excc_amt    = 익일 이체가능금액
        total_equity = float(out2.get("tot_evlu_amt") or 0)
        cash         = float(out2.get("dnca_tot_amt") or out2.get("nxdy_excc_amt") or 0)
        withdrawable = float(out2.get("prvs_rcdl_excc_amt") or cash)

        # tot_evlu_amt가 0이면 positions 평가액 합계로 보완
        if total_equity <= 0:
            total_equity = cash + sum(h["market_value"] for h in result)

        balance = {
            "balance": cash,
            "withdrawable": withdrawable,
            "total_equity": total_equity,
        }
        # 성공 캐시 저장
        _set_stock_cache(result, balance)
        return result, balance

    except Exception as e:
        log.warning("stock data fetch failed: %s", e)
        # 예외 발생 시 캐시 우선, 없으면 state 파일
        cached_result, cached_balance = _get_stock_cache()
        if cached_result is not None:
            return cached_result, cached_balance
        rt = _get_regime_trend_position()
        if rt:
            cash_rem = rt.get("_cash_after_buy", 0.0)
            return [rt], {"balance": cash_rem, "withdrawable": cash_rem,
                          "total_equity": cash_rem + rt["market_value"]}
        return [], {"balance": 0.0, "withdrawable": 0.0, "total_equity": 0.0}


# 하위 호환 래퍼 (다른 곳에서 단독 호출 시)
def _get_stock_holdings() -> list[dict[str, Any]]:
    return _get_all_stock_data()[0]


def _get_stock_balance() -> dict[str, float]:
    return _get_all_stock_data()[1]


def _get_last_plan() -> dict[str, Any]:
    plan = _read_json(_OUTPUT_DIR / "CRYPTO_ORDER_PLAN.json")
    if not plan:
        return {}
    return {
        "market": plan.get("market"),
        "side": plan.get("side"),
        "status": plan.get("status"),
        "reason": plan.get("reason", "")[:120],
        "pnl_pct": plan.get("pnl_pct"),
        "created_at": plan.get("created_at"),
        "sell_trigger": plan.get("sell_trigger"),
        "technical_score": plan.get("technical_score"),
        "macro_score": plan.get("macro_score"),
        "final_score": plan.get("final_score"),
        "macro_regime": plan.get("macro_regime"),
        "score_breakdown": plan.get("score_breakdown", {}),
        "quality_gates": plan.get("quality_gates", {}),
    }


def _get_crypto_approval() -> dict[str, Any]:
    try:
        path = _OUTPUT_DIR / "crypto_telegram_approval_request.json"
        if not path.is_file():
            return {"pending": False}
        data = json.loads(path.read_text(encoding="utf-8"))
        status = data.get("status", "")
        # side는 플랜 파일에서 읽음
        plan_side = "sell"
        try:
            plan = _read_json(_OUTPUT_DIR / "CRYPTO_ORDER_PLAN.json")
            plan_side = plan.get("side", "sell")
        except Exception:
            pass
        # 만료 여부 확인
        expires_str = data.get("expires_at", "")
        if status == "PENDING" and expires_str:
            try:
                from datetime import timezone
                from dateutil.parser import parse as _parse
                expires_dt = _parse(expires_str)
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                from datetime import datetime as _dt
                if _dt.now(tz=timezone.utc) > expires_dt:
                    status = "EXPIRED"
                    data["status"] = "EXPIRED"
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        show = status in ("PENDING", "EXPIRED")
        return {
            "pending": status == "PENDING",
            "show_banner": show,
            "status": status,
            "token": data.get("token", ""),
            "market": data.get("market", ""),
            "display_name": data.get("display_name", ""),
            "side": plan_side,
            "krw_amount": data.get("krw_amount", 0),
            "current_price": data.get("current_price", 0),
            "reason": (data.get("reason", "") or "")[:300],
            "message_text": (data.get("message_text", "") or "")[:500],
            "created_at": data.get("created_at", ""),
            "expires_at": data.get("expires_at", ""),
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("crypto approval read failed: %s", e)
        return {"pending": False, "show_banner": False}


def _get_stock_approval() -> dict[str, Any]:
    try:
        path = _OUTPUT_DIR / "TELEGRAM_APPROVAL_STATE.json"
        if not path.is_file():
            return {"pending": False}
        data = json.loads(path.read_text(encoding="utf-8"))
        status = data.get("status", "")
        expires_str = data.get("expires_at", "")
        if status == "PENDING" and expires_str:
            try:
                from datetime import timezone
                from dateutil.parser import parse as _parse
                from datetime import datetime as _dt
                expires_dt = _parse(expires_str)
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                if _dt.now(tz=timezone.utc) > expires_dt:
                    status = "EXPIRED"
                    data["status"] = "EXPIRED"
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return {
            "pending": status == "PENDING",
            "status": status,
            "token": data.get("token", ""),
            "order_count": data.get("order_count", 0),
            "total_order_value": data.get("total_order_value", 0),
            "created_at": data.get("created_at", ""),
            "expires_at": data.get("expires_at", ""),
            "request_markdown": (data.get("request_markdown", "") or "")[:80],
            "manual_live_approve_command": data.get("manual_live_approve_command", ""),
            "plan_warnings": data.get("plan_warnings", []),
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("stock approval read failed: %s", e)
        return {"pending": False}


def _handle_crypto_approval_action(action: str, token: str) -> tuple[bool, str]:
    import logging
    from dotenv import load_dotenv
    load_dotenv(str(_ENV_PATH), override=False)
    try:
        from deepsignal.crypto_trading.telegram.flow import (
            load_crypto_approval_request, _save_request, _write_audit,
            STATUS_APPROVED, STATUS_REJECTED, STATUS_PENDING,
            execute_approved_crypto_order,
        )
        from deepsignal.live_trading.time_utils import now_kst_iso
    except ImportError as e:
        return False, f"모듈 로드 실패: {e}"

    approval = load_crypto_approval_request(_OUTPUT_DIR)
    if approval is None:
        return False, "승인 요청 없음"
    if approval.token != token:
        return False, "토큰 불일치"
    if approval.status != STATUS_PENDING:
        return False, f"이미 처리됨: {approval.status}"

    def _bg_notify(msg: str) -> None:
        """to_thread 속 동기 컨텍스트에서 비동기 텔레그램 알림 전송."""
        import threading as _t
        def _run() -> None:
            try:
                asyncio.run(_telegram_notify(msg))
            except Exception:
                pass
        _t.Thread(target=_run, daemon=True).start()

    if action == "reject":
        approval.status = STATUS_REJECTED
        _save_request(_OUTPUT_DIR, approval)
        _write_audit(_OUTPUT_DIR, {"action": "rejected", "source": "web_ui", "token": token, "ts": now_kst_iso()})
        _event_bus.publish_sync("crypto_approval_update", {
            "action": "rejected", "market": approval.market, "source": "web_ui",
        })
        coin = approval.market.split("-")[-1] if "-" in approval.market else approval.market
        _bg_notify(f"🚫 매수 거부  ·  Upbit\n*{approval.market}*")
        return True, f"{approval.market} 주문 거부됨"

    if action == "approve":
        # ── 사전 호가창 품질 체크 (승인 전 매수벽/스프레드 검사) ──
        try:
            from deepsignal.crypto_trading.execution.engine import check_orderbook_quality
            broker_check = _make_broker()
            ob = broker_check.get_orderbook(approval.market)
            ob_result = check_orderbook_quality(ob)
            if not ob_result.allowed:
                reasons_ko = []
                for r in ob_result.reasons:
                    if "매수벽" in r or "bid_vol" in r.lower():
                        reasons_ko.append(f"매수벽 부족 (bid/ask 비율 {ob_result.bid_ask_ratio:.2f}x)")
                    elif "스프레드" in r or "spread" in r.lower():
                        reasons_ko.append(f"스프레드 과다 ({ob_result.spread_pct:.3f}%)")
                    else:
                        reasons_ko.append(r[:60])
                block_msg = " · ".join(reasons_ko) or "체결 품질 미달"
                return False, f"현재 체결 불가 — {block_msg}\n잠시 후 다시 시도하거나 거부하세요."
        except Exception:
            pass  # 호가창 체크 실패 시 실행 진행

        approval.status = STATUS_APPROVED
        _save_request(_OUTPUT_DIR, approval)
        _write_audit(_OUTPUT_DIR, {"action": "approved", "source": "web_ui", "token": token, "ts": now_kst_iso()})
        _event_bus.publish_sync("crypto_approval_update", {
            "action": "approved", "market": approval.market, "source": "web_ui",
        })
        try:
            from deepsignal.crypto_trading.crypto_order_plan import load_crypto_plan
            broker = _make_broker()
            plan_path = Path(approval.plan_path)
            if not plan_path.is_file():
                plan_path = _OUTPUT_DIR / "CRYPTO_ORDER_PLAN.json"
            plan = load_crypto_plan(plan_path)
            result = execute_approved_crypto_order(broker, plan, execute=True, output_dir=_OUTPUT_DIR)
            return True, f"{approval.market} 주문 실행 완료"
        except Exception as e:
            logging.getLogger(__name__).error("crypto web approval execution failed: %s", e)
            err_str = str(e)
            if "매수벽" in err_str or "bid_vol" in err_str:
                return False, f"체결 실패 — 매수벽 부족\n매도 압력이 강한 상황입니다. 잠시 후 재시도하거나 거부하세요."
            if "스프레드" in err_str or "spread" in err_str.lower():
                return False, f"체결 실패 — 스프레드 과다\n호가 차이가 너무 큽니다. 잠시 후 재시도하세요."
            return False, f"체결 실패: {err_str[:120]}"

    return False, f"알 수 없는 액션: {action}"


def _handle_stock_approval_action(action: str, token: str) -> tuple[bool, str]:
    req_path = _OUTPUT_DIR / "TELEGRAM_APPROVAL_STATE.json"
    if not req_path.is_file():
        return False, "승인 요청 없음"
    try:
        data = json.loads(req_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"파일 읽기 오류: {e}"

    if data.get("token") != token:
        return False, "토큰 불일치"
    if data.get("status") != "PENDING":
        return False, f"이미 처리됨: {data.get('status')}"

    symbol = data.get("symbol", data.get("ticker", "주식"))
    from datetime import date as _date
    if action == "reject":
        data["status"] = "REJECTED"
        data["action"] = "reject"
        req_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        state_path = _OUTPUT_DIR / f"telegram_approval_state_{token}.json"
        state_path.write_text(json.dumps({"status": "TELEGRAM_APPROVAL_REJECTED", "action": "reject", "source": "web_ui"}, ensure_ascii=False), encoding="utf-8")
        _event_bus.publish_sync("stock_approval_update", {
            "action": "rejected", "symbol": symbol, "source": "web_ui",
        })
        asyncio.create_task(_telegram_notify(f"🚫 매수 거부  ·  KIS\n*{symbol}*"))
        return True, "주식 주문 거부됨"

    if action == "approve":
        data["status"] = "APPROVED"
        data["action"] = "approve"
        req_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        state_path = _OUTPUT_DIR / f"telegram_approval_state_{token}.json"
        state_path.write_text(json.dumps({"status": "TELEGRAM_APPROVAL_APPROVED_MANUAL_EXECUTION_REQUIRED", "action": "approve", "source": "web_ui"}, ensure_ascii=False), encoding="utf-8")
        _event_bus.publish_sync("stock_approval_update", {
            "action": "approved", "symbol": symbol, "source": "web_ui",
        })
        asyncio.create_task(_telegram_notify(f"🟢 매수 승인  ·  KIS\n*{symbol}*\n웹 UI에서 승인됨"))
        cmd = data.get("manual_live_approve_command", "python main.py execute-last-approved")
        return True, f"주식 주문 승인됨 — 실행: {cmd}"

    if action == "halt":
        halt_path = _OUTPUT_DIR / f"telegram_approval_halt_{_date.today().strftime('%Y%m%d')}.json"
        halt_path.write_text(json.dumps({"halt": True, "source": "web_ui"}), encoding="utf-8")
        _event_bus.publish_sync("stock_approval_update", {"action": "halt", "symbol": symbol, "source": "web_ui"})
        asyncio.create_task(_telegram_notify(f"⏹ 오늘 승인 중단  ·  KIS\n*{symbol}*"))
        return True, "오늘 주식 승인 중단됨"

    return False, f"알 수 없는 액션: {action}"


# ──────────────────────────────────────────
# GSQS 단타 신호 헬퍼
# ──────────────────────────────────────────

def _get_scalping_scores() -> dict[str, Any]:
    """feature_vectors.json → GSQS 실시간 채점 결과."""
    fv_path = _OUTPUT_DIR / "binance_stream" / "feature_vectors.json"
    if not fv_path.is_file():
        return {"scores": [], "total": 0, "buy_count": 0, "exists": False}
    try:
        data = json.loads(fv_path.read_text(encoding="utf-8"))
        feature_names = data.get("feature_names", [])
        # pipeline이 쓰는 포맷: {"vectors": {sym: [...]}} 또는 {"symbols": {sym: [...]}}
        vectors: dict[str, list] = data.get("vectors") or data.get("symbols") or {}

        from deepsignal.crypto_trading.signal.scalping_scorer import compute_scalping_score

        scores = []
        for sym, vec in vectors.items():
            feats = {name: float(vec[i]) for i, name in enumerate(feature_names) if i < len(vec)}
            s = compute_scalping_score(sym, feats)
            scores.append({
                "symbol": s.symbol,
                "score": round(s.score, 1),
                "decision": s.decision,
                "is_buy": s.is_buy,
                "sub_scores": {k: int(round(v)) for k, v in s.sub_scores.items()},
                "notes": s.notes[:3],
            })

        scores.sort(key=lambda x: x["score"], reverse=True)
        return {
            "scores": scores,
            "total": len(scores),
            "buy_count": sum(1 for s in scores if s["is_buy"]),
            "updated_at": data.get("generated_at"),
            "exists": True,
        }
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("scalping scores failed: %s", exc)
        return {"scores": [], "error": str(exc), "exists": False}


def _get_scalping_signals() -> dict[str, Any]:
    """signal_log.jsonl → 신호 이력 & 승률 통계."""
    try:
        from deepsignal.crypto_trading.signal.signal_logger import SignalLogger
        sig = SignalLogger(_OUTPUT_DIR)
        summary = sig.summary(horizon=5)

        # 최근 20개 신호 (역순)
        log_path = _OUTPUT_DIR / "signal_log.jsonl"
        recent: list[dict[str, Any]] = []
        if log_path.is_file():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    recent.append({
                        "signal_id":       d.get("signal_id"),
                        "symbol":          d.get("symbol"),
                        "score":           d.get("score"),
                        "decision":        d.get("decision"),
                        "ts_ms":           d.get("ts_ms"),
                        "entry_price":     d.get("entry_price"),
                        "ret_5m":          d.get("ret_5m"),
                        "outcome_complete":d.get("outcome_complete", False),
                    })
                    if len(recent) >= 20:
                        break
                except Exception:
                    pass

        summary["recent"] = recent
        return summary
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("scalping signals failed: %s", exc)
        return {"error": str(exc), "total_signals": 0, "recent": []}


def _get_macro_status() -> dict[str, Any]:
    """feature_vectors.json 의 ret_1m 으로 동시 급변동 비율을 계산해 반환.

    서버는 파이프라인 프로세스와 분리되어 있으므로 히스토리 없이 스냅샷 1장만 사용.
    → sync_ratio (Layer 1) 은 현재 스냅샷에서 계산 가능.
    → mean_correlation (Layer 2) 은 파이프라인이 실행 중일 때만 의미 있고 여기서는 0 반환.
    """
    try:
        import json as _json, time as _time, os as _os
        from deepsignal.market_data.feature_engine.spec import FEATURE_NAMES

        fv_path = _OUTPUT_DIR / "binance_stream" / "feature_vectors.json"
        if not fv_path.is_file():
            return {
                "active": False, "trigger_reason": "", "active_since_ms": None,
                "decay_remaining_seconds": 0, "sync_ratio": 0.0,
                "mean_correlation": 0.0, "top_movers": [], "n_symbols": 0,
                "data_available": False,
            }

        raw = _json.loads(fv_path.read_text(encoding="utf-8"))
        # {"feature_names": [...], "vectors": {"SYM": [...]}}
        vectors: dict = raw.get("vectors", {}) if isinstance(raw, dict) else {}

        feat_idx = {name: i for i, name in enumerate(FEATURE_NAMES)}
        ret_idx  = feat_idx.get("ret_1m", 0)

        ret_map: dict[str, float] = {}
        for sym, vec in vectors.items():
            if isinstance(vec, list) and len(vec) > ret_idx:
                ret_map[sym] = float(vec[ret_idx])

        if not ret_map:
            return {
                "active": False, "trigger_reason": "", "active_since_ms": None,
                "decay_remaining_seconds": 0, "sync_ratio": 0.0,
                "mean_correlation": 0.0, "top_movers": [], "n_symbols": 0,
                "data_available": True,
            }

        # Layer 1: 동시 급변동 비율
        threshold = float(_os.getenv("MACRO_RET_MIN", "0.003"))
        up = sum(1 for r in ret_map.values() if r >= threshold)
        dn = sum(1 for r in ret_map.values() if r <= -threshold)
        sync_ratio = max(up, dn) / len(ret_map)

        # top movers
        top_movers = [
            {"symbol": s, "ret_1m": round(r, 6)}
            for s, r in sorted(ret_map.items(), key=lambda x: abs(x[1]), reverse=True)[:8]
        ]

        # MacroGuard 임계값
        sync_thr = float(_os.getenv("MACRO_SYNC_THRESHOLD", "0.70"))
        active = sync_ratio >= sync_thr
        reason = f"SYNC:{sync_ratio:.0%}" if active else ""

        return {
            "active": active,
            "trigger_reason": reason,
            "active_since_ms": int(_time.time() * 1000) if active else None,
            "decay_remaining_seconds": int(float(_os.getenv("MACRO_DECAY_MINUTES", "5")) * 60) if active else 0,
            "sync_ratio": round(sync_ratio, 4),
            "mean_correlation": 0.0,    # 히스토리 없이 계산 불가
            "top_movers": top_movers,
            "n_symbols": len(ret_map),
            "data_available": True,
        }
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("macro status failed: %s", exc)
        return {"active": False, "error": str(exc), "data_available": False}


def _get_scalping_weights() -> dict[str, Any]:
    """optimized_weights.json → 가중치 최적화 상태 (확장)."""
    try:
        from deepsignal.crypto_trading.signal.weight_optimizer import WeightOptimizer
        opt = WeightOptimizer(_OUTPUT_DIR)
        status = opt.status()  # progress_pct, next_run_at, last_* 포함

        weights_path = _OUTPUT_DIR / "optimized_weights.json"
        if weights_path.is_file():
            saved = json.loads(weights_path.read_text(encoding="utf-8"))
            status.update({
                "optimized_at":      saved.get("optimized_at"),
                "expected_win_rate": saved.get("expected_win_rate"),
                "default_win_rate":  saved.get("default_win_rate"),
                "improvement":       saved.get("improvement"),
                "applied":           saved.get("applied"),
            })
        else:
            status.update({
                "optimized_at": None,
                "expected_win_rate": None,
                "default_win_rate": None,
                "improvement": None,
                "applied": None,
            })

        # 점수 구간별 승률 (SignalLogger에서 직접 조회)
        try:
            from deepsignal.crypto_trading.signal.signal_logger import SignalLogger
            sig_logger = SignalLogger(_OUTPUT_DIR)
            band_stats = sig_logger.win_rate_stats(horizon=5)
            status["win_rate_bands"] = [
                {
                    "band": s.band,
                    "n": s.n_signals,
                    "win_rate_5m": round(s.win_rate_5m, 4) if s.win_rate_5m == s.win_rate_5m else None,
                    "reliable": s.is_reliable(),
                }
                for s in band_stats
            ]
        except Exception:
            status["win_rate_bands"] = []

        return status
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("scalping weights failed: %s", exc)
        return {"error": str(exc)}


# ──────────────────────────────────────────
# 인증 엔드포인트
# ──────────────────────────────────────────

class TelegramAuthRequest(BaseModel):
    init_data: str

@app.post("/auth/telegram")
async def auth_telegram(req: TelegramAuthRequest) -> JSONResponse:
    from dotenv import load_dotenv
    load_dotenv(str(_ENV_PATH), override=False)
    cfg = get_auth_config()

    if not cfg["bot_token"]:
        return JSONResponse({"ok": False, "error": "bot_token not configured"}, status_code=500)

    user = verify_init_data(req.init_data, cfg["bot_token"])
    if user is None:
        return JSONResponse({"ok": False, "error": "invalid or expired initData"}, status_code=401)

    user_id = int(user.get("id", 0))
    if cfg["allowed_id"] and user_id != cfg["allowed_id"]:
        return JSONResponse({"ok": False, "error": "unauthorized user"}, status_code=403)

    token = create_session_token(user_id, cfg["bot_token"], hours=cfg["session_hours"])
    return JSONResponse({
        "ok": True,
        "token": token,
        "user": {"id": user_id, "first_name": user.get("first_name", "")},
        "expires_hours": cfg["session_hours"],
    })


@app.get("/auth/status")
async def auth_status(request: Request) -> JSONResponse:
    from dotenv import load_dotenv
    load_dotenv(str(_ENV_PATH), override=False)
    cfg = get_auth_config()

    client_host = (request.client.host if request.client else "") or ""
    is_local = client_host in ("127.0.0.1", "::1", "localhost")
    if not cfg["require_auth"] or is_local:
        return JSONResponse({"ok": True, "auth": "local_bypass"})

    token = request.headers.get("X-Session-Token") or request.query_params.get("token")
    if token and cfg["bot_token"]:
        user_id = verify_session_token(token, cfg["bot_token"])
        if user_id:
            return JSONResponse({"ok": True, "auth": "session", "user_id": user_id})

    return JSONResponse({"ok": False, "auth": "none"}, status_code=401)


@app.get("/auth/config")
async def auth_config_endpoint() -> JSONResponse:
    """프론트엔드가 인증 필요 여부 확인용."""
    from dotenv import load_dotenv
    load_dotenv(str(_ENV_PATH), override=False)
    cfg = get_auth_config()
    return JSONResponse({
        "require_auth": cfg["require_auth"],
        "public_url":   cfg["public_url"],
    })


# ──────────────────────────────────────────
# API 라우터
# ──────────────────────────────────────────

@app.get("/api/status")
async def api_status() -> JSONResponse:
    runner = get_runner_status(_OUTPUT_DIR)
    # 코인(Upbit) + 주식(KIS) 동시 조회
    # KIS는 _get_all_stock_data()로 단일 API 호출 → rate-limit 방지
    (holdings, balance), all_stock = await asyncio.gather(
        asyncio.gather(
            asyncio.to_thread(_get_holdings),
            asyncio.to_thread(_get_balance),
        ),
        asyncio.to_thread(_get_all_stock_data),
    )
    stock_holdings, stock_balance = all_stock
    last_plan = _get_last_plan()
    thresholds = _read_json(_OUTPUT_DIR / "CRYPTO_ACTIVE_THRESHOLDS.json")

    return JSONResponse({
        "runner": runner,
        "balance_krw": balance.get("available", 0) if isinstance(balance, dict) else balance,
        "balance_krw_total": balance.get("total", 0) if isinstance(balance, dict) else balance,
        "holdings": holdings,
        "stock_holdings": stock_holdings,
        "stock_balance_krw": stock_balance.get("balance", 0) if isinstance(stock_balance, dict) else stock_balance,
        "stock_withdrawable_krw": stock_balance.get("withdrawable", 0) if isinstance(stock_balance, dict) else stock_balance,
        "stock_total_equity": stock_balance.get("total_equity", 0) if isinstance(stock_balance, dict) else 0,
        "last_plan": last_plan,
        "thresholds": {
            "take_profit_pct": thresholds.get("take_profit_pct"),
            "stop_loss_pct": thresholds.get("stop_loss_pct"),
            "min_volume_ratio": thresholds.get("min_volume_ratio"),
        },
        "server_time": datetime.now().isoformat(),
        "platform": platform.system(),
    })


# ── 러너 제어 ──────────────────────────────

@app.post("/api/runner/start")
async def api_start() -> JSONResponse:
    ok, msg = await asyncio.to_thread(start_runner, _OUTPUT_DIR, _PROJECT_ROOT)
    if ok:
        await _event_bus.publish("runner_control", {"action": "started", "source": "web_ui"})
        await _telegram_notify("▶️ [웹UI] 코인 러너 시작됨")
    return JSONResponse({"ok": ok, "message": msg})


@app.post("/api/runner/stop")
async def api_stop() -> JSONResponse:
    ok, msg = await asyncio.to_thread(stop_runner, _OUTPUT_DIR)
    if ok:
        await _event_bus.publish("runner_control", {"action": "stopped", "source": "web_ui"})
        await _telegram_notify("⏹ [웹UI] 코인 러너 중지됨")
    return JSONResponse({"ok": ok, "message": msg})


@app.post("/api/runner/restart")
async def api_restart() -> JSONResponse:
    ok, msg = await asyncio.to_thread(restart_runner, _OUTPUT_DIR, _PROJECT_ROOT)
    if ok:
        await _event_bus.publish("runner_control", {"action": "restarted", "source": "web_ui"})
        await _telegram_notify("🔄 [웹UI] 코인 러너 재시작됨")
    return JSONResponse({"ok": ok, "message": msg})


class PauseRequest(BaseModel):
    paused: bool
    reason: str = ""


@app.post("/api/runner/pause")
async def api_pause(req: PauseRequest) -> JSONResponse:
    ok, msg = await asyncio.to_thread(
        set_pause_state, _OUTPUT_DIR, paused=req.paused, reason=req.reason
    )
    # 비상버튼: 전역 킬스위치(TRADING_HALT)도 함께 토글 → 코인·국내·해외·추세·레버리지
    # 모든 신규 매수 즉시 중단(손절·익절 매도는 보호 위해 계속). 재개 시 해제.
    try:
        from deepsignal.risk.trading_halt import engage_halt, clear_halt
        if req.paused:
            await asyncio.to_thread(engage_halt, _OUTPUT_DIR, "전체 일시정지(웹 비상버튼)", source="web_ui")
        else:
            await asyncio.to_thread(clear_halt, _OUTPUT_DIR)
        await _telegram_notify(
            "⛔ <b>전체 일시정지</b> — 모든 시장 신규매수 중단 (매도는 계속)" if req.paused
            else "▶️ <b>일시정지 해제</b> — 자동매매 재개")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("pause halt toggle failed: %s", e)
    return JSONResponse({"ok": ok, "message": msg})


# ── 승인 관리 ─────────────────────────────

@app.get("/api/approval")
async def api_approval() -> JSONResponse:
    crypto, stock = await asyncio.gather(
        asyncio.to_thread(_get_crypto_approval),
        asyncio.to_thread(_get_stock_approval),
    )
    return JSONResponse({"crypto": crypto, "stock": stock})


class ApprovalActionRequest(BaseModel):
    type: str
    action: str
    token: str


@app.post("/api/approval/action")
async def api_approval_action(req: ApprovalActionRequest) -> JSONResponse:
    if req.type == "crypto":
        ok, msg = await asyncio.to_thread(_handle_crypto_approval_action, req.action, req.token)
    elif req.type == "stock":
        ok, msg = await asyncio.to_thread(_handle_stock_approval_action, req.action, req.token)
    else:
        return JSONResponse({"ok": False, "message": "알 수 없는 유형"})
    return JSONResponse({"ok": ok, "message": msg})


# ── 분석 데이터 ────────────────────────────

@app.get("/api/plan/detail")
async def api_plan_detail() -> JSONResponse:
    return JSONResponse(_read_json(_OUTPUT_DIR / "CRYPTO_ORDER_PLAN.json"))


@app.get("/api/sizing")
async def api_sizing() -> JSONResponse:
    data = dict(_read_json(_OUTPUT_DIR / "CRYPTO_ACTIVE_SIZING.json"))
    # 동적 TP/SL 오버레이 (BTCUSDT 바 데이터 기반, 파일값보다 우선)
    try:
        from deepsignal.crypto_trading.risk.sizing import compute_crypto_dynamic_tpsl
        result = compute_crypto_dynamic_tpsl("KRW-BTC")
        if result is not None:
            tp_d, sl_d, src_d = result
            from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS
            _c = DEFAULT_ANALYSIS_CONDITIONS.crypto
            from deepsignal.crypto_trading.risk.sizing import _clamp
            data["take_profit_pct"] = round(_clamp(tp_d, float(_c.tp_pct_min), float(_c.tp_pct_max)), 3)
            data["stop_loss_pct"]   = round(_clamp(sl_d, float(_c.sl_pct_min), float(_c.sl_pct_max)), 3)
            data["tp_source"] = src_d
            # grade / market_state 추출
            parts = src_d.split("_")  # e.g. "dynamic_A_SIDEWAYS"
            if len(parts) >= 3:
                data["dynamic_grade"] = parts[1]
                data["dynamic_market_state"] = "_".join(parts[2:])
    except Exception:
        pass
    return JSONResponse(data)


@app.get("/api/universe")
async def api_universe() -> JSONResponse:
    return JSONResponse(_read_json(_OUTPUT_DIR / "CRYPTO_UNIVERSE_SNAPSHOT.json"))


# 코인 한글명 캐시 (프로세스 수명 동안 유지)
_coin_names_cache: dict[str, dict] = {}
_coin_names_fetched_at: float = 0.0

@app.get("/api/coin-names")
async def api_coin_names() -> JSONResponse:
    """업비트 KRW 마켓 한글명 매핑 (캐시 1시간).

    Returns:
        {market: {korean_name, english_name}} 예: {"KRW-BTC": {"korean_name": "비트코인", "english_name": "Bitcoin"}}
    """
    import time as _time
    global _coin_names_cache, _coin_names_fetched_at
    if _coin_names_cache and (_time.time() - _coin_names_fetched_at) < 3600:
        return JSONResponse(_coin_names_cache)
    try:
        import requests as _req
        resp = _req.get(
            "https://api.upbit.com/v1/market/all?isDetails=false",
            timeout=5,
        )
        data = resp.json()
        result: dict[str, dict] = {}
        for m in data:
            mkt = str(m.get("market", ""))
            if mkt.startswith("KRW-"):
                result[mkt] = {
                    "korean_name":  m.get("korean_name",  ""),
                    "english_name": m.get("english_name", ""),
                }
        _coin_names_cache = result
        _coin_names_fetched_at = _time.time()
        return JSONResponse(result)
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning("coin names fetch failed: %s", _e)
        return JSONResponse(_coin_names_cache or {})


@app.get("/api/kimchi-premium")
async def api_kimchi_premium() -> JSONResponse:
    """실시간 김치프리미엄 조회.

    Returns:
        premiums: {symbol: {premium_pct, level, upbit_krw, binance_usdt, usd_krw_rate, fair_krw, ts}}
        usd_krw_rate: 현재 USD/KRW 환율
        summary: 전체 심볼 평균 프리미엄
    """
    try:
        from deepsignal.crypto_trading.kimchi_premium import (
            get_all_premiums, get_usd_krw_rate, DEFAULT_SYMBOLS
        )
        import asyncio

        loop = asyncio.get_event_loop()
        premiums = await loop.run_in_executor(None, get_all_premiums, None)
        usd_krw = await loop.run_in_executor(None, get_usd_krw_rate)

        data = {sym: p.to_dict() for sym, p in premiums.items()}
        avg = (
            round(sum(p.premium_pct for p in premiums.values()) / len(premiums), 2)
            if premiums else None
        )
        return JSONResponse({
            "premiums": data,
            "usd_krw_rate": usd_krw,
            "average_premium_pct": avg,
            "symbols_requested": DEFAULT_SYMBOLS,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/slippage")
async def api_slippage(limit: int = 100) -> JSONResponse:
    path = _OUTPUT_DIR / "CRYPTO_FILL_SLIPPAGE.jsonl"
    if not path.is_file():
        return JSONResponse({"entries": [], "exists": False})
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
        return JSONResponse({"entries": list(reversed(entries)), "exists": True, "total": len(lines)})
    except Exception as e:
        return JSONResponse({"entries": [], "error": str(e), "exists": False})


@app.get("/api/trade-history")
async def api_trade_history(limit: int = 30) -> JSONResponse:
    """거래 내역: approval audit 파일 + CRYPTO_FILL_SLIPPAGE.jsonl 병합."""
    import glob as _glob

    # slippage JSONL → 최신 항목을 (market, side, limit_price) 키로 색인
    slip_entries: list[dict] = []
    slip_path = _OUTPUT_DIR / "CRYPTO_FILL_SLIPPAGE.jsonl"
    if slip_path.is_file():
        for line in slip_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                slip_entries.append(json.loads(line))
            except Exception:
                pass
    # (market, side, rounded_limit) → 가장 마지막 slippage 항목
    # 슬리피지 1000bps(10%) 초과 = 단가 오류 데이터 → 제외
    slip_map: dict = {}
    for e in slip_entries:
        try:
            bps = float(e.get("slippage_bps") or 0)
            if bps > 1000:  # 단가 단위 버그 데이터 제외
                continue
            key = (e.get("market", ""), e.get("side", ""), round(float(e.get("limit_price", 0)), 1))
            slip_map[key] = e
        except Exception:
            pass

    # audit 파일 glob (crypto + live + telegram 방식 모두)
    patterns = [
        str(_OUTPUT_DIR / "crypto_telegram_approval_audit_*.json"),
        str(_OUTPUT_DIR / "live_approval_audit_*.json"),
        str(_OUTPUT_DIR / "telegram_approval_audit_*.json"),
    ]
    audit_files: list[str] = []
    for pat in patterns:
        audit_files.extend(_glob.glob(pat))
    audit_files.sort()

    trades: list[dict] = []
    for fpath in audit_files[-(limit * 4):]:  # 여유분 확보 후 자름
        try:
            raw = json.loads(Path(fpath).read_text(encoding="utf-8"))
        except Exception:
            continue
        plan = raw.get("plan") or {}
        if not plan.get("market"):  # plan 없는 간이 audit 파일 건너뜀
            continue
        result = raw.get("result") or {}
        market = plan.get("market") or ""
        side = plan.get("side") or ""
        limit_price = plan.get("limit_price")
        order_krw = float(plan.get("krw_amount") or result.get("krw_amount") or 0)
        display_name = plan.get("display_name") or market.replace("KRW-", "")
        created_at = plan.get("created_at") or ""
        executed = raw.get("executed", False)
        status = raw.get("status", "")
        reason = (plan.get("reason") or "")[:80]

        # slippage 매칭
        try:
            slip_key = (market, side, round(float(limit_price or 0), 1))
        except Exception:
            slip_key = (market, side, 0.0)
        slip = slip_map.get(slip_key) or {}
        fill_price = slip.get("fill_price")
        slippage_bps = slip.get("slippage_bps")
        slip_ts = slip.get("ts")

        trades.append({
            "ts": created_at,
            "fill_ts": slip_ts,
            "market": market,
            "display_name": display_name,
            "side": side,
            "order_krw": order_krw,
            "limit_price": limit_price,
            "fill_price": fill_price,
            "slippage_bps": slippage_bps,
            "executed": executed,
            "approval_status": status,
            "reason": reason,
            "type": "crypto",
        })

    trades.reverse()  # 최신 순
    return JSONResponse({"trades": trades[:limit], "total": len(audit_files)})


# ── 미체결 주문 조회·취소 ──────────────────────────────
def _get_open_orders_crypto() -> list[dict]:
    """Upbit 미체결 지정가 주문 (state=wait)."""
    try:
        broker = _make_broker()
        rows = broker.get_open_orders()
        out = []
        for r in rows:
            try:
                vol = float(r.get("volume") or 0)
                rem = float(r.get("remaining_volume") or 0)
                price = float(r.get("price") or 0)
                out.append({
                    "uuid": r.get("uuid"),
                    "market": r.get("market"),
                    "side": "매수" if str(r.get("side")) == "bid" else "매도",
                    "side_raw": r.get("side"),
                    "price": price,
                    "volume": vol,
                    "remaining": rem,
                    "filled": round(vol - rem, 8),
                    "amount": round(price * vol, 0),
                    "created_at": r.get("created_at", ""),
                })
            except Exception:
                continue
        return out
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("open orders(crypto) 조회 실패: %s", exc)
        return []


def _get_open_orders_stock() -> list[dict]:
    """KIS 국내주식 미체결 (remaining_quantity>0). 취소 가능."""
    try:
        from datetime import date as _d, timedelta as _td
        broker = _make_kis_broker()
        today = _d.today()
        s = (today - _td(days=3)).strftime("%Y%m%d")
        e = today.strftime("%Y%m%d")
        statuses = broker.get_order_status(start_date=s, end_date=e)
        out = []
        for st in statuses:
            rq = st.remaining_quantity or 0
            if rq and rq > 0:
                raw = st.raw if isinstance(st.raw, dict) else {}
                # 취소 주문(매도취소/매수취소 행)은 대기중이 아님 — 제외
                _mr = raw.get("matched_row") or {}
                _dvsn = str(_mr.get("sll_buy_dvsn_cd_name") or "")
                if "취소" in _dvsn or "정정" in _dvsn:
                    continue
                org_no = (raw.get("ord_gno_brno") or raw.get("ORD_GNO_BRNO")
                          or raw.get("KRX_FWDG_ORD_ORGNO") or raw.get("krx_fwdg_ord_orgno") or "")
                out.append({
                    "order_id": st.order_id,
                    "symbol": st.symbol,
                    "side": "매수" if str(st.side).upper() == "BUY" else "매도",
                    "price": st.order_price or 0,
                    "remaining": rq,
                    "quantity": st.quantity or 0,
                    "org_no": str(org_no or ""),
                })
        return out
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("open orders(stock) 조회 실패: %s", exc)
        return []


def _get_open_orders_overseas() -> list[dict]:
    """KIS 해외주식 미체결 주문. 취소 가능."""
    try:
        broker = _make_kis_broker()
        rows = broker.get_open_orders_overseas()
        out = []
        for r in rows:
            out.append({
                "order_id": r.get("order_id"),
                "symbol": r.get("ticker") or r.get("symbol"),
                "exchange": r.get("exchange"),
                "side": "매수" if str(r.get("side")).upper() == "BUY" else "매도",
                "price": r.get("price_usd") or 0,
                "remaining": r.get("remaining") or 0,
                "quantity": r.get("qty") or 0,
            })
        return out
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("open orders(overseas) 조회 실패: %s", exc)
        return []


def _get_open_orders_all(usd_rate: float = 1350.0) -> list[dict]:
    """3개 시장 미체결 주문 통합 — 표시·취소용 정규화 목록."""
    items: list[dict] = []
    # 코인
    for r in _get_open_orders_crypto():
        items.append({
            "market_type": "crypto",
            "id": r.get("uuid"),
            "symbol": r.get("market"),
            "name": (r.get("market") or "").replace("KRW-", ""),
            "side": r.get("side"),
            "price": r.get("price"), "price_unit": "₩",
            "remaining": r.get("remaining"), "quantity": r.get("volume"),
            "amount_krw": r.get("amount"),
            "cancellable": True,
            "cancel": {"uuid": r.get("uuid")},
        })
    # 국내주식
    for r in _get_open_orders_stock():
        amt = float(r.get("price") or 0) * float(r.get("remaining") or 0)
        items.append({
            "market_type": "stock",
            "id": r.get("order_id"),
            "symbol": r.get("symbol"),
            "name": r.get("symbol"),
            "side": r.get("side"),
            "price": r.get("price"), "price_unit": "₩",
            "remaining": r.get("remaining"), "quantity": r.get("quantity"),
            "amount_krw": round(amt),
            "cancellable": True,
            "cancel": {"order_id": r.get("order_id"), "qty": r.get("remaining"),
                       "org_no": r.get("org_no", "")},
        })
    # 해외주식
    for r in _get_open_orders_overseas():
        px = float(r.get("price") or 0)
        rem = float(r.get("remaining") or 0)
        items.append({
            "market_type": "overseas",
            "id": r.get("order_id"),
            "symbol": r.get("symbol"),
            "name": r.get("symbol"),
            "side": r.get("side"),
            "price": px, "price_unit": "$",
            "remaining": rem, "quantity": r.get("quantity"),
            "amount_krw": round(px * rem * usd_rate),
            "amount_usd": round(px * rem, 2),
            "cancellable": True,
            "cancel": {"order_id": r.get("order_id"), "ticker": r.get("symbol"),
                       "qty": r.get("remaining"), "exchange": r.get("exchange", "NASD")},
        })
    return items


def _get_candles_crypto(market: str, count: int) -> list[dict]:
    """Upbit 일봉 OHLC (종목 상세 차트용). date 포함 (LW Charts time 필드용)."""
    try:
        broker = _make_broker()
        rows = broker.get_daily_candles(market, count=count)
        result = []
        for r in rows:
            dt = str(r.get("candle_date_time_kst") or r.get("candle_date_time_utc") or "")[:10]
            result.append({
                "date":   dt,
                "open":   r.get("opening_price", 0),
                "high":   r.get("high_price", 0),
                "low":    r.get("low_price", 0),
                "close":  r.get("trade_price", 0),
                "volume": r.get("candle_acc_trade_volume", 0),
            })
        result.sort(key=lambda x: x["date"])
        return result
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("candles(crypto) 조회 실패: %s", exc)
        return []


def _get_minute_candles_crypto(market: str, unit: int, count: int) -> list[dict]:
    """Upbit 분봉 OHLC (오늘/1주 인트라데이용). time=UTC 초(정수)."""
    from datetime import datetime, timezone
    try:
        broker = _make_broker()
        m = market.strip().upper()
        n = max(2, min(int(count), 200))
        rows = broker._request("GET", f"/candles/minutes/{int(unit)}", params={"market": m, "count": n})
        if not isinstance(rows, list):
            return []
        result = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            utc = str(r.get("candle_date_time_utc") or "")
            try:
                ts = int(datetime.fromisoformat(utc).replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                continue
            result.append({
                "time":   ts,
                "open":   float(r.get("opening_price", 0) or 0),
                "high":   float(r.get("high_price", 0) or 0),
                "low":    float(r.get("low_price", 0) or 0),
                "close":  float(r.get("trade_price", 0) or 0),
                "volume": float(r.get("candle_acc_trade_volume", 0) or 0),
            })
        result.sort(key=lambda x: x["time"])
        return result
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("minute candles(crypto) 조회 실패: %s", exc)
        return []


def _get_orderbook_crypto(market: str, levels: int) -> dict:
    """Upbit 호가창 (매수/매도벽 깊이)."""
    try:
        broker = _make_broker()
        ob = broker.get_orderbook(market, levels=levels)
        units = ob.get("orderbook_units") or []
        bids, asks = [], []
        for u in units:
            bids.append({"price": float(u.get("bid_price", 0) or 0), "size": float(u.get("bid_size", 0) or 0)})
            asks.append({"price": float(u.get("ask_price", 0) or 0), "size": float(u.get("ask_size", 0) or 0)})
        bid_total = sum(b["size"] for b in bids)
        ask_total = sum(a["size"] for a in asks)
        return {
            "market": market, "bids": bids, "asks": asks,
            "bid_total": round(bid_total, 4), "ask_total": round(ask_total, 4),
            "bid_ask_ratio": round(bid_total / ask_total, 2) if ask_total else 0,
        }
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("orderbook 조회 실패: %s", exc)
        return {"market": market, "bids": [], "asks": [], "error": str(exc)}


@app.get("/api/orderbook")
async def api_orderbook(market: str, levels: int = 8) -> JSONResponse:
    """종목 호가창 (코인). market 예: KRW-BTC."""
    levels = max(1, min(int(levels), 15))
    return JSONResponse(await asyncio.to_thread(_get_orderbook_crypto, market, levels))


@app.get("/api/candles")
async def api_candles(market: str, count: int = 30) -> JSONResponse:
    """종목 일봉 OHLC. 현재 코인(Upbit)만 지원. market 예: KRW-BTC."""
    count = max(5, min(int(count), 100))
    candles = await asyncio.to_thread(_get_candles_crypto, market, count)
    return JSONResponse({"market": market, "candles": candles})


def _get_all_markets() -> list[dict[str, Any]]:
    """Upbit KRW 전체 마켓 + 24h 현재가·등락률. 1분 캐시."""
    cache = _chart_cache.get("__all_markets__")
    if cache and (_time.monotonic() - cache["ts"]) < 60:
        return cache["data"]
    try:
        broker = _make_broker()
        # 전체 KRW 마켓 목록
        mkts_raw = broker._request("GET", "/market/all", params={"isDetails": "false"})
        krw_codes = [m["market"] for m in mkts_raw if isinstance(m, dict) and str(m.get("market","")).startswith("KRW-")]
        # 24h 시세 (한 번에 최대 100개씩)
        tickers: list[dict] = []
        for i in range(0, len(krw_codes), 100):
            batch = ",".join(krw_codes[i:i+100])
            tickers += broker._request("GET", "/ticker", params={"markets": batch}) or []
        # 한글 이름 매핑
        name_map = {m["market"]: m.get("korean_name", "") for m in mkts_raw if isinstance(m, dict)}
        result = []
        for t in tickers:
            mkt = t.get("market", "")
            sym = mkt.replace("KRW-", "")
            chg = float(t.get("signed_change_rate", 0) or 0) * 100
            result.append({
                "symbol": mkt,
                "ticker": sym,
                "name": name_map.get(mkt, sym),
                "price": float(t.get("trade_price", 0) or 0),
                "change_pct": round(chg, 2),
                "volume_krw": float(t.get("acc_trade_price_24h", 0) or 0),
            })
        # 거래대금 기준 정렬
        result.sort(key=lambda x: -x["volume_krw"])
        _chart_cache["__all_markets__"] = {"data": result, "ts": _time.monotonic()}
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("all markets fetch failed: %s", e)
        return []


# 해외주식 / 국내주식 확장 목록
_STOCK_UNIVERSE = [
    {"symbol":"360750","ticker":"360750","name":"TIGER 미국S&P500"},
    {"symbol":"069500","ticker":"069500","name":"KODEX 200"},
    {"symbol":"133690","ticker":"133690","name":"TIGER 나스닥100"},
    {"symbol":"114800","ticker":"114800","name":"KODEX 인버스"},
    {"symbol":"252670","ticker":"252670","name":"KODEX 200선물인버스2X"},
    {"symbol":"005930","ticker":"005930","name":"삼성전자"},
    {"symbol":"000660","ticker":"000660","name":"SK하이닉스"},
    {"symbol":"035420","ticker":"035420","name":"NAVER"},
    {"symbol":"035720","ticker":"035720","name":"카카오"},
    {"symbol":"005380","ticker":"005380","name":"현대차"},
    {"symbol":"000270","ticker":"000270","name":"기아"},
    {"symbol":"051910","ticker":"051910","name":"LG화학"},
    {"symbol":"006400","ticker":"006400","name":"삼성SDI"},
    {"symbol":"207940","ticker":"207940","name":"삼성바이오로직스"},
    {"symbol":"068270","ticker":"068270","name":"셀트리온"},
    {"symbol":"012330","ticker":"012330","name":"현대모비스"},
    {"symbol":"055550","ticker":"055550","name":"신한지주"},
    {"symbol":"105560","ticker":"105560","name":"KB금융"},
    {"symbol":"086790","ticker":"086790","name":"하나금융지주"},
    {"symbol":"030200","ticker":"030200","name":"KT"},
    {"symbol":"017670","ticker":"017670","name":"SK텔레콤"},
    {"symbol":"032830","ticker":"032830","name":"삼성생명"},
    {"symbol":"034730","ticker":"034730","name":"SK"},
    {"symbol":"003550","ticker":"003550","name":"LG"},
    {"symbol":"096770","ticker":"096770","name":"SK이노베이션"},
]

_OVERSEAS_UNIVERSE = [
    {"symbol":"SPY",  "ticker":"SPY",  "name":"S&P500 ETF"},
    {"symbol":"QQQ",  "ticker":"QQQ",  "name":"나스닥100 ETF"},
    {"symbol":"DIA",  "ticker":"DIA",  "name":"다우존스 ETF"},
    {"symbol":"IWM",  "ticker":"IWM",  "name":"러셀2000 ETF"},
    {"symbol":"SOXL", "ticker":"SOXL", "name":"반도체3X ETF"},
    {"symbol":"GLD",  "ticker":"GLD",  "name":"금 ETF"},
    {"symbol":"TLT",  "ticker":"TLT",  "name":"장기국채 ETF"},
    {"symbol":"NVDA", "ticker":"NVDA", "name":"엔비디아"},
    {"symbol":"AAPL", "ticker":"AAPL", "name":"애플"},
    {"symbol":"MSFT", "ticker":"MSFT", "name":"마이크로소프트"},
    {"symbol":"GOOGL","ticker":"GOOGL","name":"알파벳"},
    {"symbol":"AMZN", "ticker":"AMZN", "name":"아마존"},
    {"symbol":"META", "ticker":"META", "name":"메타"},
    {"symbol":"TSLA", "ticker":"TSLA", "name":"테슬라"},
    {"symbol":"AMD",  "ticker":"AMD",  "name":"AMD"},
    {"symbol":"AVGO", "ticker":"AVGO", "name":"브로드컴"},
    {"symbol":"NFLX", "ticker":"NFLX", "name":"넷플릭스"},
    {"symbol":"ORCL", "ticker":"ORCL", "name":"오라클"},
    {"symbol":"CRM",  "ticker":"CRM",  "name":"세일즈포스"},
    {"symbol":"ADBE", "ticker":"ADBE", "name":"어도비"},
    {"symbol":"INTC", "ticker":"INTC", "name":"인텔"},
    {"symbol":"QCOM", "ticker":"QCOM", "name":"퀄컴"},
    {"symbol":"MU",   "ticker":"MU",   "name":"마이크론"},
    {"symbol":"PYPL", "ticker":"PYPL", "name":"페이팔"},
    {"symbol":"SHOP", "ticker":"SHOP", "name":"쇼피파이"},
    {"symbol":"COIN", "ticker":"COIN", "name":"코인베이스"},
    {"symbol":"PLTR", "ticker":"PLTR", "name":"팔란티어"},
    {"symbol":"SNOW", "ticker":"SNOW", "name":"스노우플레이크"},
]


def _get_yf_prices_batch(yf_symbols: list[str]) -> dict[str, tuple[float, float]]:
    """yfinance 일괄 시세 조회 → {yf_symbol: (현재가, 등락률%)}. 2분 캐시."""
    cache_key = "__yfprices__:" + ",".join(sorted(yf_symbols))
    cached = _chart_cache.get(cache_key)
    if cached and (_time.monotonic() - cached["ts"]) < 120:
        return cached["data"]
    out: dict[str, dict] = {}
    import logging
    _log = logging.getLogger(__name__)
    # threads=False — 이미 to_thread 워커 안에서 실행되므로 yfinance 내부 스레딩 비활성
    try:
        import yfinance as _yf
        df = _yf.download(yf_symbols, period="5d", interval="1d",
                          progress=False, group_by="ticker", threads=False)
        for sym in yf_symbols:
            try:
                sub = df if len(yf_symbols) == 1 else df[sym]
                closes = sub["Close"].dropna()
                if len(closes) < 1:
                    continue
                price = float(closes.iloc[-1])
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else price
                chg = (price - prev) / prev * 100 if prev else 0.0
                try:
                    vol = float(sub["Volume"].dropna().iloc[-1])
                except Exception:
                    vol = 0.0
                out[sym] = {"price": price, "change": round(chg, 2), "value": round(price * vol)}
            except Exception:
                continue
    except Exception as e:
        _log.warning("yf prices batch failed: %s", e)
    # 폴백: 배치가 비면 개별 Ticker().history (차트 경로와 동일, 검증됨)
    if not out:
        try:
            import yfinance as _yf
            for sym in yf_symbols:
                try:
                    h = _yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=True)
                    cl = h["Close"].dropna()
                    if len(cl) < 1:
                        continue
                    price = float(cl.iloc[-1])
                    prev = float(cl.iloc[-2]) if len(cl) >= 2 else price
                    try:
                        vol = float(h["Volume"].dropna().iloc[-1])
                    except Exception:
                        vol = 0.0
                    out[sym] = {"price": price, "change": round((price-prev)/prev*100 if prev else 0.0, 2),
                                "value": round(price * vol)}
                except Exception:
                    continue
        except Exception as e:
            _log.warning("yf prices fallback failed: %s", e)
    if out:  # 성공한 경우에만 캐시 (빈 결과는 다음 호출에서 재시도)
        _chart_cache[cache_key] = {"data": out, "ts": _time.monotonic()}
    return out


@app.get("/api/markets")
async def api_markets(asset_type: str = "crypto") -> JSONResponse:
    """전체 종목 목록 (차트 사이드바용).
    asset_type: crypto | stock | overseas
    """
    if asset_type == "crypto":
        items = await asyncio.to_thread(_get_all_markets)
        return JSONResponse({"items": items})
    elif asset_type == "stock":
        items = [dict(x) for x in _STOCK_UNIVERSE]
        prices = await asyncio.to_thread(_get_yf_prices_batch, [x["symbol"] + ".KS" for x in items])
        for it in items:
            p = prices.get(it["symbol"] + ".KS")
            if p:
                it["price"], it["change_pct"], it["volume_krw"] = p["price"], p["change"], p["value"]
        items.sort(key=lambda x: -(x.get("volume_krw") or 0))
        return JSONResponse({"items": items})
    else:
        items = [dict(x) for x in _OVERSEAS_UNIVERSE]
        prices = await asyncio.to_thread(_get_yf_prices_batch, [x["symbol"] for x in items])
        for it in items:
            p = prices.get(it["symbol"])
            if p:
                it["price"], it["change_pct"], it["volume_krw"] = p["price"], p["change"], p["value"]
        items.sort(key=lambda x: -(x.get("volume_krw") or 0))
        return JSONResponse({"items": items})


# ── yfinance 차트 캐시 ────────────────────────────────────────
_chart_cache: dict[str, Any] = {}
_CHART_CACHE_TTL = 300  # 5분


def _get_chart_candles_yf(yf_symbol: str, period: str = "3mo", interval: str = "1d") -> list[dict[str, Any]]:
    """yfinance로 OHLCV 조회 (국내주식·해외주식·지수). 5분 캐시.

    interval='1d'면 일봉(time=날짜 문자열), 분/시간봉이면 time=UTC 초(정수).
    """
    cache_key = f"{yf_symbol}:{period}:{interval}"
    cached = _chart_cache.get(cache_key)
    if cached and (_time.monotonic() - cached["ts"]) < _CHART_CACHE_TTL:
        return cached["data"]
    intraday = interval != "1d"
    try:
        import yfinance as _yf
        hist = _yf.Ticker(yf_symbol).history(period=period, interval=interval, auto_adjust=True)
        if hist.empty:
            return []
        result = []
        for idx, row in hist.iterrows():
            item = {
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            }
            if intraday:
                try:
                    item["time"] = int(idx.timestamp())
                except Exception:
                    continue
            else:
                item["date"] = str(idx.date())
            result.append(item)
        _chart_cache[cache_key] = {"data": result, "ts": _time.monotonic()}
        return result
    except Exception:
        return []


@app.get("/api/charts")
async def api_charts(
    asset_type: str = "crypto",
    symbol: str = "KRW-BTC",
    period: str = "3mo",
) -> JSONResponse:
    """차트 OHLCV 데이터.
    asset_type: crypto (Upbit) | stock (KIS 국내, yfinance .KS) | overseas (yfinance)
    period: 1d(오늘) | 1wk(1주) | 1mo | 3mo | 1y
    """
    # 코인: 분봉 unit·count / 일봉 count
    _CRYPTO_INTRADAY = {"1d": (10, 144), "1wk": (60, 168)}   # (분 단위, 개수)
    _CRYPTO_DAILY = {"1mo": 30, "3mo": 90, "6mo": 120, "1y": 220}
    # yfinance: period→(yf_period, interval)
    _YF_MAP = {
        "1d":  ("1d", "5m"), "1wk": ("5d", "30m"),
        "1mo": ("1mo", "1d"), "3mo": ("3mo", "1d"), "6mo": ("6mo", "1d"), "1y": ("1y", "1d"),
    }

    if asset_type == "crypto":
        if period in _CRYPTO_INTRADAY:
            unit, cnt = _CRYPTO_INTRADAY[period]
            candles = await asyncio.to_thread(_get_minute_candles_crypto, symbol, unit, cnt)
        else:
            candles = await asyncio.to_thread(_get_candles_crypto, symbol, _CRYPTO_DAILY.get(period, 90))
        price = candles[-1]["close"] if candles else 0
        return JSONResponse({"symbol": symbol, "candles": candles, "price": price, "currency": "KRW"})

    yf_period, interval = _YF_MAP.get(period, ("3mo", "1d"))
    if asset_type == "stock":
        candles = await asyncio.to_thread(_get_chart_candles_yf, symbol.strip() + ".KS", yf_period, interval)
        cur = "KRW"
    else:  # overseas
        candles = await asyncio.to_thread(_get_chart_candles_yf, symbol.upper(), yf_period, interval)
        cur = "USD"
    price = candles[-1]["close"] if candles else 0
    return JSONResponse({"symbol": symbol, "candles": candles, "price": price, "currency": cur})


@app.get("/api/orders/open")
async def api_open_orders(market: str = "all") -> JSONResponse:
    """미체결 주문 조회. market: all | crypto | stock | overseas."""
    if market == "crypto":
        items = await asyncio.to_thread(_get_open_orders_crypto)
        return JSONResponse({"market": "crypto", "cancellable": True, "items": items})
    if market == "stock":
        items = await asyncio.to_thread(_get_open_orders_stock)
        return JSONResponse({"market": "stock", "cancellable": True, "items": items})
    if market == "overseas":
        items = await asyncio.to_thread(_get_open_orders_overseas)
        return JSONResponse({"market": "overseas", "cancellable": True, "items": items})
    # 통합 (기본)
    items = await asyncio.to_thread(_get_open_orders_all)
    return JSONResponse({"market": "all", "cancellable": True, "items": items,
                         "count": len(items)})


@app.get("/api/orders/failures")
async def api_order_failures(limit: int = 5) -> JSONResponse:
    """코인 주문 실패/취소 이력 — 코인별 묶음(최신 1건+반복횟수, 최소 표시)."""
    try:
        from deepsignal.crypto_trading.execution.order_failure_log import load_crypto_order_failures_summary
        items = await asyncio.to_thread(load_crypto_order_failures_summary, str(_OUTPUT_DIR), limit=int(limit))
        return JSONResponse({"items": items, "count": len(items)})
    except Exception as exc:
        return JSONResponse({"items": [], "count": 0, "error": str(exc)[:120]})


class CancelOrderRequest(BaseModel):
    uuid: str | None = None             # 코인
    market_type: str = "crypto"         # crypto | stock | overseas
    order_id: str | None = None         # KIS 주문번호
    ticker: str | None = None           # 해외 종목
    qty: int | None = None              # 취소 수량
    exchange: str | None = None         # 해외 거래소
    org_no: str | None = None           # 국내 주문지점번호


@app.post("/api/orders/cancel")
async def api_cancel_order(req: CancelOrderRequest) -> JSONResponse:
    """미체결 주문 취소. 시장별 라우팅. 페이퍼/safe 모드면 차단됨."""
    mt = (req.market_type or "crypto").strip()
    try:
        # ── 코인 (Upbit) ──────────────────────────────
        if mt == "crypto":
            if not req.uuid:
                return JSONResponse({"ok": False, "message": "uuid 필요"}, status_code=400)
            broker = _make_broker()
            result = await asyncio.to_thread(broker.cancel_order, req.uuid)
            _event_bus.publish_sync("crypto_approval_update", {"action": "order_cancelled", "uuid": req.uuid})
            _bg_notify(f"⚪ 주문 취소  ·  Upbit\n주문번호 {req.uuid[:8]}…")
            return JSONResponse({"ok": True, "message": "주문 취소됨", "result": result})

        # ── 국내주식 (KIS) ────────────────────────────
        if mt == "stock":
            if not req.order_id:
                return JSONResponse({"ok": False, "message": "order_id 필요"}, status_code=400)
            broker = _make_kis_broker(safe_mode=False)
            result = await asyncio.to_thread(
                broker.cancel_order, req.order_id,
                quantity=int(req.qty or 0), org_no=str(req.org_no or ""),
                all_qty=True, execute=True,
            )
            ok = getattr(result, "status", "") == "KIS_CANCEL_SUBMITTED"
            _event_bus.publish_sync("stock_approval_update", {"action": "order_cancelled", "symbol": req.order_id})
            _bg_notify(f"⚪ 주문 취소  ·  국내주식\n주문번호 {req.order_id}")
            return JSONResponse({"ok": ok, "message": "주문 취소됨" if ok else getattr(result, "message", "취소 실패"),
                                 "result": getattr(result, "raw", {})})

        # ── 해외주식 (KIS) ────────────────────────────
        if mt == "overseas":
            if not req.order_id or not req.ticker:
                return JSONResponse({"ok": False, "message": "order_id·ticker 필요"}, status_code=400)
            broker = _make_kis_broker(safe_mode=False)
            result = await asyncio.to_thread(
                broker.cancel_order_overseas, req.order_id, req.ticker,
                int(req.qty or 0), exchange=str(req.exchange or "NASD"), execute=True,
            )
            ok = getattr(result, "status", "") == "KIS_CANCEL_SUBMITTED"
            _event_bus.publish_sync("stock_approval_update", {"action": "order_cancelled", "symbol": req.ticker})
            _bg_notify(f"⚪ 주문 취소  ·  해외주식\n{req.ticker} 주문번호 {req.order_id}")
            return JSONResponse({"ok": ok, "message": "주문 취소됨" if ok else getattr(result, "message", "취소 실패"),
                                 "result": getattr(result, "raw", {})})

        return JSONResponse({"ok": False, "message": f"알 수 없는 시장: {mt}"}, status_code=400)
    except Exception as exc:
        msg = str(exc)
        if "PAPER_MODE" in msg or "dry_run" in msg:
            return JSONResponse({"ok": False, "message": "페이퍼/모의 모드에서는 취소 불가"})
        return JSONResponse({"ok": False, "message": f"취소 실패: {msg[:120]}"})


@app.get("/api/account/trades")
async def api_account_trades(
    tab: str = "crypto",       # crypto | stock | overseas | all
    period: str = "1m",        # 1w | 1m | 3m | custom
    date_from: str = "",       # YYYYMMDD (custom)
    date_to: str = "",         # YYYYMMDD (custom)
    type: str = "all",         # all | buy | sell
    symbol: str = "",          # 종목/코인 필터
    limit: int = 50,
    offset: int = 0,
) -> JSONResponse:
    """코인·국장·해외장 거래내역 통합 조회."""
    from datetime import date, timedelta

    # ── 기간 계산 ──────────────────────────────────────────────────
    today = date.today()
    if period == "1w":
        d_from = today - timedelta(days=7)
    elif period == "3m":
        d_from = today - timedelta(days=90)
    elif period == "custom" and date_from:
        try:
            d_from = date.fromisoformat(date_from[:8])
        except ValueError:
            d_from = today - timedelta(days=30)
    else:  # 기본 1m
        d_from = today - timedelta(days=30)

    d_to = today
    if period == "custom" and date_to:
        try:
            d_to = date.fromisoformat(date_to[:8])
        except ValueError:
            d_to = today

    start_dt = d_from.isoformat()   # YYYY-MM-DD
    end_dt   = d_to.isoformat()

    tabs_to_fetch = ["crypto", "stock", "overseas"] if tab == "all" else [tab]
    all_items: list[dict] = []

    for t in tabs_to_fetch:
        if t == "crypto":
            all_items.extend(_fetch_crypto_trades(start_dt, end_dt, type_filter=type, symbol=symbol))
        elif t == "stock":
            all_items.extend(_fetch_stock_trades(start_dt, end_dt, type_filter=type, symbol=symbol))
        elif t == "overseas":
            all_items.extend(_fetch_overseas_trades(start_dt, end_dt, type_filter=type, symbol=symbol))

    # 날짜 내림차순 정렬
    all_items.sort(key=lambda x: x.get("executed_at") or "", reverse=True)

    total = len(all_items)
    page  = all_items[offset : offset + limit]
    return JSONResponse({"items": page, "total": total, "offset": offset, "limit": limit})


def _fetch_crypto_trades(
    start_dt: str, end_dt: str,
    type_filter: str = "all",
    symbol: str = "",
) -> list[dict]:
    """Upbit 체결 완료 주문 조회 (API 우선, 로컬 파일 폴백)."""
    import glob as _glob
    from datetime import datetime, timezone

    items: list[dict] = []

    # ── 1차: Upbit REST API /v1/orders (state=done) ────────────────
    try:
        broker = _make_broker()
        # 업비트 page/limit (페이지당 최대 100) — 여러 페이지를 모아 최대 ~1000건 조회
        raw_orders: list = []
        for _page in range(1, 11):
            params: dict = {"state": "done", "order_by": "desc", "limit": 100, "page": _page}
            batch = broker._request("GET", "/orders", params=params)
            if not isinstance(batch, list) or not batch:
                break
            raw_orders.extend(batch)
            if len(batch) < 100:
                break
        if isinstance(raw_orders, list):
            for o in raw_orders:
                side = str(o.get("side") or "").lower()   # bid=매수, ask=매도
                if side == "bid":
                    side_kr = "buy"
                elif side == "ask":
                    side_kr = "sell"
                else:
                    side_kr = side
                if type_filter != "all" and side_kr != type_filter:
                    continue

                mkt = str(o.get("market") or "")
                sym = mkt.replace("KRW-", "")
                if symbol and symbol.upper() not in (mkt.upper(), sym.upper()):
                    continue

                created_raw = o.get("created_at") or ""
                # 날짜 범위 필터
                ts_date = created_raw[:10] if created_raw else ""
                if ts_date and (ts_date < start_dt or ts_date > end_dt):
                    continue

                price    = float(o.get("price") or o.get("avg_price") or 0)
                vol      = float(o.get("executed_volume") or o.get("volume") or 0)
                amount   = float(o.get("trades_price") or 0) or round(price * vol, 0)
                paid_fee = float(o.get("paid_fee") or 0)

                items.append({
                    "tab": "crypto",
                    "executed_at": created_raw,
                    "symbol": sym,
                    "market": "KRW",
                    "side": side_kr,
                    "quantity": round(vol, 8),
                    "unit_price": round(price, 1),
                    "trade_amount": round(amount, 0),
                    "fee": round(paid_fee, 2),
                    "settlement": round(amount - paid_fee if side_kr == "sell" else amount + paid_fee, 0),
                    "order_id": o.get("uuid", ""),
                    "slippage_bps": None,
                    "source": "upbit_api",
                })
    except Exception as _e:
        import logging
        logging.getLogger(__name__).debug("upbit orders API 실패, 로컬 폴백: %s", _e)

        # ── 2차 폴백: 로컬 audit 파일 ──────────────────────────────
        slip_map = _build_slip_map()
        patterns = [
            str(_OUTPUT_DIR / "crypto_telegram_approval_audit_*.json"),
        ]
        audit_files: list[str] = []
        for pat in patterns:
            audit_files.extend(_glob.glob(pat))
        audit_files.sort()

        for fpath in audit_files:
            try:
                raw = json.loads(Path(fpath).read_text(encoding="utf-8"))
            except Exception:
                continue
            if not raw.get("executed", False):
                continue
            plan = raw.get("plan") or {}
            if not plan.get("market"):
                continue
            created_raw = plan.get("created_at") or ""
            ts_date = created_raw[:10]
            if ts_date < start_dt or ts_date > end_dt:
                continue

            mkt  = plan.get("market") or ""
            sym  = mkt.replace("KRW-", "")
            side = str(plan.get("side") or "").lower()
            if symbol and symbol.upper() not in (mkt.upper(), sym.upper()):
                continue
            if type_filter != "all" and side != type_filter:
                continue

            price    = float(plan.get("limit_price") or 0)
            amount   = float(plan.get("krw_amount") or 0)
            slip_key = (mkt, side, round(price, 1))
            slip     = slip_map.get(slip_key) or {}
            fill_p   = float(slip.get("fill_price") or price)
            slip_bps = slip.get("slippage_bps")
            fee      = round(amount * 0.0005, 2)

            items.append({
                "tab": "crypto",
                "executed_at": created_raw,
                "symbol": sym,
                "market": "KRW",
                "side": side,
                "quantity": round(amount / price, 8) if price > 0 else 0,
                "unit_price": round(fill_p, 1),
                "trade_amount": round(amount, 0),
                "fee": fee,
                "settlement": round(amount - fee if side == "sell" else amount + fee, 0),
                "order_id": "",
                "slippage_bps": slip_bps,
                "source": "local_audit",
            })

    return items


def _build_slip_map() -> dict:
    """CRYPTO_FILL_SLIPPAGE.jsonl → (market, side, price) 색인."""
    slip_map: dict = {}
    slip_path = _OUTPUT_DIR / "CRYPTO_FILL_SLIPPAGE.jsonl"
    if not slip_path.is_file():
        return slip_map
    for line in slip_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            bps = float(e.get("slippage_bps") or 0)
            if bps > 1000:
                continue
            key = (e.get("market", ""), e.get("side", ""), round(float(e.get("limit_price", 0)), 1))
            slip_map[key] = e
        except Exception:
            pass
    return slip_map


def _fetch_stock_trades(
    start_dt: str, end_dt: str,
    type_filter: str = "all",
    symbol: str = "",
) -> list[dict]:
    """KIS 국내주식 일별 체결 조회 (inquire-daily-ccld)."""
    items: list[dict] = []
    try:
        broker = _make_kis_broker()
        # YYYYMMDD 형식으로 변환
        s = start_dt.replace("-", "")
        e = end_dt.replace("-", "")
        statuses = broker.get_order_status(start_date=s, end_date=e)

        # 슬리피지 기록 모듈
        try:
            from deepsignal.live_trading.execution.kstock_slippage import (
                record_kstock_fill_slippage, compute_slippage_from_kis_row, load_slippage_entries
            )
            ks_dir = _ks_dir()
            slip_fn = "KSTOCK_FILL_SLIPPAGE.jsonl"
            # 이미 기록된 order_id 목록 (중복 방지)
            if ks_dir:
                existing = load_slippage_entries(ks_dir, slip_fn, limit=5000)
                recorded_ids: set[str] = {
                    str(e.get("order_id") or "") for e in existing.get("entries", [])
                    if e.get("order_id")
                }
            else:
                recorded_ids = set()
            slip_available = ks_dir is not None
        except Exception:
            slip_available = False
            recorded_ids = set()
            ks_dir = None

        for st in statuses:
            raw = st.raw or {}
            # matched_row: 이 status에 대응하는 단일 row 사용
            # (response_body.output1 전체를 재순회하면 N² 중복 발생)
            row = raw.get("matched_row") or {}
            if not isinstance(row, dict) or not row:
                continue

            # 체결수량이 0이면 미체결 → 건너뜀
            ccld_qty = float(row.get("tot_ccld_qty") or 0)
            if ccld_qty <= 0:
                continue

            sll_buy = str(row.get("sll_buy_dvsn_cd") or "")
            side = "buy" if sll_buy == "02" else "sell"
            if type_filter != "all" and side != type_filter:
                continue

            sym = str(row.get("pdno") or "").strip()
            name = str(row.get("prdt_name") or sym).strip()
            if symbol and symbol.upper() not in (sym.upper(), name.upper()):
                continue

            ord_dt  = str(row.get("ord_dt") or "")
            ord_tmd = str(row.get("ord_tmd") or "000000")
            # YYYYMMDD → YYYY-MM-DD
            if len(ord_dt) == 8:
                ts = f"{ord_dt[:4]}-{ord_dt[4:6]}-{ord_dt[6:8]}T{ord_tmd[:2]}:{ord_tmd[2:4]}:{ord_tmd[4:6]}+09:00"
            else:
                ts = ""

            avg_price   = float(row.get("avg_prvs") or 0)
            limit_price = float(row.get("ord_unpr") or 0)
            amount      = float(row.get("tot_ccld_amt") or 0) or round(avg_price * ccld_qty, 0)
            # 국내주식 비용:
            #   매수 = 위탁수수료(약 0.015%)
            #   매도 = 위탁수수료(0.015%) + 증권거래세·농어촌특별세(0.18%, 2024 기준)
            _brokerage = amount * 0.00015
            _tax       = amount * 0.0018 if side == "sell" else 0.0
            fee        = round(_brokerage + _tax, 0)
            oid         = str(row.get("odno") or "")

            # 체결오차(슬리피지) 계산 및 기록
            slip_bps: float | None = None
            if slip_available and ks_dir:
                try:
                    slip_bps = compute_slippage_from_kis_row(row)
                    if slip_bps is not None and oid and oid not in recorded_ids:
                        ok = record_kstock_fill_slippage(
                            ks_dir,
                            symbol=sym,
                            side=side,
                            limit_price=limit_price,
                            fill_price=avg_price,
                            order_krw=amount,
                            order_id=oid,
                            market="KRW",
                            filename=slip_fn,
                        )
                        if ok:
                            recorded_ids.add(oid)
                except Exception:
                    pass

            items.append({
                "tab": "stock",
                "executed_at": ts,
                "symbol": sym,
                "name": _get_stock_name(sym),
                "market": "KRW",
                "side": side,
                "quantity": round(ccld_qty, 0),
                "unit_price": round(avg_price, 0),
                "limit_price": round(limit_price, 0) if limit_price > 0 else None,
                "trade_amount": round(amount, 0),
                "fee": fee,
                "settlement": round(amount - fee if side == "sell" else amount + fee, 0),
                "order_id": oid,
                "slippage_bps": slip_bps,
                "source": "kis_api",
            })
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning("KIS 국장 거래내역 조회 실패: %s", _e)
    return items


def _fetch_overseas_trades(
    start_dt: str, end_dt: str,
    type_filter: str = "all",
    symbol: str = "",
) -> list[dict]:
    """KIS 해외주식 체결 조회 (JTTT3001R)."""
    import functools
    items: list[dict] = []
    try:
        from dotenv import load_dotenv
        load_dotenv(str(_ENV_PATH), override=False)
        from deepsignal.live_trading.kis_config import load_kis_config_from_env
        from deepsignal.live_trading.broker.kis_broker import KISBroker
        import requests as _req

        cfg = load_kis_config_from_env(load_dotenv_file=False)
        broker = KISBroker(cfg)

        s = start_dt.replace("-", "")
        e = end_dt.replace("-", "")
        tr = "JTTT3001R"
        path = "/uapi/overseas-stock/v1/trading/inquire-ccnl"
        headers = broker._inquire_headers(tr)
        params: dict = {
            "CANO": cfg.account_no.strip(),
            "ACNT_PRDT_CD": cfg.account_product_code.strip(),
            "PDNO": symbol.upper() if symbol else "",
            "ORD_STRT_DT": s,
            "ORD_END_DT": e,
            "SLL_BUY_DVSN": "00",
            "CCLD_NCCS_DVSN": "01",   # 체결만
            "OVRS_EXCG_CD": "NASD",
            "SORT_SQN": "DS",
            "ORD_DT": "",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "CTX_AREA_NK200": "",
            "CTX_AREA_FK200": "",
        }
        resp = _req.get(
            f"{cfg.base_url}{path}",
            headers=headers, params=params, timeout=10,
        )
        body = resp.json() if resp.status_code == 200 else {}
        output = body.get("output") or []
        if not isinstance(output, list):
            output = []

        # 환율 (간단 캐시)
        try:
            rate_resp = _req.get(
                f"{cfg.base_url}/uapi/overseas-stock/v1/trading/inquire-present-balance",
                headers=broker._inquire_headers("CTRP6548R"),
                params={"CANO": cfg.account_no.strip(), "ACNT_PRDT_CD": cfg.account_product_code.strip(),
                        "OVRS_EXCG_CD": "NASD", "WCRC_FRCR_DVSN_CD": "02", "NATN_CD": "840",
                        "TR_MKET_CD": "01", "INQR_DVSN": "00"},
                timeout=5,
            )
            _raw = float((rate_resp.json().get("output2") or [{}])[0].get("frst_bltn_exrt") or 0)
            usd_rate = _cached_usd_rate(_raw)
        except Exception:
            usd_rate = _cached_usd_rate()

        # 슬리피지 기록 모듈 초기화 (해외)
        try:
            from deepsignal.live_trading.execution.kstock_slippage import (
                record_kstock_fill_slippage, compute_slippage_from_overseas_row, load_slippage_entries
            )
            os_dir = _get_overseas_dir()
            os_slip_fn = "OVERSEAS_FILL_SLIPPAGE.jsonl"
            if os_dir:
                existing_os = load_slippage_entries(os_dir, os_slip_fn, limit=5000)
                os_recorded_ids: set[str] = {
                    str(e.get("order_id") or "") for e in existing_os.get("entries", [])
                    if e.get("order_id")
                }
            else:
                os_recorded_ids = set()
            os_slip_available = os_dir is not None
        except Exception:
            os_slip_available = False
            os_recorded_ids = set()
            os_dir = None

        for row in output:
            ccld_qty = float(row.get("ft_ccld_qty") or 0)
            if ccld_qty <= 0:
                continue
            sll_buy = str(row.get("sll_buy_dvsn_cd") or "")
            side = "buy" if sll_buy == "02" else "sell"
            if type_filter != "all" and side != type_filter:
                continue

            sym  = str(row.get("ovrs_pdno") or "").strip()
            name = str(row.get("ovrs_item_name") or sym).strip()
            if symbol and symbol.upper() not in (sym.upper(), name.upper()):
                continue

            ord_dt = str(row.get("ord_dt") or "")
            ts = f"{ord_dt[:4]}-{ord_dt[4:6]}-{ord_dt[6:8]}" if len(ord_dt) == 8 else ""

            unit_usd   = float(row.get("ft_ccld_unpr3") or 0)
            limit_usd  = float(row.get("ft_ord_unpr3") or 0)
            amt_usd    = float(row.get("ft_ccld_amt3") or 0) or round(unit_usd * ccld_qty, 2)
            fee_usd    = float(row.get("ovrs_excg_fee") or 0)
            unit_krw   = round(unit_usd * usd_rate, 0)
            amt_krw    = round(amt_usd * usd_rate, 0)
            fee_krw    = round(fee_usd * usd_rate, 0)
            oid        = str(row.get("odno") or "")

            # 체결오차(슬리피지) 계산 및 기록
            os_slip_bps: float | None = None
            if os_slip_available and os_dir:
                try:
                    os_slip_bps = compute_slippage_from_overseas_row(row)
                    if os_slip_bps is not None and oid and oid not in os_recorded_ids:
                        ok = record_kstock_fill_slippage(
                            os_dir,
                            symbol=sym,
                            side=side,
                            limit_price=limit_usd,
                            fill_price=unit_usd,
                            order_krw=amt_krw,
                            order_id=oid,
                            market="USD",
                            filename=os_slip_fn,
                        )
                        if ok:
                            os_recorded_ids.add(oid)
                except Exception:
                    pass

            items.append({
                "tab": "overseas",
                "executed_at": ts,
                "symbol": sym,
                "name": _get_overseas_name(sym),
                "market": "USD",
                "side": side,
                "quantity": ccld_qty,
                "unit_price": unit_usd,
                "unit_price_krw": unit_krw,
                "limit_price": round(limit_usd, 4) if limit_usd > 0 else None,
                "trade_amount": amt_usd,
                "trade_amount_krw": amt_krw,
                "fee": fee_usd,
                "fee_krw": fee_krw,
                "settlement": round(amt_usd - fee_usd if side == "sell" else amt_usd + fee_usd, 2),
                "settlement_krw": round(amt_krw - fee_krw if side == "sell" else amt_krw + fee_krw, 0),
                "usd_rate": usd_rate,
                "order_id": oid,
                "slippage_bps": os_slip_bps,
                "source": "kis_overseas_api",
            })
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning("KIS 해외 거래내역 조회 실패: %s", _e)
    return items


@app.get("/api/reconcile")
async def api_reconcile() -> JSONResponse:
    return JSONResponse(_read_json(_OUTPUT_DIR / "LATEST_RECONCILE_STATE.json"))


@app.get("/api/stock/analysis")
async def api_stock_analysis() -> JSONResponse:
    """최신 AI 주식 추천 결과 요약."""
    import glob as _glob

    # 최신 ai_live_trade_recommendation_*.json
    pattern = str(_OUTPUT_DIR / "ai_live_trade_recommendation_*.json")
    files = sorted(_glob.glob(pattern))
    if not files:
        return JSONResponse({"exists": False})
    latest = files[-1]
    try:
        raw = json.loads(Path(latest).read_text(encoding="utf-8"))
    except Exception:
        return JSONResponse({"exists": False})

    acct = raw.get("account_context") or {}
    macro = raw.get("macro_context") or {}
    risk = raw.get("operational_risk_context") or {}
    recs = raw.get("recommendations") or []

    def _rec_row(r: dict) -> dict:
        sb = r.get("score_breakdown") or {}
        return {
            "symbol":        r.get("symbol"),
            "action":        r.get("action"),
            "action_label":  r.get("action_label"),
            "allowed":       r.get("allowed_for_plan", False),
            "confidence":    r.get("confidence"),
            "priority":      r.get("priority"),
            "final_score":   sb.get("final_score"),
            "tech_score":    sb.get("technical_score"),
            "macro_score":   sb.get("macro_score"),
            "limit_price":   r.get("suggested_limit_price"),
            "qty":           r.get("suggested_quantity"),
            "est_value":     r.get("estimated_order_value"),
            "reason":        r.get("reason"),
            "blocked":       r.get("blocked_reasons") or [],
        }

    # 최신 plan JSON에서 order_count 보완
    plan_files = sorted(_glob.glob(str(_OUTPUT_DIR / "ai_daily_trade_plan_*.json")))
    order_count = 0
    plan_status = raw.get("status", "")
    plan_generated_at = raw.get("generated_at", "")
    if plan_files:
        try:
            plan_raw = json.loads(Path(plan_files[-1]).read_text(encoding="utf-8"))
            order_count = plan_raw.get("order_count", 0)
            plan_status = plan_raw.get("status", plan_status)
            plan_generated_at = plan_raw.get("generated_at", plan_generated_at)
        except Exception:
            pass

    return JSONResponse({
        "exists": True,
        "generated_at": plan_generated_at,
        "status": plan_status,
        "recommendation_count": len(recs),
        "order_count": order_count,
        "account": {
            "cash":           acct.get("cash"),
            "total_equity":   acct.get("total_equity"),
            "snapshot_time":  acct.get("snapshot_time"),
            "stale_snapshot": acct.get("stale_snapshot"),
            "positions":      acct.get("positions") or [],
        },
        "macro": {
            "score":   macro.get("macro_score"),
            "regime":  macro.get("market_regime"),
            "reason":  macro.get("reason"),
        },
        "risk": {
            "safety_audit": risk.get("safety_audit_status"),
            "reconcile":    risk.get("reconcile_status"),
            "risk_status":  risk.get("risk_status"),
        },
        "recommendations": [_rec_row(r) for r in recs],
    })


# ── K-GSQS 국내주식 실시간 스트림 ────────────

def _is_kis_stream_process_running() -> bool:
    """kis-stream 프로세스가 실행 중인지 확인 (pgrep 기반)."""
    import subprocess
    try:
        result = subprocess.run(
            ["pgrep", "-f", "kis-stream"],
            capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except Exception:
        return False


def _is_market_hours_now() -> bool:
    """현재 KST 기준 장 운영 시간(평일 09:05~15:15)인지 확인."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    if now.weekday() >= 5:  # 토(5)·일(6) 제외
        return False
    t = (now.hour, now.minute)
    return (9, 5) <= t <= (15, 15)


def _get_kstock_stream() -> dict:
    """kis_stream 출력 파일로 K-GSQS 실시간 상태 계산."""
    import time as _time

    market_hours = _is_market_hours_now()

    # 프로세스 실행 여부 먼저 확인 (파일 유무와 별개)
    process_running = _is_kis_stream_process_running()

    ks_dir = _OUTPUT_DIR.parent / "output" / "kis_stream"
    if not ks_dir.exists():
        # 기본 output 경로도 시도
        ks_dir2 = _OUTPUT_DIR / "kis_stream"
        if ks_dir2.exists():
            ks_dir = ks_dir2
        else:
            return {"running": process_running, "market_hours": market_hours, "scores": [], "signal_count": 0}

    bars_dir = ks_dir / "bars"
    ticks_dir = ks_dir / "ticks"
    if not bars_dir.exists():
        return {"running": process_running, "market_hours": market_hours, "scores": [], "signal_count": 0}

    # 파일 수정 시간 기반 실행 여부 (30분 이내 = 최근 활동)
    try:
        bar_files = list(bars_dir.glob("*_1m.jsonl"))
        if not bar_files:
            return {"running": process_running, "market_hours": market_hours, "scores": [], "signal_count": 0}
        latest_mtime = max(f.stat().st_mtime for f in bar_files)
        file_active = (_time.time() - latest_mtime) < 1800
        running = process_running or file_active
    except Exception:
        running = process_running

    # 심볼별 K-GSQS 스코어 계산
    from deepsignal.market_data.kis_stream.feature_engine import StockFeatureEngine
    from deepsignal.market_data.kis_stream.models import KisOhlcvBar, KisTradeTick
    from deepsignal.scoring.kstock_scorer import compute_kgsqs, THRESHOLD_NOTIFY, THRESHOLD_AUTO

    eng = StockFeatureEngine()
    scores = []

    for bar_file in sorted(bar_files):
        sym = bar_file.name.replace("_1m.jsonl", "")
        try:
            lines = bar_file.read_text(encoding="utf-8").strip().splitlines()
            if not lines:
                continue
            # 최근 30개 봉 로드
            recent_lines = lines[-30:]
            for line in recent_lines:
                row = json.loads(line)
                bar = KisOhlcvBar.from_dict(row)
                eng.on_bar(bar)

            # 최근 틱
            tick_file = ticks_dir / f"{sym}_recent.jsonl"
            if tick_file.exists():
                tick_lines = tick_file.read_text(encoding="utf-8").strip().splitlines()
                if tick_lines:
                    t = json.loads(tick_lines[-1])
                    tick = KisTradeTick(
                        symbol=sym,
                        price=int(t.get("price", 0)),
                        qty=int(t.get("qty", 0)),
                        ts_ms=int(t.get("ts_ms", 0)),
                        is_buyer=bool(t.get("is_buyer", False)),
                        ask_price=int(t.get("ask_price", 0)),
                        bid_price=int(t.get("bid_price", 0)),
                        strength=float(t.get("strength", 100.0)),
                    )
                    eng.on_tick(tick)

            features = eng.build_features(sym)
            if features is None:
                continue
            signal = compute_kgsqs(features)
            scores.append({
                "symbol": sym,
                "name": _get_stock_name(sym),
                "price": features.price,
                "total_score": signal.total_score,
                "action": signal.action,
                "hard_blocked": signal.hard_blocked,
                "blocked_reason": signal.blocked_reason,
                "sub_scores": signal.sub_scores,
                "features": {
                    "ret_1m": round(features.ret_1m, 3),
                    "ret_5m": round(features.ret_5m, 3),
                    "vol_ratio_5m": round(features.vol_ratio_5m, 2),
                    "buy_ratio_5m": round(features.buy_ratio_5m, 2),
                    "bid_ask_ratio": round(features.bid_ask_ratio, 2),
                    "spread_bps": round(features.spread_bps, 1),
                    "strength": round(features.strength, 1),
                    "atr_pct": round(features.atr_pct, 3),
                },
            })
        except Exception:
            pass

    # 총점 내림차순 정렬
    scores.sort(key=lambda x: x["total_score"], reverse=True)

    # signal_log 통계
    signal_count = 0
    win_rate = None
    sig_log = ks_dir / "kstock" / "signal_log.jsonl"
    if sig_log.exists():
        try:
            sig_lines = sig_log.read_text(encoding="utf-8").strip().splitlines()
            signal_count = len(sig_lines)
            parsed = [json.loads(l) for l in sig_lines[-50:]]
            wins = sum(1 for p in parsed if (p.get("ret_5m") or 0) > 0)
            complete = sum(1 for p in parsed if p.get("outcome_complete", False))
            if complete > 0:
                win_rate = round(wins / complete * 100, 1)
        except Exception:
            pass

    return {
        "running": running,
        "market_hours": market_hours,
        "scores": scores[:20],  # 최대 20개
        "signal_count": signal_count,
        "win_rate": win_rate,
        "threshold_notify": THRESHOLD_NOTIFY,
        "threshold_auto": THRESHOLD_AUTO,
        "data_dir": str(ks_dir),
    }


@app.get("/api/kstock/stream")
async def api_kstock_stream() -> JSONResponse:
    """K-GSQS 실시간 스트림 현황."""
    return JSONResponse(await asyncio.to_thread(_get_kstock_stream))


def _ks_dir() -> Path | None:
    """KIS 스트림 출력 디렉토리 경로 반환 (없으면 None)."""
    p1 = _OUTPUT_DIR.parent / "output" / "kis_stream"
    if p1.exists():
        return p1
    p2 = _OUTPUT_DIR / "kis_stream"
    return p2 if p2.exists() else None


def _get_kstock_signals() -> dict:
    """K-GSQS 신호 이력 & 다중 시간대 승률."""
    ks = _ks_dir()
    if ks is None:
        return {"total_signals": 0, "recent": [], "win_rates": {}, "symbol_stats": []}

    sig_log = ks / "kstock" / "signal_log.jsonl"
    if not sig_log.exists():
        return {"total_signals": 0, "recent": [], "win_rates": {}, "symbol_stats": []}

    try:
        lines = sig_log.read_text(encoding="utf-8").strip().splitlines()
    except Exception:
        return {"total_signals": 0, "recent": [], "win_rates": {}, "symbol_stats": []}

    records: list[dict] = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            pass

    # 최근 20개 신호 (역순)
    recent = []
    for r in reversed(records):
        sym = r.get("symbol") or ""
        recent.append({
            "signal_id":        r.get("signal_id"),
            "symbol":           sym,
            "name":             _get_stock_name(sym) if sym else "",
            "score":            r.get("score"),
            "decision":         r.get("decision"),
            "ts_ms":            r.get("ts_ms"),
            "entry_price":      r.get("entry_price"),
            "ret_1m":           r.get("ret_1m"),
            "ret_3m":           r.get("ret_3m"),
            "ret_5m":           r.get("ret_5m"),
            "ret_15m":          r.get("ret_15m"),
            "outcome_complete": r.get("outcome_complete", False),
        })
        if len(recent) >= 20:
            break

    # 최근 50개 완성된 신호로 시간대별 승률
    recent_50 = records[-50:]
    completed = [r for r in recent_50 if r.get("outcome_complete", False)]
    win_rates: dict = {}
    for horizon in ("ret_1m", "ret_3m", "ret_5m", "ret_15m"):
        vals = [r[horizon] for r in completed if r.get(horizon) is not None]
        if vals:
            wins = sum(1 for v in vals if v > 0)
            win_rates[horizon] = {
                "win_rate": round(wins / len(vals) * 100, 1),
                "avg_ret":  round(sum(vals) / len(vals) * 100, 3),
                "count":    len(vals),
            }

    # 종목별 승률 (최근 100개)
    sym_agg: dict[str, dict] = {}
    for r in records[-100:]:
        sym = r.get("symbol")
        if not sym:
            continue
        if sym not in sym_agg:
            sym_agg[sym] = {"count": 0, "wins": 0, "total_ret": 0.0}
        ret = r.get("ret_5m")
        if ret is not None and r.get("outcome_complete"):
            sym_agg[sym]["count"] += 1
            sym_agg[sym]["total_ret"] += ret
            if ret > 0:
                sym_agg[sym]["wins"] += 1

    symbol_stats = [
        {
            "symbol":   sym,
            "name":     _get_stock_name(sym),
            "count":    v["count"],
            "win_rate": round(v["wins"] / v["count"] * 100, 1) if v["count"] > 0 else None,
            "avg_ret":  round(v["total_ret"] / v["count"] * 100, 3) if v["count"] > 0 else None,
        }
        for sym, v in sym_agg.items() if v["count"] >= 2
    ]
    symbol_stats.sort(key=lambda x: x.get("win_rate") or 0, reverse=True)

    return {
        "total_signals":   len(records),
        "completed_count": len(completed),
        "recent":          recent,
        "win_rates":       win_rates,
        "symbol_stats":    symbol_stats[:10],
    }


def _get_kstock_positions() -> dict:
    """최신 KIS 계좌 스냅샷 & 보유 포지션."""
    import glob as _glob
    import time as _time

    pattern = str(_OUTPUT_DIR / "live_account_snapshot_*.json")
    files = sorted(_glob.glob(pattern))
    if not files:
        return {"exists": False}

    try:
        raw = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
    except Exception:
        return {"exists": False}

    cash_block = raw.get("cash") or {}
    if isinstance(cash_block, dict):
        cash = cash_block.get("cash")
        withdrawable = cash_block.get("withdrawable_cash")
        # total_equity from raw output2
        try:
            out2 = cash_block["raw"]["response_body"]["output2"][0]
            total_equity = float(out2.get("tot_evlu_amt", 0))
            total_stock_value = float(out2.get("scts_evlu_amt", 0))
            total_pnl = float(out2.get("evlu_pfls_smtl_amt", 0))
        except Exception:
            total_equity = None
            total_stock_value = None
            total_pnl = None
    else:
        cash = None
        withdrawable = None
        total_equity = None
        total_stock_value = None
        total_pnl = None

    positions = []
    seen_symbols: set[str] = set()
    for p in (raw.get("positions") or []):
        r = p.get("raw") or {}
        pnl_pct = None
        try:
            pnl_pct = float(r.get("evlu_pfls_rt", 0))
        except Exception:
            pass
        sym = p.get("symbol") or ""
        tpsl = _compute_position_tpsl(sym) if sym else None
        positions.append({
            "symbol":        sym,
            "name":          r.get("prdt_name", sym),
            "quantity":      p.get("quantity"),
            "avg_price":     p.get("avg_price"),
            "current_price": p.get("current_price"),
            "market_value":  p.get("market_value"),
            "pnl_pct":       pnl_pct,
            "pnl_amt":       float(r.get("evlu_pfls_amt", 0)) if r.get("evlu_pfls_amt") else None,
            "tpsl":          tpsl,
        })
        if sym:
            seen_symbols.add(str(sym))

    rt = _get_regime_trend_position()
    if rt and rt["symbol"] not in seen_symbols:
        sym = str(rt["symbol"])
        qty = float(rt.get("quantity") or 0)
        avg = float(rt.get("avg_price") or 0)
        cur = float(rt.get("current_price") or 0)
        positions.append({
            "symbol":        sym,
            "name":          rt.get("name", sym),
            "quantity":      rt.get("quantity"),
            "avg_price":     rt.get("avg_price"),
            "current_price": rt.get("current_price"),
            "market_value":  rt.get("market_value"),
            "pnl_pct":       rt.get("pnl_pct"),
            "pnl_amt":       round((cur - avg) * qty, 0) if avg and qty else None,
            "tpsl":          _compute_position_tpsl(sym),
            "_from_state":   True,
        })

    snap_time = raw.get("timestamp", "")
    age_min = None
    if snap_time:
        try:
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(snap_time.replace("Z", "+00:00"))
            age_min = round((datetime.now(timezone.utc) - ts).total_seconds() / 60, 1)
        except Exception:
            pass

    return {
        "exists":           True,
        "cash":             cash,
        "withdrawable":     withdrawable,
        "total_equity":     total_equity,
        "total_stock_value":total_stock_value,
        "total_pnl":        total_pnl,
        "positions":        positions,
        "snapshot_time":    snap_time,
        "snapshot_age_min": age_min,
        "stale":            age_min is not None and age_min > 120,
        "kis_env":          raw.get("kis_env", "paper"),
    }


_KSTOCK_NAME_CACHE: dict[str, str] = {}


# 미국 주요 종목/ETF → 한글명
_US_NAME_KR = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "NVDA": "엔비디아", "GOOGL": "알파벳",
    "GOOG": "알파벳", "AMZN": "아마존", "META": "메타", "TSLA": "테슬라", "AMD": "AMD",
    "AVGO": "브로드컴", "NFLX": "넷플릭스", "ADBE": "어도비", "CRM": "세일즈포스",
    "ORCL": "오라클", "INTC": "인텔", "QCOM": "퀄컴", "CSCO": "시스코", "TXN": "텍사스인스트루먼트",
    "AMAT": "어플라이드머티어리얼즈", "MU": "마이크론", "PYPL": "페이팔",
    "SPY": "S&P500 ETF", "QQQ": "나스닥100 ETF", "IWM": "러셀2000 ETF", "DIA": "다우 ETF",
    "XLK": "기술섹터 ETF", "XLF": "금융섹터 ETF", "XLE": "에너지섹터 ETF", "XLV": "헬스케어 ETF",
    "GLD": "금 ETF", "TLT": "장기국채 ETF", "SOXL": "반도체3X ETF",
}


def _get_overseas_name(symbol: str) -> str:
    """미국 티커 → 한글명 (없으면 티커 그대로)."""
    sym = (symbol or "").split(":")[-1].strip().upper()
    return _US_NAME_KR.get(sym, sym)


def _get_stock_name(symbol: str) -> str:
    """종목코드 → 종목명 조회.

    1차: pykrx (일반 주식)
    2차: KIS API (ETF 등 pykrx 미지원 종목)
    3차: 폴백 — 코드 그대로 반환
    """
    if symbol in _KSTOCK_NAME_CACHE:
        return _KSTOCK_NAME_CACHE[symbol]

    name = ""
    # 1차: pykrx
    try:
        from pykrx import stock as _pykrx
        n = _pykrx.get_market_ticker_name(symbol)
        if n and str(n).strip() and str(n).strip() != symbol:
            name = str(n).strip()
    except Exception:
        pass

    # 2차: pykrx 실패 시 KIS API (CTPF1002R — 상품 기본 정보)
    if not name:
        try:
            from deepsignal.live_trading.broker.kis_broker import KISBroker
            from deepsignal.live_trading.broker.kis_config import load_kis_config_from_env
            cfg = load_kis_config_from_env()
            br = KISBroker(cfg, safe_mode=True)
            token = br.get_access_token()
            import requests as _req
            resp = _req.get(
                f"{cfg.base_url}/uapi/domestic-stock/v1/quotations/search-info",
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey":    cfg.app_key,
                    "appsecret": cfg.app_secret,
                    "tr_id":     "CTPF1002R",
                },
                params={"PDNO": symbol, "PRDT_TYPE_CD": "300"},
                timeout=5,
            )
            out = resp.json().get("output", {})
            # 약어명 우선, 없으면 전체명
            n = out.get("prdt_abrv_name") or out.get("prdt_name") or ""
            if n.strip() and n.strip() != symbol:
                name = n.strip()
        except Exception:
            pass

    name = name or symbol
    _KSTOCK_NAME_CACHE[symbol] = name
    return name


def _detect_asset_class(symbol: str) -> str:
    """심볼 패턴으로 자산 클래스 자동 감지.
    6자리 숫자('005930') → kis_stock, 나머지(NVDA, NASD:NVDA) → kis_overseas.
    """
    clean = symbol.split(":")[-1]
    return "kis_stock" if clean.isdigit() else "kis_overseas"


def _compute_position_tpsl(symbol: str) -> dict | None:
    """보유 포지션의 동적 TP/SL 계산 — web UI 표시용."""
    try:
        from deepsignal.risk.dynamic_tpsl import compute_dynamic_tpsl, load_bars_for_symbol
        asset_class = _detect_asset_class(symbol)
        bars, tf_min = load_bars_for_symbol(symbol, asset_class, _PROJECT_ROOT)
        result = compute_dynamic_tpsl(symbol, asset_class, bars or None, timeframe_min=tf_min)
        return {
            "tp_pct":      round(result.tp_pct * 100, 2),
            "sl_pct":      round(result.sl_pct * 100, 2),
            "atr_pct":     round(result.atr_pct, 2),
            "grade":       result.grade.value,
            "market_state": result.market_state.value,
            "blocked":     result.blocked,
        }
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).debug("포지션 TP/SL 계산 실패 (%s): %s", symbol, exc)
        return None


def _get_kstock_universe() -> dict:
    """감시 종목 목록 + 장 운영 상태."""
    import time as _time
    from zoneinfo import ZoneInfo
    from datetime import datetime

    KST = ZoneInfo("Asia/Seoul")
    now_kst = datetime.now(KST)
    t = (now_kst.hour, now_kst.minute)
    is_market_hours = (9, 5) <= t <= (15, 15)
    is_trading_day = now_kst.weekday() < 5  # Mon–Fri

    ks = _ks_dir()
    bars_dir = (ks / "bars") if ks else None

    symbols = []
    if bars_dir and bars_dir.exists():
        for f in sorted(bars_dir.glob("*_1m.jsonl")):
            # macOS 리소스 포크 파일(._로 시작) 제외
            if f.name.startswith("._"):
                continue
            sym = f.name.replace("_1m.jsonl", "")
            info: dict = {"symbol": sym, "name": _get_stock_name(sym)}
            try:
                lines = f.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    last = json.loads(lines[-1])
                    bar_ts = last.get("open_ts_ms", 0) / 1000
                    age_s = _time.time() - bar_ts - 60
                    info["last_close"] = last.get("close")
                    info["last_volume"] = last.get("volume")
                    info["bar_count"] = len(lines)
                    info["bar_age_min"] = round(max(0.0, age_s) / 60, 1)
            except Exception:
                pass
            symbols.append(info)

    if not symbols:
        try:
            from deepsignal.market_data.kis_stream.config import DEFAULT_SYMBOLS
            symbols = [{"symbol": s, "name": _get_stock_name(s)} for s in DEFAULT_SYMBOLS]
        except Exception:
            pass

    # auto_universe 설정 읽기
    auto_universe = True
    universe_size = 30
    try:
        from deepsignal.market_data.kis_stream.config import load_kis_stream_config_from_env
        _cfg = load_kis_stream_config_from_env(load_dotenv_file=False)
        auto_universe = _cfg.auto_universe
        universe_size = _cfg.universe_size
    except Exception:
        pass

    return {
        "symbols":         symbols,
        "symbol_count":    len(symbols),
        "market_status":   "open" if (is_market_hours and is_trading_day) else "closed",
        "is_trading_day":  is_trading_day,
        "current_time_kst":now_kst.strftime("%H:%M"),
        "market_open_at":  "09:05",
        "market_close_at": "15:15",
        "auto_universe":   auto_universe,
        "universe_size":   universe_size,
        "refresh_interval_min": 30,
    }


@app.get("/api/kstock/signals")
async def api_kstock_signals() -> JSONResponse:
    """K-GSQS 신호 이력 & 다중 시간대 승률."""
    return JSONResponse(await asyncio.to_thread(_get_kstock_signals))


@app.get("/api/kstock/positions")
async def api_kstock_positions() -> JSONResponse:
    """KIS 계좌 스냅샷 & 보유 포지션."""
    return JSONResponse(await asyncio.to_thread(_get_kstock_positions))


@app.get("/api/kstock/universe")
async def api_kstock_universe() -> JSONResponse:
    """KIS 감시 종목 목록 & 장 상태."""
    return JSONResponse(await asyncio.to_thread(_get_kstock_universe))


def _get_kstock_weight_optimizer() -> dict:
    """K-GSQS 국장 가중치 최적화 상태."""
    try:
        from deepsignal.scoring.kstock_weight_optimizer import KStockWeightOptimizer
        ks = _ks_dir()
        if ks is None:
            return {"error": "출력 디렉토리 없음", "n_complete_signals": 0}
        opt = KStockWeightOptimizer(ks, asset_label="국장")
        return opt.status()
    except Exception as exc:
        return {"error": str(exc), "n_complete_signals": 0}


@app.get("/api/kstock/weight_optimizer")
async def api_kstock_weight_optimizer() -> JSONResponse:
    """K-GSQS 국장 가중치 최적화 상태."""
    return JSONResponse(await asyncio.to_thread(_get_kstock_weight_optimizer))


def _get_overseas_weight_optimizer() -> dict:
    """K-GSQS 해외주식 가중치 최적화 상태."""
    try:
        from deepsignal.scoring.kstock_weight_optimizer import KStockWeightOptimizer
        os_dir = _get_overseas_dir()
        if not os_dir:
            return {"error": "해외주식 출력 디렉토리 없음", "n_complete_signals": 0}
        opt = KStockWeightOptimizer(os_dir, asset_label="해외")
        return opt.status()
    except Exception as exc:
        return {"error": str(exc), "n_complete_signals": 0}


@app.get("/api/overseas/weight_optimizer")
async def api_overseas_weight_optimizer() -> JSONResponse:
    """K-GSQS 해외주식 가중치 최적화 상태."""
    return JSONResponse(await asyncio.to_thread(_get_overseas_weight_optimizer))


def _get_kstock_slippage(limit: int = 100) -> dict:
    """국내주식 체결오차 JSONL 로드."""
    try:
        from deepsignal.live_trading.execution.kstock_slippage import load_slippage_entries
        ks = _ks_dir()
        if ks is None:
            return {"entries": [], "exists": False, "total": 0}
        return load_slippage_entries(ks, "KSTOCK_FILL_SLIPPAGE.jsonl", limit=limit)
    except Exception as exc:
        return {"entries": [], "exists": False, "error": str(exc)}


@app.get("/api/kstock/slippage")
async def api_kstock_slippage(limit: int = 100) -> JSONResponse:
    """국내주식 체결오차 상세기록."""
    return JSONResponse(await asyncio.to_thread(_get_kstock_slippage, limit))


def _get_overseas_slippage(limit: int = 100) -> dict:
    """해외주식 체결오차 JSONL 로드."""
    try:
        from deepsignal.live_trading.execution.kstock_slippage import load_slippage_entries
        os_dir = _get_overseas_dir()
        if not os_dir:
            return {"entries": [], "exists": False, "total": 0}
        return load_slippage_entries(os_dir, "OVERSEAS_FILL_SLIPPAGE.jsonl", limit=limit)
    except Exception as exc:
        return {"entries": [], "exists": False, "error": str(exc)}


@app.get("/api/overseas/slippage")
async def api_overseas_slippage(limit: int = 100) -> JSONResponse:
    """해외주식 체결오차 상세기록."""
    return JSONResponse(await asyncio.to_thread(_get_overseas_slippage, limit))


# USD/KRW 환율 캐시 (10분 TTL + 마지막 성공값 폴백)
_usd_rate_cache: dict[str, float] = {"rate": 1350.0, "ts": 0.0}

def _cached_usd_rate(fresh_rate: float | None = None) -> float:
    """USD/KRW 환율. fresh_rate가 유효하면 캐시 갱신, 아니면 캐시값 반환.

    - 조회 성공값(fresh_rate>1000)이 오면 캐시 갱신
    - 실패(None/0/비정상) 시 마지막 성공값 반환 (1300 하드코딩 대신)
    """
    import time as _t
    if fresh_rate and fresh_rate > 1000:
        _usd_rate_cache["rate"] = float(fresh_rate)
        _usd_rate_cache["ts"] = _t.time()
    return _usd_rate_cache["rate"]


def _get_overseas_positions() -> dict:
    """KIS 해외주식 보유 포지션 조회 (JTTT3012R).

    거래소별 (NASD/NYSE/AMEX/TKSE/SEHK) 포지션을 합산하고
    원화 환산 수익/잔고를 계산합니다.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(str(_ENV_PATH), override=False)
        from deepsignal.live_trading.kis_config import load_kis_config_from_env
        from deepsignal.live_trading.broker.kis_broker import KISBroker
        import requests as _req

        cfg = load_kis_config_from_env(load_dotenv_file=False)
        broker = KISBroker(cfg)

        # 환율 조회
        try:
            rate_resp = _req.get(
                f"{cfg.base_url}/uapi/overseas-stock/v1/trading/inquire-present-balance",
                headers=broker._inquire_headers("CTRP6548R"),
                params={
                    "CANO": cfg.account_no.strip(),
                    "ACNT_PRDT_CD": cfg.account_product_code.strip(),
                    "OVRS_EXCG_CD": "NASD",
                    "WCRC_FRCR_DVSN_CD": "02",
                    "NATN_CD": "840",
                    "TR_MKET_CD": "01",
                    "INQR_DVSN": "00",
                },
                timeout=8,
            )
            rate_body = rate_resp.json() if rate_resp.status_code == 200 else {}
            _raw_rate = float((rate_body.get("output2") or [{}])[0].get("frst_bltn_exrt") or 0)
            usd_rate = _cached_usd_rate(_raw_rate)   # 성공 시 갱신, 실패 시 마지막값
        except Exception:
            usd_rate = _cached_usd_rate()            # 마지막 성공값 폴백

        # inquire-present-balance (CTRP6548R) → output1=보유종목, output2=환율·계좌요약
        # 거래소별로 조회하여 모든 보유 종목을 수집
        path = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        # 실전: CTRP6548R / 모의: VTRP6548R
        tr = "VTRP6548R" if not cfg.is_live else "CTRP6548R"

        all_positions: list[dict] = []
        seen_symbols: set[str] = set()
        total_usd_value = 0.0
        total_usd_pnl = 0.0
        cash_usd = 0.0

        # 미국 3대 거래소 순회 + 전체 조회 (OVRS_EXCG_CD 공백)
        exchanges_to_try = ["NASD", "NYSE", "AMEX", ""]
        for excg in exchanges_to_try:
            try:
                params: dict = {
                    "CANO": cfg.account_no.strip(),
                    "ACNT_PRDT_CD": cfg.account_product_code.strip(),
                    "OVRS_EXCG_CD": excg,
                    "WCRC_FRCR_DVSN_CD": "02",
                    "NATN_CD": "840",
                    "TR_MKET_CD": "01",
                    "INQR_DVSN": "00",
                }
                resp = _req.get(
                    f"{cfg.base_url}{path}",
                    headers=broker._inquire_headers(tr),
                    params=params,
                    timeout=8,
                )
                body = resp.json() if resp.status_code == 200 else {}
            except Exception:
                continue

            if str(body.get("rt_cd", "")).strip() != "0":
                continue

            # output1: 보유 종목 목록
            out1 = body.get("output1") or []
            if not isinstance(out1, list):
                out1 = []

            for row in out1:
                sym = str(row.get("ovrs_pdno") or "").strip()
                if not sym or sym in seen_symbols:
                    continue
                qty = float(row.get("ovrs_cblc_qty") or 0)
                if qty <= 0:
                    continue
                seen_symbols.add(sym)

                avg_price  = float(row.get("pchs_avg_pric") or 0)
                cur_price  = float(row.get("now_pric2") or 0)
                evlu_amt   = float(row.get("frcr_evlu_pfls_amt") or 0)  # USD 평가손익
                evlu_value = float(row.get("ovrs_stck_evlu_amt") or 0) or round(cur_price * qty, 2)
                pnl_pct    = float(row.get("evlu_pfls_rt") or 0)
                row_excg   = str(row.get("ovrs_excg_cd") or excg).strip() or excg

                # 원화 환산
                evlu_krw  = round(evlu_value * usd_rate, 0)
                pnl_krw   = round(evlu_amt * usd_rate, 0)
                avg_krw   = round(avg_price * usd_rate, 0)

                total_usd_value += evlu_value
                total_usd_pnl   += evlu_amt

                all_positions.append({
                    "symbol":         sym,
                    "exchange":       row_excg,
                    "name":           str(row.get("ovrs_item_name") or sym),
                    "quantity":       qty,
                    "avg_price":      avg_price,
                    "avg_price_krw":  avg_krw,
                    "current_price":  cur_price,
                    "market_value":   evlu_value,
                    "market_value_krw": evlu_krw,
                    "pnl_usd":        evlu_amt,
                    "pnl_krw":        pnl_krw,
                    "pnl_pct":        pnl_pct,
                })

            # output2: 계좌 요약 (첫 조회에서만 집계)
            if not cash_usd:
                out2 = body.get("output2") or [{}]
                if isinstance(out2, list) and out2:
                    s = out2[0]
                    try:
                        cash_usd = float(s.get("frcr_dncl_amt_2") or 0)
                        # 환율 업데이트
                        r = float(s.get("frst_bltn_exrt") or 0)
                        if r > 0:
                            usd_rate = r
                    except Exception:
                        pass

        return {
            "exists":               True,
            "positions":            all_positions,
            "total_usd_value":      round(total_usd_value, 2),
            "total_usd_pnl":        round(total_usd_pnl, 2),
            "total_krw_value":      round(total_usd_value * usd_rate, 0),
            "total_krw_pnl":        round(total_usd_pnl * usd_rate, 0),
            "cash_usd":             round(cash_usd, 2),
            "cash_krw":             round(cash_usd * usd_rate, 0),
            "usd_rate":             usd_rate,
            "kis_env":              "live" if cfg.is_live else "paper",
        }
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("해외주식 포지션 조회 실패: %s", exc)
        return {"exists": False, "error": str(exc), "positions": []}


@app.get("/api/overseas/positions")
async def api_overseas_positions() -> JSONResponse:
    """KIS 해외주식 보유 포지션."""
    return JSONResponse(await asyncio.to_thread(_get_overseas_positions))


def _get_kstock_sizing() -> dict:
    """K-GSQS 기반 국내주식 동적 포지션 사이징."""
    try:
        from deepsignal.live_trading.execution.kstock_sizing import compute_kstock_sizing, sizing_to_dict

        # 계좌 잔고
        pos_data = _get_kstock_positions()
        available_cash = float(pos_data.get("withdrawable") or pos_data.get("cash") or 0)
        total_equity   = float(pos_data.get("total_equity") or available_cash)
        kis_env        = pos_data.get("kis_env", "paper")

        # K-GSQS 실시간 스코어
        stream_data = _get_kstock_stream()
        scores = stream_data.get("scores") or []

        result = compute_kstock_sizing(
            available_cash=available_cash,
            total_equity=total_equity,
            scores=scores,
            asset_class="kis_stock",
            asset_label="국내주식",
            kis_env=kis_env,
            project_root=_PROJECT_ROOT,
        )
        return sizing_to_dict(result)
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning("국내주식 사이징 계산 실패: %s", exc)
        return {"error": str(exc), "recommendations": []}


@app.get("/api/kstock/sizing")
async def api_kstock_sizing() -> JSONResponse:
    """K-GSQS 국내주식 동적 포지션 사이징."""
    return JSONResponse(await asyncio.to_thread(_get_kstock_sizing))


def _get_overseas_sizing() -> dict:
    """K-GSQS 기반 해외주식 동적 포지션 사이징."""
    try:
        from deepsignal.live_trading.execution.kstock_sizing import compute_kstock_sizing, sizing_to_dict

        # 해외 계좌 잔고
        os_pos = _get_overseas_positions()
        usd_rate       = float(os_pos.get("usd_rate") or 1300)
        cash_usd       = float(os_pos.get("cash_usd") or 0)
        total_usd      = float(os_pos.get("total_usd_value") or 0) + cash_usd
        available_cash = cash_usd * usd_rate   # 원화 환산
        total_equity   = total_usd * usd_rate
        kis_env        = os_pos.get("kis_env", "paper")

        # 해외 K-GSQS 실시간 스코어 → USD 가격을 KRW로 환산
        stream_data = _get_overseas_stream()
        scores_raw = stream_data.get("scores") or []
        scores = []
        for s in scores_raw:
            s2 = dict(s)
            price_usd = float(s2.get("price") or 0)
            s2["price"] = price_usd * usd_rate  # KRW 환산
            scores.append(s2)

        result = compute_kstock_sizing(
            available_cash=available_cash,
            total_equity=total_equity,
            scores=scores,
            asset_class="kis_overseas",
            asset_label="해외주식",
            kis_env=kis_env,
            project_root=_PROJECT_ROOT,
        )
        d = sizing_to_dict(result)
        d["usd_rate"] = usd_rate
        return d
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning("해외주식 사이징 계산 실패: %s", exc)
        return {"error": str(exc), "recommendations": []}


@app.get("/api/overseas/sizing")
async def api_overseas_sizing() -> JSONResponse:
    """K-GSQS 해외주식 동적 포지션 사이징."""
    return JSONResponse(await asyncio.to_thread(_get_overseas_sizing))


def _get_kstock_reconcile() -> dict:
    """국내주식 계좌 대사 (저장 스냅샷 vs KIS 실시간 조회 비교).

    비교 결과:
      matched          - 종목코드·수량이 일치하는 종목 목록
      missing_in_snap  - 브로커에 있으나 저장 스냅샷에 없는 종목
      missing_in_broker - 저장 스냅샷에 있으나 브로커 실시간에 없는 종목
      quantity_mismatch - 수량 불일치 종목
      stale            - 스냅샷이 오래된지 여부 (2시간 초과)
    """
    import time as _time
    result: dict = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "asset": "국내주식",
        "success": False,
        "matched": [],
        "missing_in_snap": [],
        "missing_in_broker": [],
        "quantity_mismatch": [],
        "warnings": [],
        "snap_age_min": None,
        "stale": False,
        "broker_position_count": 0,
        "snap_position_count": 0,
    }
    try:
        # 1. 저장된 스냅샷에서 포지션 읽기
        snap_data = _get_kstock_positions()
        snap_positions = snap_data.get("positions") or []
        snap_age_min = snap_data.get("snapshot_age_min")
        stale = snap_data.get("stale", False)
        result["snap_age_min"] = snap_age_min
        result["stale"] = stale
        result["snap_position_count"] = len(snap_positions)

        if stale:
            result["warnings"].append(f"경고: 스냅샷이 오래됨 (약 {snap_age_min}분 전)")

        snap_map: dict[str, int] = {
            str(p.get("symbol") or "").zfill(6): int(p.get("quantity") or 0)
            for p in snap_positions
            if p.get("symbol") and int(p.get("quantity") or 0) > 0
        }

        # 2. KIS 실시간 잔고 조회
        broker = _make_kis_broker()
        live_positions = broker.get_positions()
        result["broker_position_count"] = len(live_positions)

        broker_map: dict[str, int] = {
            str(p.symbol).zfill(6): int(p.quantity or 0)
            for p in live_positions
            if p.symbol and int(p.quantity or 0) > 0
        }

        # 3. 비교
        from deepsignal.live_trading.execution.reconcile import reconcile_real_account

        rec = reconcile_real_account(
            broker_positions=[{"symbol": k, "quantity": v} for k, v in broker_map.items()],
            db_positions=[{"symbol": k, "quantity": v} for k, v in snap_map.items()],
        )
        result["success"] = rec.success
        result["matched"] = rec.matched
        result["missing_in_snap"] = [
            {"symbol": i.symbol, "broker_qty": i.broker_quantity, "message": i.message}
            for i in rec.missing_in_db
        ]
        result["missing_in_broker"] = [
            {"symbol": i.symbol, "snap_qty": i.db_quantity, "message": i.message}
            for i in rec.missing_in_broker
        ]
        result["quantity_mismatch"] = [
            {"symbol": i.symbol, "broker_qty": i.broker_quantity, "snap_qty": i.db_quantity, "message": i.message}
            for i in rec.quantity_mismatch
        ]
        result["warnings"].extend(rec.warnings)

        # 4. 스냅샷 저장
        try:
            from datetime import datetime as _dt
            rec_path = _OUTPUT_DIR / "KSTOCK_RECONCILE_STATE.json"
            rec_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    except Exception as exc:
        result["error"] = str(exc)
        result["warnings"].append(f"리콘사일 실행 오류: {exc}")
    return result


@app.get("/api/kstock/reconcile")
async def api_kstock_reconcile() -> JSONResponse:
    """국내주식 계좌 대사 (실시간 KIS vs 스냅샷)."""
    return JSONResponse(await asyncio.to_thread(_get_kstock_reconcile))


def _get_overseas_reconcile() -> dict:
    """해외주식 계좌 대사 (CTRP6548R 실시간 vs 같은 API 단순 일관성 검사).

    해외는 별도 스냅샷 파일이 없으므로 실시간 API를 두 번 호출 비교합니다.
    거래소별 잔고 합계 vs 전체 조회 결과를 비교하여 데이터 일관성을 검증합니다.
    """
    result: dict = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "asset": "해외주식",
        "success": False,
        "matched": [],
        "missing_in_snap": [],
        "missing_in_broker": [],
        "quantity_mismatch": [],
        "warnings": [],
        "broker_position_count": 0,
        "usd_rate": 1300.0,
    }
    try:
        # 실시간 조회 (overseas positions)
        os_data = _get_overseas_positions()
        live_positions = os_data.get("positions") or []
        result["broker_position_count"] = len(live_positions)
        result["usd_rate"] = os_data.get("usd_rate", 1300.0)
        result["kis_env"] = os_data.get("kis_env", "paper")

        if not os_data.get("exists"):
            result["warnings"].append("해외 계좌 조회 실패 또는 KIS 설정 없음")
            return result

        # 포지션 일관성 검사 (각 포지션의 qty > 0, price > 0)
        matched = []
        warnings = []
        for p in live_positions:
            sym = p.get("symbol", "")
            qty = float(p.get("quantity") or 0)
            avg = float(p.get("avg_price") or 0)
            cur = float(p.get("current_price") or 0)
            if qty <= 0:
                warnings.append(f"{sym}: 수량 0 또는 음수 ({qty})")
            elif avg <= 0:
                warnings.append(f"{sym}: 평균단가 0 (데이터 오류 가능)")
            else:
                matched.append(sym)

        result["success"] = len(warnings) == 0
        result["matched"] = matched
        result["warnings"].extend(warnings)

        # 수익/손실 요약 추가
        result["total_usd_value"] = os_data.get("total_usd_value", 0)
        result["total_usd_pnl"]   = os_data.get("total_usd_pnl", 0)
        result["cash_usd"]        = os_data.get("cash_usd", 0)

        # 저장
        try:
            os_dir = _get_overseas_dir()
            if os_dir:
                rec_path = os_dir / "OVERSEAS_RECONCILE_STATE.json"
                rec_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        except Exception:
            pass

    except Exception as exc:
        result["error"] = str(exc)
        result["warnings"].append(f"리콘사일 실행 오류: {exc}")
    return result


@app.get("/api/overseas/reconcile")
async def api_overseas_reconcile() -> JSONResponse:
    """해외주식 계좌 대사 (실시간 KIS 일관성 검사)."""
    return JSONResponse(await asyncio.to_thread(_get_overseas_reconcile))


# ── 해외주식 실시간 스트림 ────────────────────────────────────────────────────

def _get_overseas_dir() -> Path | None:
    p = _OUTPUT_DIR.parent / "output" / "kis_overseas"
    if p.exists():
        return p
    p2 = _OUTPUT_DIR / "kis_overseas"
    return p2 if p2.exists() else None


def _is_overseas_process_running() -> bool:
    import subprocess
    try:
        result = subprocess.run(["pgrep", "-f", "overseas-stream"], capture_output=True, timeout=3)
        return result.returncode == 0
    except Exception:
        return False


def _get_overseas_stream() -> dict:
    """해외주식 K-GSQS 스트림 상태."""
    import time as _time
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    h, m, wd = now.hour, now.minute, now.weekday()
    _in_time = (h == 22 and m >= 30) or (h == 23) or (0 <= h < 5) or (h == 5 and m == 0)
    # 주말 제외: 22~23시 → 토·일 KST = 미국 토·일, 새벽 → 일·월 KST = 미국 토·일
    if h >= 22:
        _in_time = _in_time and wd not in (5, 6)
    else:
        _in_time = _in_time and wd not in (6, 0)
    market_hours = _in_time

    process_running = _is_overseas_process_running()
    os_dir = _get_overseas_dir()

    if not os_dir:
        return {"running": process_running, "market_hours": market_hours, "scores": [], "signal_count": 0}

    bars_dir = os_dir / "bars"
    if not bars_dir.exists():
        return {"running": process_running, "market_hours": market_hours, "scores": [], "signal_count": 0}

    try:
        bar_files = list(bars_dir.glob("*_1m.jsonl"))
        if not bar_files:
            return {"running": process_running, "market_hours": market_hours, "scores": [], "signal_count": 0}
        latest_mtime = max(f.stat().st_mtime for f in bar_files)
        file_active = (_time.time() - latest_mtime) < 1800
        running = process_running or file_active
    except Exception:
        running = process_running

    from deepsignal.market_data.kis_stream.feature_engine import StockFeatureEngine
    from deepsignal.market_data.kis_stream.models import KisOhlcvBar
    from deepsignal.scoring.kstock_scorer import compute_kgsqs, THRESHOLD_NOTIFY, THRESHOLD_AUTO

    eng = StockFeatureEngine()
    scores = []

    for bar_file in sorted(bar_files):
        sym = bar_file.name.replace("_1m.jsonl", "")
        try:
            lines = bar_file.read_text(encoding="utf-8").strip().splitlines()
            if not lines:
                continue
            for line in lines[-30:]:
                row = json.loads(line)
                bar = KisOhlcvBar.from_dict(row)
                eng.on_bar(bar)
            features = eng.build_features(sym)
            if features is None:
                continue
            signal = compute_kgsqs(features)
            scores.append({
                "symbol": sym,
                "price": features.price,
                "total_score": signal.total_score,
                "action": signal.action,
                "hard_blocked": signal.hard_blocked,
                "sub_scores": signal.sub_scores,
            })
        except Exception:
            pass

    scores.sort(key=lambda x: x["total_score"], reverse=True)

    return {
        "running": running,
        "market_hours": market_hours,
        "scores": scores[:20],
        "signal_count": 0,
        "threshold_notify": THRESHOLD_NOTIFY,
        "threshold_auto": THRESHOLD_AUTO,
    }


def _get_overseas_signals() -> dict:
    """해외주식 신호 이력."""
    os_dir = _get_overseas_dir()
    if not os_dir:
        return {"recent": [], "win_rates": {}, "symbol_stats": []}
    sig_log = os_dir / "kstock" / "signal_log.jsonl"
    if not sig_log.exists():
        return {"recent": [], "win_rates": {}, "symbol_stats": []}
    try:
        lines = sig_log.read_text(encoding="utf-8").strip().splitlines()
        parsed = [json.loads(l) for l in lines]
        horizons = ["ret_1m", "ret_3m", "ret_5m", "ret_15m"]
        win_rates: dict = {}
        for h in horizons:
            vals = [p[h] for p in parsed if p.get(h) is not None]
            if vals:
                wins = sum(1 for v in vals if v > 0)
                win_rates[h] = {"win_rate": round(wins / len(vals) * 100, 1), "count": len(vals)}
        recent = sorted(parsed, key=lambda x: x.get("ts_ms", 0), reverse=True)[:20]
        return {"recent": recent, "win_rates": win_rates, "symbol_stats": []}
    except Exception:
        return {"recent": [], "win_rates": {}, "symbol_stats": []}


def _get_overseas_universe() -> dict:
    """해외주식 감시 유니버스."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    h, m, wd = now.hour, now.minute, now.weekday()
    _in_time = (h == 22 and m >= 30) or (h == 23) or (0 <= h < 5)
    if h >= 22:
        _in_time = _in_time and wd not in (5, 6)
    else:
        _in_time = _in_time and wd not in (6, 0)
    market_open = _in_time

    os_dir = _get_overseas_dir()
    symbols_info = []

    if os_dir and (os_dir / "bars").exists():
        import time as _time
        for f in sorted((os_dir / "bars").glob("*_1m.jsonl")):
            sym = f.name.replace("_1m.jsonl", "")
            try:
                lines = f.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    last = json.loads(lines[-1])
                    bar_age_min = round((_time.time() * 1000 - last.get("open_ts_ms", 0)) / 60000, 1)
                    symbols_info.append({
                        "symbol": sym,
                        "last_close": last.get("close"),
                        "last_volume": last.get("volume"),
                        "bar_count": len(lines),
                        "bar_age_min": bar_age_min,
                    })
            except Exception:
                symbols_info.append({"symbol": sym})

    auto_universe = True
    universe_size = 30
    if not symbols_info:
        try:
            from deepsignal.market_data.kis_stream.overseas_pipeline import DEFAULT_OVERSEAS_SYMBOLS, OverseasStreamConfig
            symbols_info = [{"symbol": f"{e}:{t}"} for e, t in DEFAULT_OVERSEAS_SYMBOLS]
            _ocfg = OverseasStreamConfig()
            auto_universe = _ocfg.auto_universe
            universe_size = _ocfg.universe_size
        except Exception:
            pass

    return {
        "symbol_count": len(symbols_info),
        "market_status": "open" if market_open else "closed",
        "current_time_kst": now.strftime("%H:%M"),
        "symbols": symbols_info[:universe_size],
        "market_name": "미국 (US Regular 22:30~05:00 KST)",
        "auto_universe": auto_universe,
        "universe_size": universe_size,
        "refresh_interval_min": 30,
    }


@app.get("/api/overseas/stream")
async def api_overseas_stream() -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(_get_overseas_stream))


@app.get("/api/overseas/signals")
async def api_overseas_signals() -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(_get_overseas_signals))


@app.get("/api/overseas/universe")
async def api_overseas_universe() -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(_get_overseas_universe))


# ── 투자공격성 다이얼 (1~10) ─────────────────

@app.get("/api/aggression")
async def api_aggression_get() -> JSONResponse:
    """현재 공격성 단계 + 프로파일 + 전체 1~10 표."""
    from deepsignal.risk.aggression import resolve, summary_table, current_level
    # .env 값을 환경에 반영(웹 프로세스가 최신값 보도록)
    try:
        from dotenv import load_dotenv
        load_dotenv(str(_ENV_PATH), override=True)
    except Exception:
        pass
    return JSONResponse({
        "level": current_level(),
        "current": resolve().to_dict(),
        "table": summary_table(),
    })


class AggressionUpdate(BaseModel):
    level: int


@app.post("/api/aggression")
async def api_aggression_set(req: AggressionUpdate) -> JSONResponse:
    """공격성 단계 변경 → .env 저장 + 텔레그램 통보."""
    from deepsignal.risk.aggression import clamp_level, resolve
    lvl = clamp_level(req.level)
    ok, msg = await asyncio.to_thread(write_settings, _ENV_PATH, {"DEEPSIGNAL_AGGRESSION": str(lvl)})
    if ok:
        os.environ["DEEPSIGNAL_AGGRESSION"] = str(lvl)
        # 러너들이 즉시 새 단계를 반영하도록 재시작 (macOS launchd)
        async def _restart_runners() -> None:
            import shutil
            import subprocess
            if not shutil.which("launchctl"):
                return
            uid = os.getuid() if hasattr(os, "getuid") else 0
            for label in ("crypto_auto_runner", "auto_runner", "overseas_auto_runner",
                          "regime_trend_runner", "leverage_trend_runner", "intraday_runner"):
                try:
                    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/com.deepsignal.{label}"],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass
        asyncio.create_task(_restart_runners())
        p = resolve(lvl)
        warn = "⚠️ 청산 가능 구간" if p.band == "liquidation_possible" else ("⚠️ 위험 구간" if p.band == "risky" else "")
        await _telegram_notify(
            f"🎚 <b>투자공격성 {lvl}단계로 변경</b> ({p.band_kr})\n{p.note}\n예상 최대낙폭 {p.est_mdd_pct}% {warn}\n러너 재시작으로 즉시 반영됩니다.")
    return JSONResponse({"ok": ok, "message": msg, "level": lvl, "current": resolve(lvl).to_dict()})


# ── GSQS 단타 신호 ─────────────────────────

@app.get("/api/scalping/scores")
async def api_scalping_scores() -> JSONResponse:
    """feature_vectors.json 기반 실시간 GSQS 채점 결과."""
    return JSONResponse(await asyncio.to_thread(_get_scalping_scores))


@app.get("/api/scalping/signals")
async def api_scalping_signals() -> JSONResponse:
    """signal_log.jsonl 기반 신호 이력 & 승률 통계."""
    return JSONResponse(await asyncio.to_thread(_get_scalping_signals))


@app.get("/api/scalping/weights")
async def api_scalping_weights() -> JSONResponse:
    """가중치 최적화기 상태 및 현재 가중치."""
    return JSONResponse(await asyncio.to_thread(_get_scalping_weights))


@app.get("/api/macro/status")
async def api_macro_status() -> JSONResponse:
    """심볼 간 상관관계 + 매크로 이벤트 상태."""
    return JSONResponse(await asyncio.to_thread(_get_macro_status))


# ── 수익률 통계 ─────────────────────────────

def _empty_returns_stat() -> dict:
    return {"avg_return_pct": 0.0, "total_realized_krw": 0.0,
            "trade_count": 0, "win_count": 0, "win_rate": 0.0}


def _compute_crypto_returns_stats(date_from: str, date_to: str) -> dict:
    """crypto_trades DB에서 기간 내 실현 수익률 집계.

    - 청산 완료 거래만 집계 (exit_price > 0). 미청산 포지션 제외.
    - 동일 거래 중복 삽입 방지를 위해 DISTINCT 적용.
    - 기간 기준: 청산 시각(exit_time).
    """
    db_path = _OUTPUT_DIR / "crypto_trades.db"
    if not db_path.exists():
        return _empty_returns_stat()
    try:
        import sqlite3 as _sq
        conn = _sq.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT symbol, entry_time, exit_time, actual_return, position_size, entry_price, exit_price "
            "FROM crypto_trades "
            "WHERE paper=0 AND exit_price>0 AND actual_return IS NOT NULL AND entry_price>0 "
            "AND exit_time>=? AND exit_time<=?",
            (date_from + "T00:00:00", date_to + "T23:59:59"),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return _empty_returns_stat()
    if not rows:
        return _empty_returns_stat()
    returns, total_krw, wins = [], 0.0, 0
    for _sym, _et, _xt, actual_ret, pos_size, entry_px, exit_px in rows:
        r = float(actual_ret or 0)
        # 실현손익 = (매도가 - 매수가) × 수량
        total_krw += (float(exit_px or 0) - float(entry_px or 0)) * float(pos_size or 0)
        returns.append(r * 100)
        if r > 0:
            wins += 1
    count = len(returns)
    return {
        "avg_return_pct":    round(sum(returns) / count, 2) if count else 0.0,
        "total_realized_krw": round(total_krw, 0),
        "trade_count": count,
        "win_count":   wins,
        "win_rate":    round(wins / count * 100, 1) if count else 0.0,
    }


def _compute_stock_returns_stats(date_from: str, date_to: str) -> dict:
    """KIS 주식 체결 내역을 FIFO 매칭해 실현 수익률 집계.

    매수는 조회 기간 이전에 발생했을 수 있으므로,
    매도 기간만 date_from~date_to로 제한하고 매수 조회는 최대 1년 전으로 확장.
    """
    try:
        from datetime import date as _d, timedelta as _td
        # 매수 조회 범위: date_from 기준 1년 전까지 확장
        d_from_obj = _d.fromisoformat(date_from)
        buy_from = (d_from_obj - _td(days=365)).isoformat()

        # 전체 기간 trades (buy: 1년 전 ~ today, sell: 기간 내)
        all_trades = _fetch_stock_trades(buy_from, date_to)
        if not all_trades:
            return _empty_returns_stat()

        from collections import defaultdict as _dd
        buy_q: dict = _dd(list)
        returns, total_krw, wins = [], 0.0, 0

        # 시간순 정렬 후 FIFO 매칭
        for t in sorted(all_trades, key=lambda x: x.get("executed_at") or ""):
            side  = t.get("side", "")
            sym   = t.get("symbol", "")
            price = float(t.get("unit_price") or 0)
            qty   = float(t.get("quantity") or 0)
            ts    = t.get("executed_at") or ""
            if not sym or price <= 0 or qty <= 0:
                continue
            if side == "buy":
                buy_q[sym].append([price, qty])
            elif side == "sell" and ts >= date_from:
                # 조회 기간 내 매도만 결과에 포함
                rem = qty
                while rem > 0 and buy_q[sym]:
                    bp, bq = buy_q[sym][0]
                    mq = min(rem, bq)
                    r = (price - bp) / bp * 100
                    returns.append(r)
                    total_krw += (price - bp) * mq
                    if r > 0:
                        wins += 1
                    rem -= mq
                    if bq <= mq:
                        buy_q[sym].pop(0)
                    else:
                        buy_q[sym][0][1] -= mq

        count = len(returns)
        return {
            "avg_return_pct":    round(sum(returns) / count, 2) if count else 0.0,
            "total_realized_krw": round(total_krw, 0),
            "trade_count": count,
            "win_count":   wins,
            "win_rate":    round(wins / count * 100, 1) if count else 0.0,
        }
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning("stock returns stats: %s", _e)
        return _empty_returns_stat()


@app.get("/api/stats/returns")
async def api_stats_returns(period: str = "1m") -> JSONResponse:
    """기간별 실현 수익률 통계 (1d|1w|1m|all)."""
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    if period == "1d":
        d_from = today                       # 오늘
    elif period == "1w":
        d_from = today - _td(days=7)          # 오늘 기준 이전 1주일
    elif period == "1m":
        d_from = today - _td(days=30)         # 오늘 기준 이전 1개월
    else:
        # "전체": 2020-01-01 고정 대신 실제 데이터 최초 날짜 사용
        earliest = _date(2020, 1, 1)
        try:
            import sqlite3 as _sq3
            db_path = _OUTPUT_DIR / "crypto_trades.db"
            if db_path.exists():
                with _sq3.connect(str(db_path)) as _c:
                    row = _c.execute(
                        "SELECT MIN(entry_time) FROM crypto_trades WHERE paper=0"
                    ).fetchone()
                    if row and row[0]:
                        earliest = _date.fromisoformat(str(row[0])[:10])
        except Exception:
            pass
        d_from = earliest
    start, end = d_from.isoformat(), today.isoformat()
    crypto = await asyncio.to_thread(_compute_crypto_returns_stats, start, end)
    stock  = await asyncio.to_thread(_compute_stock_returns_stats,  start, end)
    tc = crypto["trade_count"] + stock["trade_count"]
    avg_combined = (
        (crypto["avg_return_pct"] * crypto["trade_count"] +
         stock["avg_return_pct"]  * stock["trade_count"]) / tc
    ) if tc else 0.0
    tw = crypto["win_count"] + stock["win_count"]
    return JSONResponse({
        "period": period, "date_from": start, "date_to": end,
        "crypto": crypto,
        "stock":  stock,
        "combined": {
            "avg_return_pct":    round(avg_combined, 2),
            "total_realized_krw": round(
                crypto["total_realized_krw"] + stock["total_realized_krw"], 0),
            "trade_count": tc,
            "win_count":   tw,
            "win_rate":    round(tw / tc * 100, 1) if tc else 0.0,
        },
    })


# ── 리포트 ─────────────────────────────────

@app.get("/api/reports")
async def api_reports() -> JSONResponse:
    try:
        md_files = sorted(
            [p for p in _OUTPUT_DIR.glob("*.md") if not p.name.startswith("._")],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        reports = []
        for f in md_files[:30]:
            try:
                reports.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                })
            except Exception:
                pass
        return JSONResponse({"reports": reports})
    except Exception as e:
        return JSONResponse({"reports": [], "error": str(e)})


@app.get("/api/reports/{filename}")
async def api_report_content(filename: str) -> JSONResponse:
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "잘못된 파일명")
    path = _OUTPUT_DIR / filename
    if not path.is_file() or path.suffix not in (".md", ".txt"):
        raise HTTPException(404, "리포트 없음")
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return JSONResponse({"name": filename, "content": content})
    except Exception as e:
        raise HTTPException(500, str(e))


# ── 분석 실행 트리거 ──────────────────────

class RecommendRequest(BaseModel):
    type: str = "crypto"


def _norm_overseas_orders(plan: dict) -> list[dict]:
    """해외 plan dict → 표시용 주문 목록(원화 환산 포함)."""
    rate = float(plan.get("usd_rate") or 1350.0)
    out = []
    for o in (plan.get("orders") or []):
        try:
            sym = str(o.get("symbol") or "").split(":")[-1]
            qty = int(o.get("quantity") or 0)
            px = float(o.get("estimated_price_usd") or 0)
            amt_usd = float(o.get("estimated_order_value_usd") or qty * px)
            out.append({
                "symbol": sym, "name": sym,
                "side": "매수" if str(o.get("side", "BUY")).upper() == "BUY" else "매도",
                "qty": qty, "price": round(px, 2), "price_unit": "$",
                "amount_krw": round(amt_usd * rate), "amount_usd": round(amt_usd, 2),
                "score": o.get("score"), "reason": str(o.get("reason") or ""),
            })
        except Exception:
            continue
    return out


def _build_plan_detail(req_type: str) -> dict | None:
    """생성된 매매계획을 표시용 구조로 추출. 없으면 None.

    반환: {"kind", "orders":[{symbol,name,side,qty,price,price_unit,
            amount_krw,score,reason,tp_pct,sl_pct}], "total_krw"}
    """
    import json as _j
    try:
        if req_type == "crypto":
            p = _OUTPUT_DIR / "CRYPTO_ORDER_PLAN.json"
            if not p.exists():
                return None
            d = _j.loads(p.read_text(encoding="utf-8"))
            mkt = d.get("market") or ""
            status = str(d.get("status") or "")
            if not mkt or "NO_RECOMMENDATION" in status or "NO_ORDER" in status:
                return None
            o = {
                "symbol": mkt, "name": d.get("display_name") or mkt,
                "side": "매수" if str(d.get("side")) == "buy" else "매도",
                "qty": d.get("volume"), "price": d.get("limit_price"), "price_unit": "₩",
                "amount_krw": round(float(d.get("krw_amount") or 0)),
                "score": d.get("final_score"), "reason": str(d.get("reason") or ""),
                "tp_pct": d.get("take_profit_pct"), "sl_pct": d.get("stop_loss_pct"),
            }
            return {"kind": "코인", "orders": [o], "total_krw": o["amount_krw"]}
        else:
            p = _OUTPUT_DIR / "live_order_plan_ai_latest.json"
            if not p.exists():
                return None
            d = _j.loads(p.read_text(encoding="utf-8"))
            orders = []
            total = 0.0
            for o in (d.get("orders") or []):
                amt = float(o.get("estimated_order_value") or 0)
                total += amt
                orders.append({
                    "symbol": o.get("symbol"), "name": o.get("symbol"),
                    "side": "매수" if str(o.get("side", "BUY")).upper() == "BUY" else "매도",
                    "qty": o.get("estimated_qty"),
                    "price": o.get("estimated_price"), "price_unit": "₩",
                    "amount_krw": round(amt),
                    "score": (o.get("ai_confidence")),
                    "reason": str(o.get("reason") or ""),
                })
            if not orders:
                return None
            return {"kind": "국내주식", "orders": orders, "total_krw": round(total)}
    except Exception:
        return None
    return None


@app.post("/api/runner/recommend")
async def api_runner_recommend(req: RecommendRequest) -> JSONResponse:
    import subprocess as _sp
    import asyncio

    # ── 해외주식: subprocess 없이 직접 plan 생성 (장 시간 무관, 스코어 있으면 plan) ──
    if req.type == "overseas":
        try:
            from deepsignal.live_trading.overseas_plan import build_overseas_order_plan
            # USD 환율 (해외 포지션 조회에서 가져오거나 기본)
            try:
                _ospos = await asyncio.to_thread(_get_overseas_positions)
                _rate = float(_ospos.get("usd_rate") or 1350)
                _avail = float(_ospos.get("cash_usd") or 0) or None
            except Exception:
                _rate, _avail = 1350.0, None
            plan = await asyncio.to_thread(
                build_overseas_order_plan, str(_OUTPUT_DIR),
                usd_rate=_rate, available_cash_usd=_avail,
            )
            n = plan.get("order_count", 0)
            if n > 0:
                tickers = ", ".join(o["symbol"].split(":")[-1] for o in plan["orders"][:3])
                return JSONResponse({"ok": True, "has_plan": True,
                    "message": f"✅ 해외주식 분석 완료 — {n}종목 매수 후보 ({tickers}{'…' if n>3 else ''})",
                    "plan": {"kind": "해외주식", "orders": _norm_overseas_orders(plan),
                             "total_krw": round(sum(_o.get("amount_krw", 0) for _o in _norm_overseas_orders(plan)))}})
            scanned = plan.get("scanned", 0)
            if scanned == 0:
                import datetime as _dt
                _now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9)))
                _h, _m, _wd = _now.hour, _now.minute, _now.weekday()
                _us_open = ((_h == 22 and _m >= 30) or _h == 23 or (0 <= _h < 5)) and _wd < 5
                _msg = ("✅ 해외주식 분석 완료 — 미국 장중이나 스코어 0건 "
                        "(해외 실시간 스트림 미연결 가능 — 러너 제어에서 kis_overseas_stream 확인)"
                        if _us_open else
                        "✅ 해외주식 분석 완료 — 미국 장외 시간 (스코어 0건)")
                return JSONResponse({"ok": True, "has_plan": False, "message": _msg})
            return JSONResponse({"ok": True, "has_plan": False,
                "message": f"✅ 해외주식 분석 완료 — 추천 없음 (스캔 {scanned}종목, 매수 조건 미충족)"})
        except Exception as e:
            import logging as _lg
            _lg.getLogger(__name__).warning("overseas recommend failed: %s", e)
            return JSONResponse({"ok": False, "message": f"해외주식 분석 실패: {str(e)[:120]}"})

    cmd_map = {
        "crypto": [sys.executable, str(_PROJECT_ROOT / "main.py"), "crypto-daily-plan",
                   "--output-dir", str(_OUTPUT_DIR)],
        "stock":  [sys.executable, str(_PROJECT_ROOT / "main.py"), "daily-ai-trade-plan",
                   "--network", "--output-dir", str(_OUTPUT_DIR)],
    }
    cmd = cmd_map.get(req.type)
    if not cmd:
        return JSONResponse({"ok": False, "message": "알 수 없는 유형"})
    label = "코인" if req.type == "crypto" else "주식"
    try:
        log_path = _OUTPUT_DIR / "webui_recommend.log"
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(_PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        except asyncio.TimeoutError:
            proc.kill()
            return JSONResponse({"ok": False, "message": f"{label} 분석 시간 초과 (3분)"})
        output = stdout_bytes.decode("utf-8", errors="replace")
        # 로그 파일에도 기록
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(output)
        except Exception:
            pass
        rc = proc.returncode
        if rc != 0:
            last_lines = "\n".join(output.strip().splitlines()[-3:])
            return JSONResponse({"ok": False, "message": f"{label} 분석 실패 (exit {rc}): {last_lines}"})
        # 결과 요약
        if req.type == "crypto":
            plan_path = _OUTPUT_DIR / "CRYPTO_ORDER_PLAN.json"
            if plan_path.exists():
                try:
                    import json as _j
                    plan_raw = _j.loads(plan_path.read_text(encoding="utf-8"))
                    status = plan_raw.get("status", "")
                    market = plan_raw.get("market", "")
                    side = "매수" if plan_raw.get("side") == "buy" else "매도"
                    if status and market:
                        # 무승인 통일: 코인도 수동 분석 시 승인 배너를 띄우지 않는다.
                        # (분석=미리보기, 실제 매수는 무승인 자동매매 러너가 처리)
                        return JSONResponse({"ok": True, "message": f"✅ {label} 분석 완료 — {market} {side} 계획 생성됨", "has_plan": True, "plan": _build_plan_detail("crypto")})
                except Exception:
                    pass
            return JSONResponse({"ok": True, "message": f"✅ {label} 분석 완료 — 추천 없음 (매매 조건 미충족)", "has_plan": False})
        else:
            plan_path = _OUTPUT_DIR / "AI_DAILY_TRADE_PLAN.md"
            if plan_path.exists():
                try:
                    content = plan_path.read_text(encoding="utf-8")
                    if "주문 수: 0개" in content or "NO_ORDERS" in content:
                        return JSONResponse({"ok": True, "message": f"✅ {label} 분석 완료 — 추천 없음 (매매 조건 미충족)", "has_plan": False})
                    return JSONResponse({"ok": True, "message": f"✅ {label} 분석 완료 — 매매계획 생성됨", "has_plan": True, "plan": _build_plan_detail(req.type)})
                except Exception:
                    pass
            return JSONResponse({"ok": True, "message": f"✅ {label} 분석 완료", "has_plan": False})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)})


# ── 설정 ───────────────────────────────────

@app.get("/api/settings")
async def api_get_settings() -> JSONResponse:
    data = read_settings(_ENV_PATH)
    return JSONResponse(data)


class SettingsUpdateRequest(BaseModel):
    updates: dict[str, str]


@app.post("/api/settings")
async def api_save_settings(req: SettingsUpdateRequest) -> JSONResponse:
    ok, msg = write_settings(_ENV_PATH, req.updates)
    return JSONResponse({"ok": ok, "message": msg})


# ── 로그 REST (최근 N줄) ──────────────────

@app.get("/api/logs")
async def api_logs(file: str = "crypto_auto_runner", lines: int = 100) -> JSONResponse:
    allowed = {"crypto_auto_runner", "crypto_auto_runner.error", "binance_stream"}
    if file not in allowed:
        raise HTTPException(400, "허용되지 않은 로그 파일")
    log_dir = Path.home() / ".deepsignal" / "logs"
    log_path = log_dir / f"{file}.log"
    if not log_path.is_file():
        return JSONResponse({"lines": [], "path": str(log_path), "exists": False})
    try:
        all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return JSONResponse({
            "lines": all_lines[-lines:],
            "path": str(log_path),
            "exists": True,
            "total": len(all_lines),
        })
    except Exception as e:
        return JSONResponse({"lines": [], "error": str(e), "exists": False})


# ── WebSocket 로그 스트리밍 ──────────────

@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket, file: str = "crypto_auto_runner"):
    await ws.accept()
    allowed = {"crypto_auto_runner", "crypto_auto_runner.error", "binance_stream"}
    if file not in allowed:
        await ws.close(code=1008)
        return

    log_dir = Path.home() / ".deepsignal" / "logs"
    log_path = log_dir / f"{file}.log"

    # 기존 마지막 50줄 전송
    if log_path.is_file():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-50:]:
            await ws.send_json({"type": "line", "data": line})

    # tail -f 스타일 실시간 스트리밍
    last_size = log_path.stat().st_size if log_path.is_file() else 0
    try:
        while True:
            await asyncio.sleep(1)
            if not log_path.is_file():
                continue
            cur_size = log_path.stat().st_size
            if cur_size > last_size:
                with log_path.open("r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_size)
                    new_content = f.read()
                for line in new_content.splitlines():
                    if line.strip():
                        await ws.send_json({"type": "line", "data": line})
                last_size = cur_size
            elif cur_size < last_size:
                # 로그 로테이션
                last_size = 0
    except WebSocketDisconnect:
        pass


# ── WebSocket 상태 푸시 (레거시 폴링 유지) ─
@app.websocket("/ws/status")
async def ws_status(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            runner = get_runner_status(_OUTPUT_DIR)
            await ws.send_json({"type": "status", "data": runner})
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass


# ── WebSocket 이벤트 스트림 (실시간 연동) ──

@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """EventBus 구독 → 상태 변화 즉시 푸시."""
    await ws.accept()
    q = _event_bus.subscribe()
    # 연결 직후 현재 상태 스냅샷 전송
    try:
        runner = await asyncio.to_thread(get_runner_status, _OUTPUT_DIR)
        await ws.send_json({"type": "snapshot", "data": {"runner": runner}})
    except Exception:
        pass
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30)
                await ws.send_json(event)
            except asyncio.TimeoutError:
                # heartbeat — 브라우저 연결 유지
                await ws.send_json({"type": "ping"})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _event_bus.unsubscribe(q)


# ── 정적 파일 / SPA 폴백 ──────────────────

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@app.get("/static/{filename:path}")
async def static_files(filename: str):
    """정적 파일 — no-cache 헤더 포함."""
    path = _STATIC / filename
    if not path.is_file():
        from fastapi import HTTPException
        raise HTTPException(404)
    return FileResponse(str(path), headers=_NO_CACHE)


@app.get("/")
async def root():
    return FileResponse(str(_STATIC / "index.html"), headers=_NO_CACHE)


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # API 경로는 위에서 처리됨
    return FileResponse(str(_STATIC / "index.html"), headers=_NO_CACHE)


# ──────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────

def run_web_ui(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    output_dir: str = "outputs",
    project_root: str = ".",
    env_path: str = ".env",
    no_browser: bool = False,
) -> None:
    global _OUTPUT_DIR, _PROJECT_ROOT, _ENV_PATH
    _OUTPUT_DIR = Path(output_dir).resolve()
    _PROJECT_ROOT = Path(project_root).resolve()
    _ENV_PATH = Path(env_path).resolve()

    url = f"http://{host}:{port}"
    print(f"\n  DeepSignal Web UI → {url}\n  Ctrl+C 로 종료\n", flush=True)

    if not no_browser:
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")
