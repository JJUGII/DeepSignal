"""KIS 초당 요청 한도(EGW00201) 방지용 토큰 버킷 레이트리미터 (P1).

고속 회전 시 여러 호출이 몰리면 KIS가 '초당 거래건수 초과'로 거절한다.
acquire()가 초당 max_per_sec 건으로 호출을 직렬화/지연시켜 한도를 지킨다. 스레드 안전.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, max_per_sec: float = 4.0) -> None:
        self._min_interval = 1.0 / max(0.1, float(max_per_sec))
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> float:
        """다음 호출 슬롯까지 대기(필요시 sleep). 대기한 초를 반환."""
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            else:
                wait = 0.0
            self._next_at = max(now, self._next_at) + self._min_interval
            return max(0.0, wait)


# KIS 공용 리미터 (조회 4건/초 보수값 — 실전 한도보다 여유)
_KIS_LIMITER = RateLimiter(max_per_sec=4.0)


def kis_acquire() -> float:
    return _KIS_LIMITER.acquire()
