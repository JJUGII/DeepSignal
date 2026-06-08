"""Tune crypto take_profit / stop_loss / min_volume_ratio from outcome DB."""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_recommendation_outcomes import (
    crypto_outcomes_db_path,
    init_crypto_outcomes_db,
)
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto

TUNING_JSON = "crypto_threshold_tuning_latest.json"
TUNING_MD = "CRYPTO_THRESHOLD_TUNING.md"
ACTIVE_THRESHOLDS_JSON = "CRYPTO_ACTIVE_THRESHOLDS.json"


@dataclass
class CryptoOutcomeSample:
    side: str
    market: str
    return_pct: float
    return_source: str
    exit_reason: str | None
    executed: bool


@dataclass
class CryptoTunedThresholds:
    take_profit_pct: float
    stop_loss_pct: float
    min_volume_ratio: float
    take_profit_buffer_pct: float
    stop_loss_buffer_pct: float
    generated_at: str
    lookback_days: int
    sample_sell_closed: int
    sample_buy_executed: int
    sell_win_rate: float | None
    sell_avg_return_pct: float | None
    buy_win_rate: float | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def load_crypto_outcome_samples(
    outcomes_db: str | Path,
    *,
    lookback_days: int = 60,
) -> list[CryptoOutcomeSample]:
    path = init_crypto_outcomes_db(outcomes_db)
    since = (date.today() - timedelta(days=max(1, int(lookback_days)))).isoformat()
    out: list[CryptoOutcomeSample] = []
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT market, side, executed, realized_pnl_pct, pnl_pct, max_profit_pct,
                   closed_at, exit_reason
            FROM crypto_recommendation_outcomes
            WHERE substr(created_at, 1, 10) >= ?
            """,
            (since,),
        ).fetchall()
    for row in rows:
        side = str(row["side"] or "").lower()
        executed = int(row["executed"] or 0) == 1
        realized = _float(row["realized_pnl_pct"])
        open_pnl = _float(row["pnl_pct"])
        max_profit = _float(row["max_profit_pct"])
        closed = bool(row["closed_at"])
        ret: float | None = None
        source = ""
        if closed and realized is not None:
            ret, source = realized, "realized"
        elif executed and max_profit is not None and side == "buy":
            ret, source = max_profit, "max_profit"
        elif executed and open_pnl is not None:
            ret, source = open_pnl, "open_pnl"
        if ret is None:
            continue
        out.append(
            CryptoOutcomeSample(
                side=side,
                market=str(row["market"]),
                return_pct=float(ret),
                return_source=source,
                exit_reason=str(row["exit_reason"]) if row["exit_reason"] else None,
                executed=executed,
            )
        )
    return out


def tune_crypto_thresholds_from_outcomes(
    outcomes_db: str | Path,
    *,
    lookback_days: int = 60,
    min_sell_samples: int = 3,
    min_buy_samples: int = 5,
    target_sell_win_rate: float = 0.5,
    target_buy_win_rate: float = 0.45,
) -> CryptoTunedThresholds:
    """Derive thresholds from closed sells and executed buys."""
    warnings: list[str] = []
    samples = load_crypto_outcome_samples(outcomes_db, lookback_days=lookback_days)

    if bool(_CRYPTO.prefer_fund_manager_tp_sl):
        from deepsignal.crypto_trading.crypto_fund_manager_policy import fund_manager_tp_sl_percent

        tp, sl, _, _ = fund_manager_tp_sl_percent()
    else:
        tp = float(_CRYPTO.take_profit_pct)
        sl = float(_CRYPTO.stop_loss_pct)
    mvr = float(_CRYPTO.min_volume_ratio)
    mvr_floor = float(_CRYPTO.min_volume_ratio)
    mvr_cap = float(getattr(_CRYPTO, "outcome_tune_max_volume_ratio", 0.45))
    tp_buf = float(_CRYPTO.take_profit_buffer_pct)
    sl_buf = float(_CRYPTO.stop_loss_buffer_pct)

    sell_returns = [
        s.return_pct for s in samples if s.side == "sell" and s.return_source == "realized"
    ]
    buy_returns = [
        s.return_pct
        for s in samples
        if s.side == "buy" and s.executed and s.return_source in ("realized", "max_profit", "open_pnl")
    ]

    sell_win_rate: float | None = None
    sell_avg: float | None = None
    buy_win_rate: float | None = None

    if len(sell_returns) >= min_sell_samples:
        wins = [r for r in sell_returns if r > 0]
        losses = [r for r in sell_returns if r <= 0]
        sell_win_rate = len(wins) / len(sell_returns) if sell_returns else None
        sell_avg = statistics.mean(sell_returns) if sell_returns else None
        if wins:
            med_win = statistics.median(wins)
            tp = _clamp(med_win * 0.92, float(_CRYPTO.tp_pct_min), float(_CRYPTO.tp_pct_max))
        if losses:
            med_loss = statistics.median(losses)
            sl = _clamp(med_loss * 1.08, float(_CRYPTO.sl_pct_min), float(_CRYPTO.sl_pct_max))
        if sell_win_rate is not None and sell_win_rate < target_sell_win_rate - 0.1:
            tp = _clamp(tp * 0.95, float(_CRYPTO.tp_pct_min), float(_CRYPTO.tp_pct_max))
            sl = _clamp(sl * 1.05, float(_CRYPTO.sl_pct_min), float(_CRYPTO.sl_pct_max))
            warnings.append(
                f"SELL 승률 {sell_win_rate * 100:.0f}% < 목표 — 익절 소폭 하향·손절 강화"
            )
    else:
        warnings.append(
            f"청산 매도 샘플 {len(sell_returns)}건 < {min_sell_samples} — take_profit/stop_loss 기본값 유지"
        )

    if len(buy_returns) >= min_buy_samples:
        buy_wins = [r for r in buy_returns if r > 0]
        buy_win_rate = len(buy_wins) / len(buy_returns)
        buy_avg = statistics.mean(buy_returns)
        if buy_win_rate < target_buy_win_rate:
            mvr = _clamp(mvr + 0.05, mvr_floor, mvr_cap)
            warnings.append(
                f"BUY 체결 후 수익률 승률 {buy_win_rate * 100:.0f}% — min_volume_ratio {mvr:.2f}로 강화 (cap {mvr_cap:.2f})"
            )
        elif buy_win_rate > target_buy_win_rate + 0.15 and buy_avg > 0.5:
            mvr = _clamp(mvr - 0.05, mvr_floor, mvr_cap)
            warnings.append(f"BUY 성과 양호 — min_volume_ratio {mvr:.2f}로 완화")
    else:
        warnings.append(
            f"체결 BUY 샘플 {len(buy_returns)}건 < {min_buy_samples} — min_volume_ratio 기본값 유지"
        )

    return CryptoTunedThresholds(
        take_profit_pct=round(tp, 3),
        stop_loss_pct=round(sl, 3),
        min_volume_ratio=round(mvr, 3),
        take_profit_buffer_pct=tp_buf,
        stop_loss_buffer_pct=sl_buf,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        lookback_days=int(lookback_days),
        sample_sell_closed=len(sell_returns),
        sample_buy_executed=len(buy_returns),
        sell_win_rate=sell_win_rate,
        sell_avg_return_pct=round(sell_avg, 3) if sell_avg is not None else None,
        buy_win_rate=buy_win_rate,
        warnings=warnings,
    )


def write_crypto_threshold_tuning_reports(
    tuned: CryptoTunedThresholds,
    *,
    output_dir: str | Path,
    apply_active: bool = True,
) -> tuple[Path, Path, Path | None]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    jp = root / TUNING_JSON
    mp = root / TUNING_MD
    jp.write_text(json.dumps(tuned.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# DeepSignal — 코인 임계값 자동 튜닝 (outcomes)",
        "",
        f"- Generated: {tuned.generated_at}",
        f"- Lookback: {tuned.lookback_days}일",
        f"- SELL 청산 샘플: {tuned.sample_sell_closed}",
        f"- BUY 체결 샘플: {tuned.sample_buy_executed}",
        "",
        "## 권장 임계값",
        "",
        f"- take_profit_pct: **{tuned.take_profit_pct:+.2f}%**",
        f"- stop_loss_pct: **{tuned.stop_loss_pct:+.2f}%**",
        f"- min_volume_ratio: **{tuned.min_volume_ratio:.2f}**",
        f"- take_profit_buffer_pct: {tuned.take_profit_buffer_pct:.2f}%p",
        f"- stop_loss_buffer_pct: {tuned.stop_loss_buffer_pct:.2f}%p",
        "",
    ]
    if tuned.sell_win_rate is not None:
        lines.append(f"- SELL 승률: {tuned.sell_win_rate * 100:.1f}% (평균 {tuned.sell_avg_return_pct:+.2f}%)")
    if tuned.buy_win_rate is not None:
        lines.append(f"- BUY 승률(체결 후): {tuned.buy_win_rate * 100:.1f}%")
    if tuned.warnings:
        lines.extend(["", "## 참고", ""])
        for w in tuned.warnings:
            lines.append(f"- {w}")
    lines.append("")
    mp.write_text("\n".join(lines), encoding="utf-8")

    active_path: Path | None = None
    if apply_active:
        active_path = root / ACTIVE_THRESHOLDS_JSON
        active_path.write_text(json.dumps(tuned.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return jp, mp, active_path


def run_tune_crypto_thresholds_from_outcomes(
    outcomes_db: str | Path,
    *,
    output_dir: str | Path = "outputs",
    lookback_days: int = 60,
    min_sell_samples: int = 3,
    min_buy_samples: int = 5,
) -> CryptoTunedThresholds:
    tuned = tune_crypto_thresholds_from_outcomes(
        outcomes_db,
        lookback_days=lookback_days,
        min_sell_samples=min_sell_samples,
        min_buy_samples=min_buy_samples,
    )
    write_crypto_threshold_tuning_reports(tuned, output_dir=output_dir, apply_active=True)
    return tuned


def load_active_crypto_thresholds(output_dir: str | Path) -> CryptoTunedThresholds | None:
    path = Path(output_dir) / ACTIVE_THRESHOLDS_JSON
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return CryptoTunedThresholds(
            take_profit_pct=float(data.get("take_profit_pct", _CRYPTO.take_profit_pct)),
            stop_loss_pct=float(data.get("stop_loss_pct", _CRYPTO.stop_loss_pct)),
            min_volume_ratio=float(data.get("min_volume_ratio", _CRYPTO.min_volume_ratio)),
            take_profit_buffer_pct=float(
                data.get("take_profit_buffer_pct", _CRYPTO.take_profit_buffer_pct)
            ),
            stop_loss_buffer_pct=float(data.get("stop_loss_buffer_pct", _CRYPTO.stop_loss_buffer_pct)),
            generated_at=str(data.get("generated_at", "")),
            lookback_days=int(data.get("lookback_days", 60)),
            sample_sell_closed=int(data.get("sample_sell_closed", 0)),
            sample_buy_executed=int(data.get("sample_buy_executed", 0)),
            sell_win_rate=_float(data.get("sell_win_rate")),
            sell_avg_return_pct=_float(data.get("sell_avg_return_pct")),
            buy_win_rate=_float(data.get("buy_win_rate")),
            warnings=list(data.get("warnings") or []),
        )
    except (TypeError, ValueError):
        return None


def reset_scalping_active_thresholds(output_dir: str | Path) -> Path:
    """Write CRYPTO_ACTIVE_THRESHOLDS.json with 단타 기본값 (TP 2% / SL -1.5% / vol 0.3)."""
    tuned = CryptoTunedThresholds(
        take_profit_pct=float(_CRYPTO.take_profit_pct),
        stop_loss_pct=float(_CRYPTO.stop_loss_pct),
        min_volume_ratio=float(_CRYPTO.min_volume_ratio),
        take_profit_buffer_pct=float(_CRYPTO.take_profit_buffer_pct),
        stop_loss_buffer_pct=float(_CRYPTO.stop_loss_buffer_pct),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        lookback_days=0,
        sample_sell_closed=0,
        sample_buy_executed=0,
        sell_win_rate=None,
        sell_avg_return_pct=None,
        buy_win_rate=None,
        warnings=["scalping_mode_reset"],
    )
    _, _, active = write_crypto_threshold_tuning_reports(tuned, output_dir=output_dir, apply_active=True)
    assert active is not None
    return active


def apply_active_thresholds_to_runner(cfg: Any, output_dir: str | Path) -> bool:
    """Mutate CryptoAutoRunnerConfig-like object from CRYPTO_ACTIVE_THRESHOLDS.json."""
    if bool(getattr(_CRYPTO, "scalping_mode", True)):
        cfg.take_profit_pct = float(_CRYPTO.take_profit_pct)
        cfg.stop_loss_pct = float(_CRYPTO.stop_loss_pct)
        cfg.take_profit_buffer_pct = float(_CRYPTO.take_profit_buffer_pct)
        cfg.stop_loss_buffer_pct = float(_CRYPTO.stop_loss_buffer_pct)
        tuned = load_active_crypto_thresholds(output_dir)
        if tuned is None:
            cfg.min_volume_ratio = float(_CRYPTO.min_volume_ratio)
            return False
        mvr_cap = float(getattr(_CRYPTO, "outcome_tune_max_volume_ratio", 0.45))
        cfg.min_volume_ratio = min(float(tuned.min_volume_ratio), mvr_cap)
        if not bool(getattr(_CRYPTO, "outcome_tune_apply_tp_sl", False)):
            return True
    tuned = load_active_crypto_thresholds(output_dir)
    if tuned is None:
        return False
    cfg.take_profit_pct = tuned.take_profit_pct
    cfg.stop_loss_pct = tuned.stop_loss_pct
    cfg.min_volume_ratio = tuned.min_volume_ratio
    cfg.take_profit_buffer_pct = tuned.take_profit_buffer_pct
    cfg.stop_loss_buffer_pct = tuned.stop_loss_buffer_pct
    return True
