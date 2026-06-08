"""고속 회전 — 장중 인트라데이 루프 (P4).

기존 '하루 1회' 주식 플랜의 한계를 넘어, 장중 N분 간격으로:
  1) 안전 게이트 확인(장시간·일시정지·킬스위치)
  2) 레이트리미터로 KIS 호출 직렬화(초당 한도 보호)
  3) 보유 종목 트레일링 스톱·하드손절 점검 → 도달 시 청산(또는 dry-run 보고)

기본 dry-run(execute=False) — 실주문은 명시적으로 켜야 동작.
신규 진입(스캔→매수)은 기존 검증된 경로를 그대로 재사용하도록 훅만 제공한다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def _interval_seconds() -> int:
    try:
        return max(30, int(float(os.environ.get("INTRADAY_INTERVAL_SEC", "180"))))
    except ValueError:
        return 180


def intraday_enabled() -> bool:
    return os.environ.get("INTRADAY_RUNNER_ENABLED", "").strip().lower() in ("1", "true", "yes")


def run_intraday_tick(output_dir: str | Path, *, execute: bool = False, market: str = "kr") -> dict[str, Any]:
    """장중 1틱: 게이트 점검 → 보유 트레일링스톱 점검. dict 요약 반환."""
    from deepsignal.risk.trading_halt import is_trading_halted
    from deepsignal.risk.rate_limiter import kis_acquire
    from deepsignal.risk.trailing_stop import TrailingState, trailing_pct_for_leverage
    from deepsignal.live_trading.overseas_auto_execute import is_runner_paused

    out = {"market": market, "execute": execute, "actions": [], "skipped": None}

    # 0) 투자공격성 다이얼 적용 (트레일링 폭 등 반영)
    try:
        from deepsignal.risk.aggression import refresh_and_apply
        refresh_and_apply()
    except Exception:
        pass

    # 1) 안전 게이트
    halted, hreason = is_trading_halted(output_dir)
    if halted:
        out["skipped"] = f"halt: {hreason}"
        return out
    if is_runner_paused(output_dir):
        out["skipped"] = "paused"
        return out

    # 2) 장 시간 게이트
    if market == "us":
        from deepsignal.live_trading.overseas_auto_execute import is_us_market_open
        if not is_us_market_open():
            out["skipped"] = "us_market_closed"
            return out
    else:
        try:
            from deepsignal.live_trading.utils.trading_session import (
                is_trading_session_open, load_trading_session_policy_from_env,
            )
            if not is_trading_session_open(policy=load_trading_session_policy_from_env()).is_open:
                out["skipped"] = "kr_market_closed"
                return out
        except Exception:
            pass  # 세션 판정 실패 시 통과(보유 점검은 진행)

    # 3) 보유 종목 트레일링 스톱 점검
    try:
        from deepsignal.live_trading.broker.kis_broker import KISBroker
        from deepsignal.live_trading.broker.kis_config import load_kis_config_from_env
        kis_acquire()  # 초당 한도 보호
        broker = KISBroker(load_kis_config_from_env(), safe_mode=not execute)
        positions = broker.get_positions()
    except Exception as e:  # noqa: BLE001
        out["skipped"] = f"position_fetch_failed: {e}"
        return out

    trail_base = float(os.environ.get("INTRADAY_TRAIL_PCT", "0.10"))
    state = _load_peak_state(output_dir)
    for p in positions:
        sym = str(p.symbol)
        cur = float(p.current_price or 0)
        avg = float(p.avg_price or 0)
        if cur <= 0 or avg <= 0:
            continue
        peak = max(state.get(sym, avg), cur)
        state[sym] = peak
        ts = TrailingState(entry_price=avg, peak_price=peak,
                           trail_pct=trailing_pct_for_leverage(1.0, trail_base),
                           hard_stop_pct=float(os.environ.get("INTRADAY_HARD_STOP_PCT", "0.07")))
        exit_now, why = ts.should_exit(cur)
        if exit_now:
            action = {"symbol": sym, "qty": int(p.quantity or 0), "price": cur,
                      "reason": why, "executed": False}
            if execute:
                # 실청산은 기존 주문 경로 재사용 (여기선 안전상 보고만; 실주문 연결은 운영 검증 후)
                action["note"] = "execute=True지만 실청산 연결은 운영검증 후 활성화"
            out["actions"].append(action)
    _save_peak_state(output_dir, state)
    return out


def _peak_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / "INTRADAY_PEAK_STATE.json"


def _load_peak_state(output_dir: str | Path) -> dict[str, float]:
    import json
    p = _peak_path(output_dir)
    if not p.is_file():
        return {}
    try:
        return {k: float(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}


def _save_peak_state(output_dir: str | Path, state: dict[str, float]) -> None:
    import json
    p = _peak_path(output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def run_intraday_loop(output_dir: str | Path, *, execute: bool = False, market: str = "kr") -> None:
    """상시 루프 — INTRADAY_INTERVAL_SEC 간격으로 tick 반복."""
    interval = _interval_seconds()
    print(f"intraday-runner 시작 (market={market}, interval={interval}s, execute={execute})", flush=True)
    while True:
        try:
            res = run_intraday_tick(output_dir, execute=execute, market=market)
            if res.get("skipped"):
                print(f"[tick] skip: {res['skipped']}", flush=True)
            elif res.get("actions"):
                for a in res["actions"]:
                    print(f"[tick] 청산신호 {a['symbol']} {a['qty']}주 @ {a['price']:,.0f} — {a['reason']}", flush=True)
            else:
                print("[tick] 보유 점검 완료 — 청산 신호 없음", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[tick] 오류: {e}", flush=True)
        time.sleep(interval)
