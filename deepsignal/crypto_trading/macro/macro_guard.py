"""매크로 이벤트 게이팅.

CorrelationTracker 상태를 읽어 동시 급변동 / 상관계수 임계값 초과 시 active=True.
active 중 BUY 신호에 macro_risk=True 태그 → Telegram 경고 메시지 변형.

환경 변수:
    MACRO_SYNC_THRESHOLD  동시 급변동 심볼 비율 임계값  (기본 0.70 = 70%)
    MACRO_CORR_THRESHOLD  평균 페어 상관계수 임계값     (기본 0.85)
    MACRO_DECAY_MINUTES   이벤트 해제 대기 시간 (분)    (기본 5)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from deepsignal.crypto_trading.macro.correlation_tracker import CorrelationTracker

logger = logging.getLogger(__name__)

_SYNC_THRESHOLD = float(os.getenv("MACRO_SYNC_THRESHOLD", "0.70"))
_CORR_THRESHOLD = float(os.getenv("MACRO_CORR_THRESHOLD", "0.85"))
_DECAY_SECONDS  = float(os.getenv("MACRO_DECAY_MINUTES",  "5")) * 60.0
_ALERT_COOLDOWN = 60.0   # 같은 이벤트 내 Telegram 알림 최소 간격 (초)


class MacroGuard:
    """매크로 이벤트 감지 + 신호 게이팅 상태 관리."""

    def __init__(self) -> None:
        self._active: bool = False
        self._trigger_reason: str = ""
        self._active_since: float = 0.0      # monotonic (리셋용)
        self._active_since_ms: int = 0       # unix ms (API용)
        self._last_alert: float = 0.0        # 마지막 Telegram 발송 시각
        # 외부에서 주입: fn(reason, sync, corr, top_movers) -> None
        self._alert_cb: Callable[..., None] | None = None

    # ── 설정 ────────────────────────────────────────────────────────

    def set_alert_callback(self, cb: Callable[..., None]) -> None:
        """매크로 이벤트 시작 시 호출할 함수 등록 (ScalpSignalNotifier 연결용)."""
        self._alert_cb = cb

    # ── 상태 조회 ────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @property
    def trigger_reason(self) -> str:
        return self._trigger_reason

    def decay_remaining(self) -> float:
        """이벤트 해제까지 남은 초. 비활성이면 0."""
        if not self._active:
            return 0.0
        return max(0.0, _DECAY_SECONDS - (time.monotonic() - self._active_since))

    # ── 핵심 로직 ────────────────────────────────────────────────────

    def evaluate(self, tracker: "CorrelationTracker") -> None:
        """매 score_and_log 사이클 마지막에 호출. 상태를 갱신한다."""
        sync = tracker.sync_ratio()
        corr = tracker.mean_correlation()
        now  = time.monotonic()
        now_ms = int(time.time() * 1000)

        # ── 트리거 판단 ─────────────────────────────────────────────
        if sync >= _SYNC_THRESHOLD:
            reason = f"SYNC:{sync:.0%}"
            self._arm(reason, now, now_ms, tracker, sync, corr)
        elif corr >= _CORR_THRESHOLD:
            reason = f"CORR:{corr:.2f}"
            self._arm(reason, now, now_ms, tracker, sync, corr)
        else:
            # 트리거 없음 — 이미 활성이면 decay 체크
            if self._active and (now - self._active_since) >= _DECAY_SECONDS:
                self._active = False
                self._trigger_reason = ""
                logger.info("✅ 매크로 이벤트 해제 (decay 완료)")

    def snapshot(self) -> dict[str, Any]:
        """API 직렬화."""
        return {
            "active": self._active,
            "trigger_reason": self._trigger_reason,
            "active_since_ms": self._active_since_ms if self._active else None,
            "decay_remaining_seconds": round(self.decay_remaining()),
        }

    # ── 내부 ────────────────────────────────────────────────────────

    def _arm(
        self,
        reason: str,
        now: float,
        now_ms: int,
        tracker: "CorrelationTracker",
        sync: float,
        corr: float,
    ) -> None:
        if not self._active:
            # 신규 이벤트
            self._active = True
            self._active_since = now
            self._active_since_ms = now_ms
            self._trigger_reason = reason
            logger.warning(
                "🚨 매크로 이벤트 감지 [%s]  동시급변동=%.0f%%  상관계수=%.2f",
                reason, sync * 100, corr,
            )
            self._fire_alert(reason, sync, corr, tracker)
        else:
            # 기존 이벤트 유지 → 타이머 리셋, 이유 갱신
            self._active_since = now
            self._trigger_reason = reason

    def _fire_alert(
        self,
        reason: str,
        sync: float,
        corr: float,
        tracker: "CorrelationTracker",
    ) -> None:
        if self._alert_cb is None:
            return
        now = time.monotonic()
        if now - self._last_alert < _ALERT_COOLDOWN:
            return
        self._last_alert = now
        try:
            self._alert_cb(reason, sync, corr, tracker.top_movers())
        except Exception as exc:
            logger.debug("macro alert callback error: %s", exc)
