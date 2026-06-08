"""전역 거래 중단(kill-switch) + 일일 실현손실 한도.

설계:
- ``outputs/TRADING_HALT`` 파일이 존재하면 모든 러너(코인·KIS)가 **신규 매수를 중단**한다.
  매도/청산(TP/SL)은 계속 동작한다 — "더 사지 말되, 보유분은 빠질 수 있게".
- 일일 실현손실이 한도를 넘으면 이 파일을 **자동 생성**한다(auto kill-switch).
- 수동으로 즉시 멈추려면 파일을 만들면 된다(CLI: ``trading-halt`` / 해제 ``trading-resume``).

env:
- ``DEEPSIGNAL_MAX_DAILY_LOSS_KRW`` (기본 100000) — 당일 실현손실이 이 KRW 이상이면 자동 halt. 0=비활성.
- ``DEEPSIGNAL_MAX_DAILY_LOSS_PCT`` (기본 0=비활성) — equity 대비 손실% 한도.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

HALT_FILENAME = "TRADING_HALT"
_KST = timezone(timedelta(hours=9))

_ENV_LOSS_KRW = "DEEPSIGNAL_MAX_DAILY_LOSS_KRW"
_ENV_LOSS_PCT = "DEEPSIGNAL_MAX_DAILY_LOSS_PCT"
_DEFAULT_LOSS_KRW = 100000.0


def halt_flag_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / HALT_FILENAME


def is_trading_halted(output_dir: str | Path) -> tuple[bool, str]:
    """(중단여부, 사유). 파일이 존재하면 중단."""
    p = halt_flag_path(output_dir)
    if not p.is_file():
        return (False, "")
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
        return (True, str(meta.get("reason") or "manual halt"))
    except (OSError, json.JSONDecodeError):
        return (True, "manual halt")


def engage_halt(output_dir: str | Path, reason: str, *, source: str = "auto") -> Path:
    """halt 파일을 원자적으로 생성한다(이미 있으면 사유 갱신)."""
    p = halt_flag_path(output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active": True,
        "reason": reason,
        "source": source,
        "engaged_at": datetime.now(_KST).isoformat(timespec="seconds"),
    }
    tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return p


def clear_halt(output_dir: str | Path) -> bool:
    """halt 파일을 제거한다. 제거했으면 True, 원래 없었으면 False."""
    try:
        halt_flag_path(output_dir).unlink()
        return True
    except FileNotFoundError:
        return False


@dataclass
class DailyLossPolicy:
    max_loss_krw: float = 0.0
    max_loss_pct: float = 0.0

    @property
    def enabled(self) -> bool:
        return self.max_loss_krw > 0 or self.max_loss_pct > 0


def load_daily_loss_policy_from_env() -> DailyLossPolicy:
    def _f(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    return DailyLossPolicy(
        max_loss_krw=_f(_ENV_LOSS_KRW, _DEFAULT_LOSS_KRW),
        max_loss_pct=_f(_ENV_LOSS_PCT, 0.0),
    )


def evaluate_daily_loss(
    realized_pnl_krw: float | None,
    *,
    equity: float | None = None,
    policy: DailyLossPolicy | None = None,
) -> tuple[bool, str]:
    """당일 실현손익(KRW)이 한도를 넘었는지. (초과여부, 사유)."""
    policy = policy or load_daily_loss_policy_from_env()
    if not policy.enabled or realized_pnl_krw is None or realized_pnl_krw >= 0:
        return (False, "")
    loss = -float(realized_pnl_krw)  # 양수 = 손실 규모
    if policy.max_loss_krw > 0 and loss >= policy.max_loss_krw:
        return (True, f"일일 실현손실 {loss:,.0f}원 ≥ 한도 {policy.max_loss_krw:,.0f}원")
    if policy.max_loss_pct > 0 and equity and equity > 0:
        pct = loss / float(equity) * 100.0
        if pct >= policy.max_loss_pct:
            return (True, f"일일 실현손실 {pct:.2f}% ≥ 한도 {policy.max_loss_pct:.2f}%")
    return (False, "")


def crypto_realized_pnl_krw_today(
    output_dir: str | Path, *, include_paper: bool = False
) -> float | None:
    """오늘(KST) 청산된 코인 실현손익(KRW) 합. crypto_trades.db 없으면 None.

    실현손익 = actual_return(소수) × entry_price × position_size.
    """
    db = Path(output_dir) / "crypto_trades.db"
    if not db.is_file():
        return None
    today = datetime.now(_KST).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT entry_price, position_size, actual_return, paper "
                "FROM crypto_trades WHERE exit_price IS NOT NULL "
                "AND substr(exit_time,1,10)=?",
                (today,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    total = 0.0
    seen = False
    for r in rows:
        if not include_paper and int(r["paper"] or 0) != 0:
            continue
        try:
            ep = float(r["entry_price"])
            ps = float(r["position_size"])
            ar = float(r["actual_return"])
        except (TypeError, ValueError):
            continue
        total += ar * ep * ps
        seen = True
    return total if seen else None


def check_crypto_buy_halt(
    output_dir: str | Path, *, equity: float | None = None
) -> tuple[bool, str]:
    """코인 신규매수 차단 여부 통합 판정.

    1) TRADING_HALT 파일이 있으면 즉시 차단.
    2) 없으면 당일 실현손실을 평가해 한도 초과 시 halt 파일을 자동 생성하고 차단.
    """
    halted, reason = is_trading_halted(output_dir)
    if halted:
        return (True, reason)
    policy = load_daily_loss_policy_from_env()
    if not policy.enabled:
        return (False, "")
    pnl = crypto_realized_pnl_krw_today(output_dir)
    hit, loss_reason = evaluate_daily_loss(pnl, equity=equity, policy=policy)
    if hit:
        engage_halt(output_dir, loss_reason, source="daily_loss_limit")
        return (True, loss_reason)
    return (False, "")
