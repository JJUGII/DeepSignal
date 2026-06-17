"""AI 매매 감독관 러너 — 최근 코인거래 분석 → 리포트 저장 + 텔레그램 알림.

launchd로 주기 실행(예: 30분). 자동으로 파라미터를 바꾸지 않고(안전), 진단·권고만
보고한다. (실시간 틱 제어가 아닌 '슬로우 감독')
사용: ./.venv/bin/python scripts/ai_supervisor_run.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
for _l in (open(os.path.join(_ROOT, ".env")) if Path(os.path.join(_ROOT, ".env")).is_file() else []):
    if _l.strip() and "=" in _l and not _l.startswith("#"):
        _k, _v = _l.strip().split("=", 1)
        os.environ.setdefault(_k, _v)

from deepsignal.ai.trade_supervisor import analyze_crypto_health, llm_comment

_REPORT = Path(_ROOT) / "outputs" / "AI_SUPERVISOR_REPORT.json"


def _send_telegram(text: str) -> bool:
    import requests
    tok = (os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID") or "").strip()
    if not tok or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=12)
        return r.status_code == 200
    except Exception:
        return False


def _format(report: dict, comment: str | None) -> str:
    if report.get("error"):
        return f"🤖 [AI 감독관] {report['error']}"
    m = report["metrics"]
    lines = [
        "🤖 [AI 매매 감독관] 코인",
        f"최근 {m['n']}건 · 승률 {m['win_rate']}% · 순손익 {m['net_krw']:+,}원 "
        f"(거래대금 {m['volume_krw']:,}원·수수료 {m['fee_krw']:,}원, 종목당 평균 {m['avg_trade_pct']}%)",
        f"평균 익절 +{m['avg_win']}% / 손절 {m['avg_loss']}% · 중앙보유 {m['median_hold_min']}분 · churn {m['churn_rate']}%",
        "",
        "🔎 진단:",
    ]
    lines += [f"• {f}" for f in report["findings"]]
    if report["recommendations"]:
        lines += ["", "💡 권고:"] + [f"→ {c}" for c in report["recommendations"]]
    if comment:
        lines += ["", f"🧠 총평: {comment}"]
    return "\n".join(lines)


def main() -> None:
    report = analyze_crypto_health(lookback=60)
    comment = llm_comment(report) if not report.get("error") else None
    report["llm_comment"] = comment
    _REPORT.parent.mkdir(parents=True, exist_ok=True)
    _REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    # 스팸 방지: 실제 경고(순손실·churn·비대칭·반복손실 등)가 있을 때만 텔레그램.
    findings = report.get("findings") or []
    has_warning = bool(report.get("error")) or any(
        f != "특이 이상 없음 — 정상 범위" for f in findings
    )
    msg = _format(report, comment)
    sent = _send_telegram(msg) if has_warning else False
    print(f"[{datetime.now().strftime('%H:%M')}] 감독관 리포트 → {_REPORT.name} · 텔레그램 "
          f"{'전송' if sent else ('정상이라 미전송' if not has_warning else '미전송')}")
    print(msg)


if __name__ == "__main__":
    main()
