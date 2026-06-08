"""K-GSQS → 기존 신호 시스템 브리지.

KStockSignal을 SignalLogger / ScalpSignalNotifier가 기대하는
인터페이스로 감싸는 어댑터.

DB 브리지: AUTO 임계값(82점) 이상 신호는 AI 추천 엔진이 읽는
data/deepsignal.db signals 테이블에도 upsert한다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# K-GSQS → AI DB 기록 임계값 (이 점수 이상이어야 signals DB에 저장)
_DB_WRITE_MIN_SCORE = 72.0


@dataclass
class _KStockScoreAdapter:
    """KStockSignal → SignalLogger/Notifier 인터페이스 어댑터."""

    symbol: str
    score: float         # total_score
    decision: str        # BUY_CANDIDATE / STRONG_BUY / NOTIFY → 코인 형식으로 변환
    sub_scores: dict[str, float]

    @classmethod
    def from_signal(cls, signal: Any) -> "_KStockScoreAdapter":
        # action → decision 매핑
        action_map = {
            "STRONG_BUY": "STRONG_BUY",
            "BUY": "BUY_CANDIDATE",
            "NOTIFY": "BUY_CANDIDATE",
        }
        decision = action_map.get(signal.action, "HOLD")
        return cls(
            symbol=signal.symbol,
            score=signal.total_score,
            decision=decision,
            sub_scores=dict(signal.sub_scores),
        )


class KStockSignalBridge:
    """K-GSQS 신호를 기존 로거·알림·AI DB 시스템에 연결."""

    def __init__(
        self,
        output_dir: Path | str,
        enable_telegram: bool = True,
        db_path: str | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path  # None이면 settings에서 자동 로드
        self._logger = None
        self._notifier = None
        self._init_logger()
        if enable_telegram:
            self._init_notifier()

    def _init_logger(self) -> None:
        try:
            from deepsignal.crypto_trading.signal.signal_logger import SignalLogger
            self._logger = SignalLogger(self.output_dir / "kstock")
            logger.info("K-GSQS SignalLogger 초기화 완료")
        except Exception as exc:
            logger.warning("SignalLogger 초기화 실패 (비치명적): %s", exc)

    def _init_notifier(self) -> None:
        try:
            from deepsignal.crypto_trading.signal.signal_notifier import ScalpSignalNotifier
            self._notifier = ScalpSignalNotifier()
            logger.info("K-GSQS Telegram 알림 초기화 완료")
        except Exception as exc:
            logger.warning("Notifier 초기화 실패 (비치명적): %s", exc)

    def _write_to_ai_db(self, signal: Any, current_price: float) -> None:
        """K-GSQS 신호를 AI 추천 엔진용 signals DB에 upsert.

        score >= _DB_WRITE_MIN_SCORE(72)일 때만 기록.
        BUY_CANDIDATE(82+) / STRONG_BUY(88+) → AI 엔진이 BUY 추천.
        """
        score = float(signal.total_score)
        if score < _DB_WRITE_MIN_SCORE:
            return

        # action → AI DB action 매핑
        # AI 엔진에서 BUY 발동 조건: action=='BUY_CANDIDATE' OR final_score>=60
        action_map = {
            "STRONG_BUY": "BUY_CANDIDATE",
            "BUY": "BUY_CANDIDATE",
            "NOTIFY": "BUY_CANDIDATE",
        }
        db_action = action_map.get(signal.action, "HOLD")

        confidence = min(1.0, score / 100.0)
        signal_date = datetime.now().strftime("%Y-%m-%d")

        sub = dict(signal.sub_scores) if hasattr(signal, "sub_scores") else {}
        reason_parts = [f"K-GSQS score={score:.1f}"]
        if sub:
            reason_parts.append(
                " ".join(f"{k}={v:.1f}" for k, v in list(sub.items())[:4])
            )
        reason = " | ".join(reason_parts)

        raw = {
            "k_gsqs_score": score,
            "action": signal.action,
            "sub_scores": sub,
            "current_price": current_price,
            "ts_ms": getattr(signal, "ts_ms", None),
        }

        try:
            from deepsignal.storage.database import upsert_kgsqs_signal
            result = upsert_kgsqs_signal(
                db_path=self._db_path,
                symbol=signal.symbol,
                signal_date=signal_date,
                total_score=score,
                action=db_action,
                confidence=confidence,
                reason=reason,
                raw=raw,
            )
            logger.info(
                "K-GSQS → AI DB: %s score=%.1f action=%s %s",
                signal.symbol, score, db_action, result,
            )
        except Exception as exc:
            logger.warning("K-GSQS DB upsert 실패 (비치명적): %s", exc)

    def on_signal(self, signal: Any, current_price: float) -> None:
        """신호 발생 시 호출 — 로그 기록 + Telegram 알림 + AI DB upsert."""
        if signal.action not in ("BUY", "NOTIFY", "STRONG_BUY"):
            return

        adapter = _KStockScoreAdapter.from_signal(signal)

        # 신호 로그 (JSONL)
        if self._logger is not None:
            try:
                self._logger.log_signal(adapter, price=current_price, ts_ms=signal.ts_ms)
            except Exception as exc:
                logger.debug("신호 로그 실패: %s", exc)

        # Telegram 알림
        if self._notifier is not None:
            try:
                self._notifier.notify(adapter, price=current_price)
            except Exception as exc:
                logger.debug("Telegram 알림 실패: %s", exc)

        # AI DB upsert (score >= 72)
        self._write_to_ai_db(signal, current_price)

    def check_outcomes(self, current_prices: dict[str, float]) -> None:
        """사후 수익률 기록 (매분 호출)."""
        if self._logger is None:
            return
        try:
            self._logger.check_outcomes(current_prices)
        except Exception as exc:
            logger.debug("outcome check 실패: %s", exc)

    def get_signal_stats(self) -> dict[str, Any]:
        """신호 통계 조회."""
        if self._logger is None:
            return {}
        try:
            return {
                "win_rate_stats": self._logger.win_rate_stats(),
                "auto_threshold": self._logger.auto_threshold(),
                "pending_count": len(self._logger._pending),
            }
        except Exception:
            return {}
