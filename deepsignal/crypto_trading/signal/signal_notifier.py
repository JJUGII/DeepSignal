"""GSQS 매수 신호 Telegram 알림.

BUY_CANDIDATE / STRONG_BUY 신호 발생 시 Telegram으로 즉시 알림.

특징:
    - 심볼별 쿨다운으로 스팸 방지 (기본 15분)
    - 비동기 전송: ThreadPoolExecutor로 파이프라인 블로킹 없음
    - 시간당 최대 메시지 수 제한 (기본 20건)
    - .env DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN / CHAT_ID 사용

환경 변수::
    DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN   Telegram Bot 토큰
    DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID     수신 Chat ID
    SCALP_SIGNAL_COOLDOWN_MINUTES          심볼별 쿨다운 (기본 15)
    SCALP_SIGNAL_MAX_PER_HOUR             시간당 최대 알림 수 (기본 20)
"""

from __future__ import annotations

import collections
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepsignal.crypto_trading.signal.scalping_scorer import ScalpingScore

logger = logging.getLogger(__name__)

# ── 환경 변수 기반 기본값 ──────────────────────────────────────────
_DEFAULT_COOLDOWN_MINUTES = 15
_DEFAULT_MAX_PER_HOUR = 20

# 한국어 컴포넌트 이름
_COMP_KR: dict[str, str] = {
    "trend":     "추세",
    "volume":    "거래량",
    "orderbook": "호가창",
    "tradeflow": "체결흐름",
    "futures":   "선물",
    "risk":      "리스크",
    "market":    "시장",
}

# 결정 이모지
_DECISION_EMOJI: dict[str, str] = {
    "STRONG_BUY":    "🚀",
    "BUY_CANDIDATE": "✅",
}


def _score_bar(score: float, width: int = 10) -> str:
    """점수(0~100) → 블록 막대 (예: ████████░░)."""
    filled = round(score / 100 * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _fmt_price(price: float) -> str:
    """가격 포맷: 1달러 이상이면 소수 2자리, 미만이면 최대 6자리."""
    if price >= 1_000:
        return f"{price:,.2f}"
    if price >= 1.0:
        return f"{price:.4f}"
    return f"{price:.6f}"


def _build_macro_signal_message(score: "ScalpingScore", price: float, reason: str) -> str:
    """매크로 이벤트 중 BUY 신호 — 경고 헤더 포함."""
    base = _build_message(score, price)
    reason_kr = reason.replace("SYNC:", "동시급변동 ").replace("CORR:", "상관계수 ")
    header = f"⚠️ <b>[매크로 경보 중]</b> — {_escape_html(reason_kr)}\n"
    footer = "\n⚠️ <i>매크로 노이즈 가능성 — 신중하게 판단하세요.</i>"
    return header + base + footer


def _build_macro_event_message(
    reason: str,
    sync: float,
    corr: float,
    top_movers: list[dict],
) -> str:
    """매크로 이벤트 시작 공지 메시지."""
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst).strftime("%m/%d %H:%M")

    reason_kr = "동시 급변동" if reason.startswith("SYNC") else "상관계수 급등"
    lines = [
        f"🚨 <b>매크로 이벤트 감지</b>",
        f"사유: {reason_kr} ({_escape_html(reason)})",
        f"동시급변동: <b>{sync:.0%}</b>  |  상관계수: <b>{corr:.2f}</b>",
        "",
        "📉 <b>주요 급변 심볼:</b>",
    ]
    for m in (top_movers or [])[:6]:
        sym = m["symbol"].replace("USDT", "")
        ret = m.get("ret_1m", 0.0)
        sign = "+" if ret >= 0 else ""
        lines.append(f"  {sym:<6} {sign}{ret*100:.2f}%")

    lines += [
        "",
        "→ BUY 신호에 경고 태그 적용 중 (5분)",
        f"⏱ {now_kst} KST",
    ]
    return "\n".join(lines)


def _build_weight_update_message(
    new_weights: dict[str, float],
    old_weights: dict[str, float],
    improvement: float,
    n_samples: int,
) -> str:
    """가중치 자동 보정 완료 알림 메시지."""
    from datetime import datetime, timezone, timedelta
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst).strftime("%m/%d %H:%M")

    comp_kr = {
        "trend": "추세", "volume": "거래량", "orderbook": "호가창",
        "tradeflow": "체결흐름", "futures": "선물지표", "risk": "리스크", "market": "시장",
    }

    lines = [
        "⚖️ <b>GSQS 가중치 자동 보정 완료</b>",
        f"샘플: {n_samples}건  |  5분 승률 개선: <b>+{improvement*100:.1f}%</b>",
        "",
        "변화 (기본 → 최적):",
    ]
    for k, v_new in new_weights.items():
        v_old = old_weights.get(k, v_new)
        diff = v_new - v_old
        if abs(diff) < 0.001:
            continue
        arrow = "↑" if diff > 0 else "↓"
        label = comp_kr.get(k, k)
        lines.append(
            f"  {label:<6} {v_old*100:.0f}% → <b>{v_new*100:.0f}%</b> {arrow}"
        )
    lines += ["", f"⏱ {now_kst} KST"]
    return "\n".join(lines)


