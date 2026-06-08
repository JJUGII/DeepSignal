"""파이프라인 상태 영속성 — 재시작 시 0초 워밍업.

흐름:
    종료 시: save_snapshot()  →  state_snapshot.json (경량 상태)
    시작 시: load_and_warmup() → bars/*.jsonl 읽기 → FeatureEngine 복원
             → delta_needed 반환 → 누락 봉만 REST fetch

bars/*.jsonl 은 StreamPersistence.append_closed_bar() 가 이미 쌓고 있으므로
별도 저장 없이 그대로 재활용한다.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from deepsignal.market_data.feature_engine.engine import FeatureEngine
    from deepsignal.crypto_trading.macro.correlation_tracker import CorrelationTracker

logger = logging.getLogger(__name__)

# 이 시간(시간) 초과 시 bars가 있어도 풀 REST 워밍업으로 폴백
MAX_STALE_HOURS = 2.0

# 심볼당 읽어들일 봉 수 (실제 필요 + 여유분)
_BARS_LOAD: dict[int, int] = {1: 125, 3: 45, 15: 25}

# delta fetch 최대 허용 봉 수 (이 이상이면 폴백)
_DELTA_MAX: dict[int, int] = {1: 60, 3: 20, 15: 10}

_SNAPSHOT_FILE = "state_snapshot.json"


# ════════════════════════════════════════════════════════════════
# 결과 데이터클래스
# ════════════════════════════════════════════════════════════════

@dataclass
class WarmupResult:
    """load_and_warmup() 반환값."""
    success: bool
    symbols_loaded: list[str]          # 복원 성공
    symbols_failed: list[str]          # bars 파일 없거나 너무 오래됨
    # 심볼 → timeframe_min → 추가로 fetch 해야 할 봉 수
    delta_needed: dict[str, dict[int, int]] = field(default_factory=dict)
    saved_at_ms: int = 0
    elapsed_ms: int = 0

    def summary(self) -> str:
        ok  = len(self.symbols_loaded)
        fail = len(self.symbols_failed)
        deltas = sum(sum(v.values()) for v in self.delta_needed.values())
        age_s  = (int(time.time() * 1000) - self.saved_at_ms) // 1000 if self.saved_at_ms else 0
        return (
            f"복원 {ok}심볼 / 실패 {fail}심볼 | "
            f"delta {deltas}봉 | 저장 {age_s}초 전 | 소요 {self.elapsed_ms}ms"
        )


# ════════════════════════════════════════════════════════════════
# 메인 클래스
# ════════════════════════════════════════════════════════════════

class StreamStateManager:
    """bars/*.jsonl + state_snapshot.json 기반 파이프라인 상태 저장/복원."""

    def __init__(self, max_stale_hours: float = MAX_STALE_HOURS) -> None:
        self._max_stale_ms = int(max_stale_hours * 3600 * 1000)

    # ── 공개 API ────────────────────────────────────────────────

    def save_snapshot(
        self,
        output_dir: Path,
        eng: "FeatureEngine",
        corr_tracker: "CorrelationTracker | None" = None,
    ) -> Path:
        """파이프라인 종료 시 호출. 경량 상태를 state_snapshot.json 에 저장."""
        now_ms = int(time.time() * 1000)

        symbols_state: dict[str, dict[str, Any]] = {}
        for sym, st in eng._symbols.items():
            entry: dict[str, Any] = {
                "last_price":       st.last_price,
                "funding_rate":     None if math.isnan(st.funding_rate) else st.funding_rate,
                "oi_change_pct":    None if math.isnan(st.oi_change_pct) else st.oi_change_pct,
                "long_short_ratio": None if math.isnan(st.long_short_ratio) else st.long_short_ratio,
            }
            symbols_state[sym] = entry

        market_state: dict[str, Any] = {
            "fear_greed": (
                None if math.isnan(eng._market.fear_greed)
                else eng._market.fear_greed
            ),
            "btc_closes_1m": list(eng._market.btc_closes_1m),
        }

        corr_state: dict[str, list[list]] = {}
        if corr_tracker is not None:
            for sym, prices in corr_tracker._prices.items():
                corr_state[sym] = [[ts, close] for ts, close in prices]

        payload = {
            "saved_at_ms":   now_ms,
            "saved_at_kst":  _kst_iso(now_ms),
            "symbols":       symbols_state,
            "market":        market_state,
            "correlation":   corr_state,
        }

        path = output_dir / _SNAPSHOT_FILE
        _atomic_write(path, json.dumps(payload, ensure_ascii=False))
        logger.info("상태 스냅샷 저장: %s (%d심볼)", path, len(symbols_state))
        return path

    def load_and_warmup(
        self,
        output_dir: Path,
        eng: "FeatureEngine",
        corr_tracker: "CorrelationTracker | None" = None,
        symbols: list[str] | None = None,
    ) -> WarmupResult:
        """bars/*.jsonl 을 읽어 FeatureEngine 복원. delta_needed 반환.

        Args:
            symbols: 복원할 심볼 목록. None 이면 snapshot 의 symbols 키에서 추론.
                     파이프라인에서 호출 시 self.symbols 를 넘기는 것을 권장.
        """
        t0 = time.monotonic()
        bars_dir = output_dir / "bars"

        # ── state_snapshot.json 유효성 체크 (만료 확인용) ──────
        snap = self._load_snapshot(output_dir)
        now_ms = int(time.time() * 1000)

        if snap is not None:
            saved_at_ms: int = snap.get("saved_at_ms", 0)
            if now_ms - saved_at_ms > self._max_stale_ms:
                age_h = (now_ms - saved_at_ms) / 3_600_000
                logger.info(
                    "상태 스냅샷 만료 (%.1f시간 경과, 한도 %.1f시간) — REST 풀 워밍업",
                    age_h, self._max_stale_ms / 3_600_000,
                )
                return WarmupResult(success=False, symbols_loaded=[], symbols_failed=[])
        else:
            saved_at_ms = 0

        # ── 복원할 심볼 목록 결정 ───────────────────────────────
        # 우선순위: 인수 > snapshot.symbols > bars/ 파일 자동 감지
        if symbols:
            target_symbols = [s.upper() for s in symbols]
        elif snap and snap.get("symbols"):
            target_symbols = list(snap["symbols"].keys())
        else:
            # bars/ 파일에서 자동 감지 (1m 파일 기준, macOS 숨김파일 제외)
            target_symbols = [
                p.stem.replace("_1m", "").upper()
                for p in bars_dir.glob("*_1m.jsonl")
                if not p.name.startswith(".")
            ]

        if not target_symbols:
            return WarmupResult(success=False, symbols_loaded=[], symbols_failed=[])

        loaded: list[str] = []
        failed: list[str] = []
        delta_needed: dict[str, dict[int, int]] = {}

        for sym in target_symbols:
            sym_delta, ok = self._restore_symbol(sym, bars_dir, eng, now_ms)
            if ok:
                loaded.append(sym)
                delta_needed[sym] = sym_delta
            else:
                failed.append(sym)

        if not loaded:
            return WarmupResult(success=False, symbols_loaded=[], symbols_failed=failed)

        # ── 경량 상태 주입 (last_price, funding 등) ─────────────
        self._restore_light_state(snap, eng)

        # ── CorrelationTracker 복원 ─────────────────────────────
        if corr_tracker is not None:
            self._restore_correlation(snap, corr_tracker)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        result = WarmupResult(
            success=True,
            symbols_loaded=loaded,
            symbols_failed=failed,
            delta_needed=delta_needed,
            saved_at_ms=saved_at_ms,
            elapsed_ms=elapsed_ms,
        )
        logger.info("✅ 상태 복원 완료 — %s", result.summary())
        return result

    # ── 내부 ────────────────────────────────────────────────────

    def _load_snapshot(self, output_dir: Path) -> dict[str, Any] | None:
        path = output_dir / _SNAPSHOT_FILE
        if not path.is_file():
            logger.debug("state_snapshot.json 없음 — 첫 실행 또는 미저장")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("state_snapshot.json 파싱 실패: %s", exc)
            return None

    def _restore_symbol(
        self,
        sym: str,
        bars_dir: Path,
        eng: "FeatureEngine",
        now_ms: int,
    ) -> tuple[dict[int, int], bool]:
        """bars/*.jsonl 에서 심볼 봉 로드 → FeatureEngine 주입.

        반환: (delta_needed_per_tf, success)
        """
        from deepsignal.market_data.binance_stream.models import OhlcvBar

        delta: dict[int, int] = {}
        any_loaded = False

        for tf_min, limit in _BARS_LOAD.items():
            # JSONL 파일 경로 (1m → BTCUSDT_1m.jsonl)
            path = bars_dir / f"{sym}_{tf_min}m.jsonl"
            if not path.is_file():
                delta[tf_min] = limit  # 전부 fetch 필요
                continue

            bars = _read_tail_jsonl(path, limit)
            if not bars:
                delta[tf_min] = limit
                continue

            for d in bars:
                try:
                    bar = OhlcvBar.from_dict(d)
                    eng.on_bar(bar, is_historical=True)
                except Exception as exc:
                    logger.debug("bar 복원 실패 [%s %dm]: %s", sym, tf_min, exc)

            # delta 계산: 마지막 봉 이후 경과된 봉 수
            last_ts = bars[-1].get("open_ts_ms", 0)
            interval_ms = tf_min * 60_000
            missing = max(0, (now_ms - last_ts) // interval_ms - 1)

            if missing > _DELTA_MAX[tf_min]:
                # delta가 너무 크면 이 timeframe만 풀 fetch
                delta[tf_min] = _BARS_LOAD[tf_min]
            elif missing > 0:
                delta[tf_min] = int(missing) + 2  # 여유 2봉
            # missing == 0 이면 delta 없음

            any_loaded = True

        return delta, any_loaded

    def _restore_light_state(
        self,
        snap: dict[str, Any],
        eng: "FeatureEngine",
    ) -> None:
        """last_price, funding_rate, OI, L/S 비율 주입."""
        import math as _math
        for sym, info in snap.get("symbols", {}).items():
            try:
                st = eng._state(sym)
                if info.get("last_price"):
                    st.last_price = float(info["last_price"])
                if info.get("funding_rate") is not None:
                    st.funding_rate = float(info["funding_rate"])
                if info.get("oi_change_pct") is not None:
                    st.oi_change_pct = float(info["oi_change_pct"])
                if info.get("long_short_ratio") is not None:
                    st.long_short_ratio = float(info["long_short_ratio"])
            except Exception as exc:
                logger.debug("light state 주입 실패 [%s]: %s", sym, exc)

        # BTC 1m closes (시장 상태용)
        btc_closes = snap.get("market", {}).get("btc_closes_1m", [])
        for c in btc_closes:
            try:
                eng._market.btc_closes_1m.append(float(c))
            except Exception:
                pass

    def _restore_correlation(
        self,
        snap: dict[str, Any],
        tracker: "CorrelationTracker",
    ) -> None:
        """CorrelationTracker 가격 히스토리 복원."""
        import collections
        for sym, price_pairs in snap.get("correlation", {}).items():
            for pair in price_pairs:
                try:
                    ts_ms, close = int(pair[0]), float(pair[1])
                    tracker.update(sym, close, ts_ms)
                except Exception:
                    pass
        restored = len(snap.get("correlation", {}))
        if restored:
            logger.debug("CorrelationTracker 복원: %d심볼", restored)


# ════════════════════════════════════════════════════════════════
# 헬퍼
# ════════════════════════════════════════════════════════════════

def _read_tail_jsonl(path: Path, n: int) -> list[dict[str, Any]]:
    """JSONL 파일에서 마지막 n개 라인을 읽어 파싱."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        result = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return result
    except Exception as exc:
        logger.debug("JSONL 읽기 실패 [%s]: %s", path, exc)
        return []


def _atomic_write(path: Path, text: str) -> None:
    """임시 파일에 먼저 쓰고 rename — 중간 corruption 방지."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _kst_iso(ts_ms: int) -> str:
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(ts_ms / 1000, tz=kst).strftime("%Y-%m-%d %H:%M:%S")
