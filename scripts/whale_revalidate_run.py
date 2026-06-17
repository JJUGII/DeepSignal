"""3일 뒤 고래신호 재검증 1회 실행 — whale_signal_validate 결과를 텔레그램 통지 후
자기 launchd 작업을 제거(one-shot). 그동안 라이브로 모인 WHALE_SIGNAL_LOG.jsonl 표본으로
forward-return IC를 다시 측정한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _l in (open(os.path.join(_ROOT, ".env")) if Path(os.path.join(_ROOT, ".env")).is_file() else []):
    if _l.strip() and "=" in _l and not _l.startswith("#"):
        k, v = _l.strip().split("=", 1)
        os.environ.setdefault(k, v)


def _telegram(text: str) -> bool:
    import requests
    tok = (os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID") or "").strip()
    if not tok or not chat:
        return False
    try:
        return requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                             json={"chat_id": chat, "text": text[:4000]}, timeout=12).status_code == 200
    except Exception:
        return False


def main() -> None:
    py = sys.executable
    validate = os.path.join(_ROOT, "scripts", "whale_signal_validate.py")
    log = Path(_ROOT) / "output" / "crypto_stream" / "WHALE_SIGNAL_LOG.jsonl"
    n = sum(1 for _ in log.open()) if log.is_file() else 0
    try:
        r = subprocess.run([py, validate, "24"], cwd=_ROOT, capture_output=True, text=True, timeout=300)
        out = (r.stdout or "").strip()
    except Exception as e:
        out = f"실행 오류: {e}"
    tail = "\n".join(out.splitlines()[-30:]) if out else "(출력 없음)"
    msg = (f"🐋 [고래신호 재검증 {datetime.now():%m-%d %H:%M}] 누적 {n}건\n\n{tail}\n\n"
           "→ IC 결과로 scalping_scorer +3pt 유지/하향/제거 결정하세요.")
    _telegram(msg)
    print(msg)
    # one-shot: 자기 launchd 작업 제거(실패해도 무시)
    try:
        uid = str(os.getuid())
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/com.deepsignal.whale_revalidate"],
                       capture_output=True, text=True, timeout=15)
    except Exception:
        pass


if __name__ == "__main__":
    main()