def _build_message(score: "ScalpingScore", price: float) -> str:
    """Telegram HTML 형식 알림 메시지 생성."""
    emoji = _DECISION_EMOJI.get(score.decision, "📊")
    sym = score.symbol.replace("USDT", "")  # 'XLMUSDT' → 'XLM' (짧게)

    # 헤더
    lines: list[str] = [
        f"{emoji} <b>{score.decision}  {sym}/USDT</b>",
        f"💰 ${_fmt_price(price)}  |  📊 <b>{score.score:.1f}점</b>",
        "",
    ]

    # 컴포넌트 점수 표 (막대 + 숫자)
    lines.append("<b>컴포넌트:</b>")
    sub = score.sub_scores
    for key in ("trend", "volume", "orderbook", "tradeflow", "futures", "risk", "market"):
        val = sub.get(key, 0.0)
        bar = _score_bar(val, width=8)
        kr = _COMP_KR.get(key, key)
        lines.append(f"  {kr:<5} {bar} <code>{val:3.0f}</code>")

    # 주요 신호 노트
    if score.notes:
        lines.append("")
        notes_text = " | ".join(score.notes[:4])  # 최대 4개
        lines.append(f"📌 <i>{_escape_html(notes_text)}</i>")

    # 시각 (KST)
    try:
        from datetime import datetime, timezone, timedelta
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst).strftime("%m/%d %H:%M")
        lines.append("")
        lines.append(f"⏱ {now_kst} KST")
    except Exception:
        pass

    return "\n".join(lines)


