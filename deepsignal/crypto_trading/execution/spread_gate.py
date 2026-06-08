"""
spread_gate.py — 코인별 동적 스프레드 게이트 (Phase 2).

현재 고정 0.15% 기준 대신, 코인별 최근 관측된 스프레드의 중앙값 × 배수를
임계값으로 사용한다. 유동성이 낮은 코인(NEAR, RENDER 등)은 원래 스프레드가
넓으므로 넉넉하게 허용하고, BTC처럼 타이트한 코인은 엄격하게 유지.

ENV 플래그:
  CRYPTO_DYNAMIC_SPREAD_ENABLED   (기본: false) — on/off
  CRYPTO_SPREAD_MULTIPLIER        (기본: 1.5)   — median × 배수
  CRYPTO_SPREAD_FALLBACK_PCT      (기본: 0.30)  — 데이터 없는 신규 코인 기본값
  CRYPTO_SPREAD_HARD_MAX_PCT      (기본: 0.80)  — 동적 임계값 절대 상한
  CRYPTO_SPREAD_HARD_MIN_PCT      (기본: 0.10)  — 동적 임계값 절대 하한
  CRYPTO_SPREAD_HISTORY_SIZE      (기본: 500)   — 코인당 보관 관측 수

저장 경로: outputs/CRYPTO_SPREAD_HISTORY.json
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import threading
from collections import deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HISTORY_FILE = "CRYPTO_SPREAD_HISTORY.json"


# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

def _env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, "")
    if not v:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key) or default)
    except (TypeError, ValueError):
        return default


# ──────────────────────────────────────────────
# 동적 스프레드 게이트
# ──────────────────────────────────────────────

class DynamicSpreadGate:
    """
    코인별 스프레드 관측 히스토리를 관리하고,
    동적 임계값을 계산하는 스레드 세이프 클래스.
    """

    def __init__(
        self,
        *,
        multiplier: float = 1.5,
        fallback_pct: float = 0.30,
        hard_max_pct: float = 0.80,
        hard_min_pct: float = 0.10,
        history_size: int = 500,
    ) -> None:
        self._lock = threading.Lock()
        self._history: dict[str, deque[float]] = {}
        self.multiplier   = multiplier
        self.fallback_pct = fallback_pct
        self.hard_max_pct = hard_max_pct
        self.hard_min_pct = hard_min_pct
        self.history_size = history_size

    # ── 관측 기록 ──────────────────────────────

    def record(self, market: str, spread_pct: float) -> None:
        """호가창 조회 시마다 스프레드 관측값 기록."""
        if spread_pct < 0 or spread_pct > 50.0:
            return  # 비정상 값 무시
        m = market.upper()
        with self._lock:
            if m not in self._history:
                self._history[m] = deque(maxlen=self.history_size)
            self._history[m].append(spread_pct)

    # ── 임계값 계산 ────────────────────────────

    def threshold(self, market: str) -> float:
        """
        코인별 동적 스프레드 임계값 반환.
        데이터 없거나 너무 적으면 fallback_pct 사용.
        """
        m = market.upper()
        with self._lock:
            hist = list(self._history.get(m, []))

        if len(hist) < 20:
            return self.fallback_pct

        median_spread = statistics.median(hist)
        dynamic = median_spread * self.multiplier
        # 절대 상·하한 클램프
        return max(self.hard_min_pct, min(self.hard_max_pct, dynamic))

    def sample_count(self, market: str) -> int:
        m = market.upper()
        with self._lock:
            return len(self._history.get(m, []))

    def market_stats(self) -> dict[str, dict[str, float]]:
        """UI/로그용: 코인별 현재 임계값 + 중앙값 + 샘플수."""
        with self._lock:
            markets = dict(self._history)
        result = {}
        for m, hist in markets.items():
            lst = list(hist)
            if not lst:
                continue
            med = statistics.median(lst)
            result[m] = {
                "samples": len(lst),
                "median_pct": round(med, 4),
                "threshold_pct": round(self.threshold(m), 4),
            }
        return result

    # ── 허용 여부 판정 ─────────────────────────

    def check(
        self, market: str, spread_pct: float
    ) -> tuple[bool, str]:
        """
        (allowed, reason) 반환.
        CRYPTO_DYNAMIC_SPREAD_ENABLED=false 면 항상 True 반환.
        """
        if not _env_bool("CRYPTO_DYNAMIC_SPREAD_ENABLED"):
            return True, "dynamic_spread_disabled(정적 엔진이 처리)"

        thr = self.threshold(market)
        if spread_pct <= thr:
            samples = self.sample_count(market)
            return True, f"spread_ok:{spread_pct:.3f}%<={thr:.3f}%(n={samples})"
        samples = self.sample_count(market)
        return False, f"spread_blocked:{spread_pct:.3f}%>{thr:.3f}%(n={samples})"

    # ── 영속성: 저장 / 불러오기 ────────────────

    def save(self, output_dir: str | Path) -> None:
        """히스토리를 JSON으로 저장 (key: market, value: 최근 N개 리스트)."""
        path = Path(output_dir) / _HISTORY_FILE
        with self._lock:
            data: dict[str, list[float]] = {
                m: list(dq) for m, dq in self._history.items()
            }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("DynamicSpreadGate.save 실패: %s", exc)

    @classmethod
    def load(
        cls,
        output_dir: str | Path,
        **kwargs: Any,
    ) -> "DynamicSpreadGate":
        """저장된 히스토리를 불러와 인스턴스 생성."""
        gate = cls(**kwargs)
        path = Path(output_dir) / _HISTORY_FILE
        if not path.is_file():
            return gate
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for market, values in raw.items():
                if isinstance(values, list):
                    dq: deque[float] = deque(
                        (float(v) for v in values if isinstance(v, (int, float))),
                        maxlen=gate.history_size,
                    )
                    gate._history[market.upper()] = dq
            logger.info(
                "DynamicSpreadGate: %d 코인 히스토리 로드 완료", len(gate._history)
            )
        except Exception as exc:
            logger.warning("DynamicSpreadGate.load 실패 (빈 상태로 시작): %s", exc)
        return gate


# ──────────────────────────────────────────────
# 모듈 레벨 싱글턴 (ws_runner 와 engine 공유)
# ──────────────────────────────────────────────

_singleton: DynamicSpreadGate | None = None
_singleton_lock = threading.Lock()


def get_spread_gate(output_dir: str | Path = "outputs") -> DynamicSpreadGate:
    """
    모듈 레벨 싱글턴 반환.
    최초 호출 시 output_dir에서 히스토리 로드.
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = DynamicSpreadGate.load(
                    output_dir,
                    multiplier=_env_float("CRYPTO_SPREAD_MULTIPLIER", 1.5),
                    fallback_pct=_env_float("CRYPTO_SPREAD_FALLBACK_PCT", 0.30),
                    hard_max_pct=_env_float("CRYPTO_SPREAD_HARD_MAX_PCT", 0.80),
                    hard_min_pct=_env_float("CRYPTO_SPREAD_HARD_MIN_PCT", 0.10),
                    history_size=_env_int("CRYPTO_SPREAD_HISTORY_SIZE", 500),
                )
    return _singleton
