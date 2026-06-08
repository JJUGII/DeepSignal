"""GSQS 신호 기록 + 사후 평가 + 자동 임계값 결정 시스템.

흐름:
    1. 파이프라인이 BUY_CANDIDATE 이상 신호를 감지 → log_signal() 호출
    2. 1m/3m/5m/15m 경과 후 가격을 check_outcomes() 로 기록
    3. 충분한 데이터 후 auto_threshold() / WinRateStats 로 최적 진입 기준 산출

파일 구조 (outputs/signal_log.jsonl):
    {"signal_id": "BTCUSDT_1748430000000", "ts_ms": ..., "symbol": ...,
     "score": 74.1, "decision": "BUY_CANDIDATE", "entry_price": 73465.0,
     "sub_scores": {...}, "ret_1m": 0.003, "ret_3m": -0.001, ...
     "outcome_complete": true}
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ── 결과 확인 시간대 (분) ──────────────────────────────────────────────
OUTCOME_HORIZONS = (1, 3, 5, 15)

# 최소 신호 수 (통계 유의미 기준)
MIN_SIGNALS_FOR_STATS = 30


# ══════════════════════════════════════════════════════════════════
# 데이터 클래스
# ══════════════════════════════════════════════════════════════════

@dataclass
class SignalRecord:
    """하나의 매수 신호 기록."""
    signal_id:   str            # "{symbol}_{ts_ms}"
    ts_ms:       int            # 신호 발생 시각 (Unix ms)
    symbol:      str
    score:       float          # GSQS 최종 점수 (0~100)
    decision:    str            # BUY_CANDIDATE / STRONG_BUY
    entry_price: float          # 진입 가격 (신호 발생 시점)
    sub_scores:  dict[str, float] = field(default_factory=dict)

    # 매크로 이벤트 중 발생 여부
    macro_risk: bool = False

    # 사후 결과 (시간 경과 후 기록)
    ret_1m:  float = float("nan")
    ret_3m:  float = float("nan")
    ret_5m:  float = float("nan")
    ret_15m: float = float("nan")
    outcome_complete: bool = False

    def win(self, horizon: int = 5) -> bool | None:
        """horizon분 후 수익률이 양수면 True (승리)."""
        ret = getattr(self, f"ret_{horizon}m", float("nan"))
        if math.isnan(ret):
            return None
        return ret > 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # nan → null (JSON 직렬화)
        for k in ("ret_1m", "ret_3m", "ret_5m", "ret_15m"):
            if math.isnan(d[k]):
                d[k] = None
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "SignalRecord":
        d2 = dict(d)
        for k in ("ret_1m", "ret_3m", "ret_5m", "ret_15m"):
            if d2.get(k) is None:
                d2[k] = float("nan")
        d2.setdefault("macro_risk", False)  # 구버전 레코드 호환
        return SignalRecord(**d2)


@dataclass
class WinRateStats:
    """점수 구간별 승률 통계."""
    band:        str    # "70-80", "80-100" 등
    n_signals:   int
    win_rate_1m: float
    win_rate_3m: float
    win_rate_5m: float
    win_rate_15m: float

    def is_reliable(self) -> bool:
        return self.n_signals >= MIN_SIGNALS_FOR_STATS

    def best_horizon(self) -> int:
        """가장 높은 승률을 보이는 시간대 반환."""
        rates = {
            1: self.win_rate_1m,
            3: self.win_rate_3m,
            5: self.win_rate_5m,
            15: self.win_rate_15m,
        }
        return max(rates, key=lambda h: rates[h])


# ══════════════════════════════════════════════════════════════════
# 메인 클래스
# ══════════════════════════════════════════════════════════════════

class SignalLogger:
    """신호 기록·사후평가·자동 임계값 결정 통합 클래스.

    사용 예::

        logger = SignalLogger(Path("outputs"))
        logger.log_signal(score, price=73465.0, ts_ms=now_ms)
        ...
        # 매 flush 시 호출
        logger.check_outcomes(current_prices={"BTCUSDT": 73500.0}, now_ms=now_ms)
        ...
        stats = logger.win_rate_stats()
        threshold = logger.auto_threshold()
    """

    def __init__(self, output_dir: Path | str) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.output_dir / "signal_log.jsonl"

        # 메모리 내 pending 레코드 (아직 결과 미기록)
        # key: signal_id  value: SignalRecord
        self._pending: dict[str, SignalRecord] = {}

        # 결과 확인 대기 목록: {signal_id: {horizon_min: deadline_ms}}
        self._pending_deadlines: dict[str, dict[int, int]] = {}

        self._load_pending()

    # ── 신호 기록 ────────────────────────────────────────────────

    def log_signal(
        self,
        score_obj: Any,         # ScalpingScore 인스턴스
        price: float,
        ts_ms: int | None = None,
        macro_risk: bool = False,
    ) -> str:
        """매수 신호를 기록하고 signal_id를 반환."""
        ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
        sid = f"{score_obj.symbol}_{ts}"

        rec = SignalRecord(
            signal_id=sid,
            ts_ms=ts,
            symbol=score_obj.symbol,
            score=float(score_obj.score),
            decision=score_obj.decision,
            entry_price=float(price),
            sub_scores=dict(score_obj.sub_scores),
            macro_risk=macro_risk,
        )
        self._pending[sid] = rec

        # 각 시간대별 결과 확인 마감시각 설정
        self._pending_deadlines[sid] = {
            h: ts + h * 60_000 for h in OUTCOME_HORIZONS
        }

        self._append_jsonl(rec.to_dict())
        return sid

    # ── 사후 결과 기록 ────────────────────────────────────────────

    def check_outcomes(
        self,
        current_prices: dict[str, float],
        now_ms: int | None = None,
    ) -> int:
        """현재 가격을 기반으로 pending 신호의 결과를 기록. 완료된 건수 반환."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        completed: list[str] = []

        updated_records: list[SignalRecord] = []

        for sid, rec in list(self._pending.items()):
            price_now = current_prices.get(rec.symbol)
            if price_now is None or price_now <= 0:
                continue

            deadlines = self._pending_deadlines.get(sid, {})
            changed = False

            for h in OUTCOME_HORIZONS:
                if h not in deadlines:
                    continue
                if now < deadlines[h]:
                    continue
                # 해당 시간대 결과 기록
                ret = (price_now - rec.entry_price) / rec.entry_price
                setattr(rec, f"ret_{h}m", ret)
                del deadlines[h]
                changed = True

            if changed:
                if not deadlines:  # 모든 시간대 완료
                    rec.outcome_complete = True
                    completed.append(sid)
                updated_records.append(rec)

        # 변경된 레코드를 한 번에 파일 업데이트 (O(N) → O(1) 파일 I/O)
        if updated_records:
            self._batch_overwrite(updated_records)

        for sid in completed:
            del self._pending[sid]
            self._pending_deadlines.pop(sid, None)

        return len(completed)

    # ── 통계 / 자동 임계값 ──────────────────────────────────────

    def win_rate_stats(
        self,
        horizon: int = 5,
    ) -> list[WinRateStats]:
        """점수 구간(60~70, 70~80, 80~100)별 승률 통계 반환."""
        bands = [(60, 70), (70, 75), (75, 80), (80, 100)]
        records = self._load_all_complete()
        result: list[WinRateStats] = []

        for lo, hi in bands:
            subset = [r for r in records if lo <= r.score < hi]
            if not subset:
                continue

            def _wr(h: int) -> float:
                wins = [r for r in subset if not math.isnan(getattr(r, f"ret_{h}m"))]
                if not wins:
                    return float("nan")
                return sum(1 for r in wins if getattr(r, f"ret_{h}m") > 0) / len(wins)

            result.append(WinRateStats(
                band=f"{lo}-{hi}",
                n_signals=len(subset),
                win_rate_1m=_wr(1),
                win_rate_3m=_wr(3),
                win_rate_5m=_wr(5),
                win_rate_15m=_wr(15),
            ))

        return result

    def auto_threshold(self, horizon: int = 5, min_win_rate: float = 0.55) -> float:
        """데이터 기반 자동 임계값 결정.

        신호 수가 MIN_SIGNALS_FOR_STATS 미만이면 기본값(70.0) 반환.
        충분한 데이터가 쌓이면 실제 승률 ≥ min_win_rate를 만족하는
        최저 점수 구간의 하한값을 반환.

        Returns:
            float: 권장 최소 진입 점수 (0~100)
        """
        stats = self.win_rate_stats(horizon)
        reliable = [s for s in stats if s.is_reliable()]

        if not reliable:
            return 70.0  # 데이터 부족 — 기본값

        target_attr = f"win_rate_{horizon}m"
        passing = [
            s for s in reliable
            if not math.isnan(getattr(s, target_attr))
            and getattr(s, target_attr) >= min_win_rate
        ]

        if not passing:
            # 기준을 만족하는 구간 없음 → 가장 높은 승률 구간 사용
            best = max(reliable, key=lambda s: getattr(s, target_attr, 0.0))
            band_lo = int(best.band.split("-")[0])
            return float(band_lo)

        # 승률 기준을 만족하는 최저 구간의 하한 반환 (더 많은 신호 포착)
        band_lo = min(int(s.band.split("-")[0]) for s in passing)
        return float(band_lo)

    def summary(self, horizon: int = 5) -> dict[str, Any]:
        """현재 상태 요약 딕셔너리."""
        records = self._load_all_complete()
        stats = self.win_rate_stats(horizon)
        threshold = self.auto_threshold(horizon)

        total_signals = len(records)
        overall_wr = float("nan")
        if records:
            valid = [r for r in records if not math.isnan(getattr(r, f"ret_{horizon}m"))]
            if valid:
                overall_wr = sum(1 for r in valid if getattr(r, f"ret_{horizon}m") > 0) / len(valid)

        return {
            "total_signals":    total_signals,
            "pending_signals":  len(self._pending),
            "overall_win_rate": round(overall_wr, 4) if not math.isnan(overall_wr) else None,
            "auto_threshold":   threshold,
            "horizon_minutes":  horizon,
            "bands": [
                {
                    "band": s.band,
                    "n": s.n_signals,
                    f"win_rate_{horizon}m": round(getattr(s, f"win_rate_{horizon}m"), 4)
                    if not math.isnan(getattr(s, f"win_rate_{horizon}m")) else None,
                    "reliable": s.is_reliable(),
                }
                for s in stats
            ],
            "data_sufficient": total_signals >= MIN_SIGNALS_FOR_STATS,
        }

    # ── 내부 헬퍼 ────────────────────────────────────────────────

    def _append_jsonl(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _batch_overwrite(self, records: list["SignalRecord"]) -> None:
        """여러 레코드를 한 번의 파일 I/O로 업데이트."""
        if not self.log_path.exists():
            return
        update_map = {r.signal_id: r for r in records}
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        new_lines: list[str] = []
        for line in lines:
            try:
                d = json.loads(line)
                sid = d.get("signal_id")
                if sid and sid in update_map:
                    new_lines.append(json.dumps(update_map.pop(sid).to_dict(), ensure_ascii=False))
                    continue
            except json.JSONDecodeError:
                pass
            new_lines.append(line)
        # 파일에 없던 레코드는 append
        for rec in update_map.values():
            new_lines.append(json.dumps(rec.to_dict(), ensure_ascii=False))
        self.log_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def _overwrite_record(self, rec: SignalRecord) -> None:
        """단일 레코드 업데이트 (하위 호환 유지)."""
        self._batch_overwrite([rec])

    def _load_all_complete(self) -> list[SignalRecord]:
        """결과까지 모두 기록된 완성 레코드 로드."""
        if not self.log_path.exists():
            return []
        records: list[SignalRecord] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("outcome_complete"):
                    records.append(SignalRecord.from_dict(d))
            except (json.JSONDecodeError, TypeError):
                continue
        return records

    def _load_pending(self) -> None:
        """재시작 시 미완성 레코드를 메모리로 복원."""
        if not self.log_path.exists():
            return
        now_ms = int(time.time() * 1000)
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("outcome_complete"):
                    continue
                rec = SignalRecord.from_dict(d)
                # 15분보다 오래된 미완성 레코드는 복원 스킵
                if now_ms - rec.ts_ms > 15 * 60_000 * 2:
                    continue
                self._pending[rec.signal_id] = rec
                self._pending_deadlines[rec.signal_id] = {
                    h: rec.ts_ms + h * 60_000
                    for h in OUTCOME_HORIZONS
                    if math.isnan(getattr(rec, f"ret_{h}m"))
                }
            except (json.JSONDecodeError, TypeError):
                continue