def _escape_html(text: str) -> str:
    """HTML 특수문자 이스케이프 (Telegram HTML mode용)."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class ScalpSignalNotifier:
    """GSQS 매수 신호 Telegram 알림 발송기.

    Args:
        bot_token:        Telegram Bot API 토큰. None이면 환경 변수에서 로드.
        chat_id:          수신 Chat ID. None이면 환경 변수에서 로드.
        cooldown_minutes: 동일 심볼 알림 쿨다운 (분). 기본 15분.
        max_per_hour:     시간당 최대 알림 수. 기본 20건.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        cooldown_minutes: int | None = None,
        max_per_hour: int | None = None,
    ) -> None:
        self._token = (
            bot_token
            or os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN", "")
        ).strip() or None

        self._chat = (
            chat_id
            or os.getenv("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID", "")
        ).strip() or None

        self._cooldown = float(
            (cooldown_minutes if cooldown_minutes is not None
             else int(os.getenv("SCALP_SIGNAL_COOLDOWN_MINUTES", _DEFAULT_COOLDOWN_MINUTES)))
        ) * 60.0

        self._max_per_hour = int(
            max_per_hour if max_per_hour is not None
            else int(os.getenv("SCALP_SIGNAL_MAX_PER_HOUR", _DEFAULT_MAX_PER_HOUR))
        )

        # 심볼별 마지막 알림 시각 (monotonic)
        self._last_sent: dict[str, float] = {}

        # 시간당 전송 횟수 추적 (deque: 전송 시각 저장)
        self._sent_times: collections.deque[float] = collections.deque()

        # 비동기 전송용 (최대 2 워커: 동시 전송 대기 최소화)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tg_signal")

        if not self._token or not self._chat:
            logger.warning(
                "ScalpSignalNotifier: DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN / CHAT_ID 미설정 — "
                "알림 비활성화. .env에 설정하면 즉시 활성화됩니다."
            )

    # ── 공개 API ─────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """Bot 토큰과 Chat ID가 모두 설정된 경우에만 True."""
        return bool(self._token and self._chat)

    def notify(self, score: "ScalpingScore", *, price: float) -> None:
        """BUY 신호 알림 발송 (비동기, 논블로킹).

        쿨다운 또는 시간당 한도 초과 시 스킵.
        Telegram 미설정 시 조용히 스킵.
        """
        if not self.enabled:
            return

        now = time.monotonic()

        # ── 심볼별 쿨다운 체크 ─────────────────────────────────
        last = self._last_sent.get(score.symbol, 0.0)
        if now - last < self._cooldown:
            remaining = int(self._cooldown - (now - last))
            logger.debug(
                "알림 스킵 [%s] — 쿨다운 %d초 남음 (결정=%s 점수=%.1f)",
                score.symbol, remaining, score.decision, score.score,
            )
            return

        # ── 시간당 전송 한도 체크 ─────────────────────────────
        cutoff = now - 3600.0
        while self._sent_times and self._sent_times[0] < cutoff:
            self._sent_times.popleft()
        if len(self._sent_times) >= self._max_per_hour:
            logger.debug(
                "알림 스킵 [%s] — 시간당 한도 %d건 초과",
                score.symbol, self._max_per_hour,
            )
            return

        # ── 전송 예약 ───────────────────────────────────────────
        self._last_sent[score.symbol] = now
        self._sent_times.append(now)

        msg = _build_message(score, price)
        token, chat = self._token, self._chat

        self._executor.submit(self._send, token, chat, msg, score.symbol, score.score)

    def notify_macro_warning(
        self,
        score: "ScalpingScore",
        *,
        price: float,
        reason: str,
    ) -> None:
        """매크로 이벤트 중 BUY 신호 — 경고 헤더를 붙여 발송 (쿨다운·한도 적용)."""
        if not self.enabled:
            return
        now = time.monotonic()
        last = self._last_sent.get(score.symbol, 0.0)
        if now - last < self._cooldown:
            return
        cutoff = now - 3600.0
        while self._sent_times and self._sent_times[0] < cutoff:
            self._sent_times.popleft()
        if len(self._sent_times) >= self._max_per_hour:
            return
        self._last_sent[score.symbol] = now
        self._sent_times.append(now)
        msg = _build_macro_signal_message(score, price, reason)
        self._executor.submit(self._send, self._token, self._chat, msg, score.symbol, score.score)

    def notify_macro_event(
        self,
        reason: str,
        sync: float,
        corr: float,
        top_movers: list[dict],
    ) -> None:
        """매크로 이벤트 시작 공지 (MacroGuard 콜백으로 호출됨)."""
        if not self.enabled:
            return
        msg = _build_macro_event_message(reason, sync, corr, top_movers)
        self._executor.submit(self._send, self._token, self._chat, msg, "MACRO_EVENT", 0.0)

    def notify_weight_update(
        self,
        new_weights: dict[str, float],
        old_weights: dict[str, float],
        improvement: float,
        n_samples: int,
    ) -> None:
        """가중치 자동 보정 완료 알림 (improvement > 0일 때만 전송)."""
        if not self.enabled or improvement <= 0:
            return
        msg = _build_weight_update_message(new_weights, old_weights, improvement, n_samples)
        self._executor.submit(self._send, self._token, self._chat, msg, "WEIGHT_UPDATE", 0.0)

    def shutdown(self) -> None:
        """프로세스 종료 시 executor 정리."""
        self._executor.shutdown(wait=False)

    # ── 내부 ───────────────────────────────────────────────────

    @staticmethod
    def _send(
        token: str,
        chat: str,
        text: str,
        sym: str,
        score_val: float,
    ) -> None:
        """Telegram sendMessage API 호출 (별도 스레드에서 실행)."""
        try:
            import urllib.request
            import urllib.parse
            import json

            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({
                "chat_id": chat,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("ok"):
                    logger.info(
                        "Telegram 알림 전송 완료 [%s] score=%.1f",
                        sym, score_val,
                    )
                else:
                    logger.warning(
                        "Telegram 알림 실패 [%s]: %s",
                        sym, body.get("description", body),
                    )
        except Exception as exc:
            logger.warning("Telegram 알림 전송 오류 [%s]: %s", sym, exc)


# ── 단독 테스트 ────────────────────────────────────────────────────

def _test_send() -> None:
    """python -m deepsignal.crypto_trading.signal.signal_notifier 로 테스트."""
    import sys
    from dataclasses import dataclass, field

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    @dataclass
    class _FakeScore:
        symbol: str = "BTCUSDT"
        score: float = 83.5
        decision: str = "STRONG_BUY"
        blocked: bool = False
        block_reason: str = ""
        sub_scores: dict[str, float] = field(default_factory=lambda: {
            "trend": 95, "volume": 80, "orderbook": 70,
            "tradeflow": 75, "futures": 65, "risk": 90, "market": 80,
        })
        signals: dict[str, Any] = field(default_factory=dict)
        notes: list[str] = field(default_factory=lambda: [
            "체결가속 +3pt (acc=4.0x)", "고래출현 +3pt (ratio=88.2x)"
        ])
        is_buy: bool = True

    notifier = ScalpSignalNotifier()
    if not notifier.enabled:
        print("❌ Telegram 미설정 (.env에 BOT_TOKEN / CHAT_ID 확인)")
        sys.exit(1)

    print("메시지 미리보기:")
    print("-" * 40)
    fake = _FakeScore()
    print(_build_message(fake, price=73245.99))  # type: ignore[arg-type]
    print("-" * 40)

    print("전송 중...")
    notifier.notify(fake, price=73245.99)  # type: ignore[arg-type]
    # executor가 전송을 마칠 때까지 잠시 대기
    import time as _time
    _time.sleep(3)
    notifier.shutdown()
    print("완료")


if __name__ == "__main__":
    _test_send()
