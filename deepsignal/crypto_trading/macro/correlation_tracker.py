"""심볼 간 1분봉 수익률 상관관계 추적기.

매 score_and_log 호출 시 update() → sync_ratio / mean_correlation 을 MacroGuard 가 읽는다.

Layer 1 — 동시 급변동:
    추적 심볼 중 MACRO_RET_MIN(기본 0.3%) 이상 같은 방향으로 움직이는 비율.

Layer 2 — 롤링 상관계수:
    최근 N봉(기본 10) 수익률 행렬의 페어 상관계수 평균.
"""

from __future__ import annotations

import collections
import time
from typing import Any

import numpy as np

_RET_WINDOW = 10    # 상관계수 계산 윈도우 (봉 수)
_MIN_SYMBOLS = 5    # 통계에 필요한 최소 심볼 수
_RET_THRESHOLD = float(__import__("os").getenv("MACRO_RET_MIN", "0.003"))  # 0.3 %
_CACHE_TTL = 1.0    # 동일 사이클 내 재계산 억제 (초)


class CorrelationTracker:
    """심볼별 1분봉 종가를 받아 동시 급변동 비율과 평균 페어 상관계수를 제공."""

    def __init__(self, ret_window: int = _RET_WINDOW) -> None:
        self._window = ret_window
        # symbol -> deque[(ts_ms, close)]
        self._prices: dict[str, collections.deque[tuple[int, float]]] = {}
        # symbol -> deque[float]  (1m 수익률)
        self._returns: dict[str, collections.deque[float]] = {}
        # 캐시
        self._cache_ts: float = 0.0
        self._cache_sync: float = 0.0
        self._cache_corr: float = 0.0
        self._cache_movers: list[dict[str, Any]] = []

    # ── 퍼블릭 ──────────────────────────────────────────────────────

    def update(self, symbol: str, close: float, ts_ms: int) -> None:
        """새 봉 종가 수신. 직전 종가 대비 수익률을 계산해 누적."""
        if close <= 0:
            return
        if symbol not in self._prices:
            self._prices[symbol] = collections.deque(maxlen=self._window + 2)
            self._returns[symbol] = collections.deque(maxlen=self._window)

        prices = self._prices[symbol]
        if prices:
            last_close = prices[-1][1]
            if last_close > 0:
                self._returns[symbol].append((close - last_close) / last_close)

        prices.append((ts_ms, close))
        self._cache_ts = 0.0  # 캐시 무효화

    def sync_ratio(self) -> float:
        """최근 1분봉에서 같은 방향으로 ≥ 0.3% 움직인 심볼 비율 (0~1)."""
        self._refresh()
        return self._cache_sync

    def mean_correlation(self) -> float:
        """최근 N봉 수익률 페어 상관계수 평균 (-1~1)."""
        self._refresh()
        return self._cache_corr

    def top_movers(self) -> list[dict[str, Any]]:
        """절댓값 최근 1분 수익률 상위 10개 심볼."""
        self._refresh()
        return self._cache_movers

    def snapshot(self) -> dict[str, Any]:
        """대시보드 API용 직렬화."""
        return {
            "sync_ratio": round(self.sync_ratio(), 4),
            "mean_correlation": round(self.mean_correlation(), 4),
            "top_movers": self.top_movers(),
            "n_symbols": len(self._returns),
        }

    # ── 내부 ────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._cache_ts < _CACHE_TTL:
            return
        self._cache_ts = now

        # Layer 1 은 마지막 1개 수익률만 필요, Layer 2 는 3개 이상 필요
        rets = {s: list(r) for s, r in self._returns.items() if len(r) >= 1}
        if len(rets) < _MIN_SYMBOLS:
            self._cache_sync = 0.0
            self._cache_corr = 0.0
            self._cache_movers = []
            return

        # ── Layer 1: 동시 급변동 비율 ──────────────────────────────
        last_rets = {s: r[-1] for s, r in rets.items()}
        up = sum(1 for r in last_rets.values() if r >= _RET_THRESHOLD)
        dn = sum(1 for r in last_rets.values() if r <= -_RET_THRESHOLD)
        self._cache_sync = max(up, dn) / len(last_rets)

        # top movers (절댓값 기준 상위 10)
        self._cache_movers = [
            {"symbol": s, "ret_1m": round(r, 6)}
            for s, r in sorted(last_rets.items(), key=lambda x: abs(x[1]), reverse=True)[:10]
        ]

        # ── Layer 2: 롤링 상관계수 ─────────────────────────────────
        min_len = min(len(r) for r in rets.values())
        if min_len < 3:
            self._cache_corr = 0.0
            return

        matrix = np.array([r[-min_len:] for r in rets.values()])  # (n_sym, window)
        try:
            corr_mat = np.corrcoef(matrix)                         # (n_sym, n_sym)
            n = corr_mat.shape[0]
            idx_u, idx_v = np.triu_indices(n, k=1)
            vals = corr_mat[idx_u, idx_v]
            valid = vals[~np.isnan(vals)]
            self._cache_corr = float(np.mean(valid)) if len(valid) else 0.0
        except Exception:
            self._cache_corr = 0.0
