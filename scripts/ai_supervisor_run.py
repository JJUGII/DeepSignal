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

from deepsignal.ai.trade_supervisor import analyze_asset_health, auto_tune, llm_comment

_REPORT = Path(_ROOT) / "outputs" / "AI_SUPERVISOR_REPORT.json"
_ASSETS = [("crypto", "코인"), ("domestic", "국내"), ("overseas", "해외")]


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


def _fmt_collected_at(report: dict) -> str:
    """데이터 수집(분석 실행) 시각 — KST 'YYYY-MM-DD HH:MM:SS' 형태."""
    raw = str(report.get("analyzed_at") or "").strip()
    if raw:
        return raw.replace("T", " ")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _format(report: dict, comment: str | None, asset_label: str = "코인") -> str:
    collected = _fmt_collected_at(report)
    if report.get("error"):
        return f"📅 수집: {collected} (KST)\n🤖 [AI 감독관·{asset_label}] {report['error']}"
    m = report["metrics"]
    hold = m.get("median_hold_min")
    hold_s = f"{hold}분" if hold is not None else "—"
    lines = [
        f"📅 데이터 수집: {collected} (KST)",
        f"🤖 [AI 매매 감독관] {asset_label}",
        f"최근 {m['n']}건 · 승률 {m['win_rate']}% · 순손익 {m['net_krw']:+,}원 "
        f"(거래대금 {m['volume_krw']:,}원·수수료 {m['fee_krw']:,}원, 종목당 평균 {m['avg_trade_pct']}%)",
        f"평균 익절 +{m['avg_win']}% / 손절 {m['avg_loss']}% · 중앙보유 {hold_s} · churn {m['churn_rate']}%",
        "",
        "🔎 진단:",
    ]
    lines += [f"• {f}" for f in report["findings"]]
    if report["recommendations"]:
        lines += ["", "💡 권고:"] + [f"→ {c}" for c in report["recommendations"]]
    tune = report.get("auto_tune")
    if tune and tune.get("applied"):
        lines += ["", f"🔧 자동 튜닝 적용: TP {tune['take_profit_pct']}% / SL {tune['stop_loss_pct']}%",
                  f"   ({tune['reason']})"]
    if comment:
        lines += ["", f"🧠 총평: {comment}"]
    return "\n".join(lines)


def _run_one(asset: str, label: str) -> tuple[dict, bool]:
    """단일 자산 분석 → 리포트 저장 + (경고 시) 텔레그램. (report, sent) 반환."""
    report = analyze_asset_health(asset, lookback=60)
    # 자동 튜닝은 코인 전용(CRYPTO_ACTIVE_THRESHOLDS.json) — 주식은 진단·보고만
    tune = auto_tune(report) if asset == "crypto" else None
    report["auto_tune"] = tune
    report["asset"] = asset
    comment = llm_comment(report) if not report.get("error") else None
    report["llm_comment"] = comment
    # 코인은 하위호환 위해 기존 파일명도 유지 + 자산별 파일
    _REPORT.parent.mkdir(parents=True, exist_ok=True)
    (_REPORT.parent / f"AI_SUPERVISOR_REPORT_{asset}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1))
    if asset == "crypto":
        _REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    findings = report.get("findings") or []
    has_warning = bool(report.get("error") and report.get("n") != 0) or \
        bool((tune or {}).get("applied")) or \
        any(f != "특이 이상 없음 — 정상 범위" and "표본" not in f for f in findings)
    msg = _format(report, comment, label)
    sent = _send_telegram(msg) if has_warning else False
    print(f"[{datetime.now().strftime('%H:%M')}] {label} → AI_SUPERVISOR_REPORT_{asset}.json · "
          f"텔레그램 {'전송' if sent else ('정상/표본부족 미전송' if not has_warning else '미전송')}")
    print(msg + "\n" + "─" * 40)
    return report, sent


def main() -> None:
    for asset, label in _ASSETS:
        try:
            _run_one(asset, label)
        except Exception as e:
            print(f"[{label}] 분석 실패: {e}")


if __name__ == "__main__":
    main()
