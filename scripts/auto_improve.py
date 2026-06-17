"""자율 개선 루프 (하루 2회) — 약점 분석 → 개선 브리핑/자동수정.

2모드:
  ① 브리핑 모드(기본, CLI 불필요): 약점+구체 코드수정안+위험도를 LLM이 도출 →
     텔레그램 통지. 저위험은 "수정?" 제안, 고위험은 검토요청. (사람이 트리거)
  ② 자동 모드(CLAUDE_CLI_PATH 설정 시): claude CLI를 worktree에서 호출해 저위험만
     테스트 통과 시 자동 적용. (※ CLI 설치 + 명시적 활성화 필요)

안전: 저위험만 자동(코드 경로 allowlist), 테스트 게이트, 브랜치 격리, 텔레그램 통지,
env AUTO_IMPROVE_AUTOCODE 로 자동모드 on/off (기본 off=브리핑만).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
for _l in (open(os.path.join(_ROOT, ".env")) if Path(os.path.join(_ROOT, ".env")).is_file() else []):
    if _l.strip() and "=" in _l and not _l.startswith("#"):
        _k, _v = _l.strip().split("=", 1)
        os.environ.setdefault(_k, _v)

from deepsignal.ai.trade_supervisor import analyze_crypto_health

# 저위험으로 자동수정 허용하는 코드 경로 (분석·리포트·도구만 — 실행/리스크/브로커 제외)
SAFE_PATHS = ("deepsignal/ai/", "scripts/", "deepsignal/analyzer/", "deepsignal/collector/")
# 절대 자동수정 금지 (고위험 — 무조건 사람 승인)
RISKY_PATHS = ("execution/", "risk/", "broker/", "order_manager", "sizing", "live_trading/")


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


def _recent_git_log() -> str:
    try:
        out = subprocess.run(["git", "log", "--oneline", "-8"], cwd=_ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception:
        return ""


def _llm_weakness_brief(report: dict) -> dict | None:
    """LLM: 최근 성과 데이터로 #1 약점 + 구체적 코드수정안 + 위험도(low/high)."""
    if os.environ.get("NEWS_LLM_ENABLED", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key or report.get("error"):
        return None
    import requests
    m = report.get("metrics", {})
    prompt = (
        "너는 코인 자동매매 시스템의 자율 개선 엔지니어다. 아래 최근 성과 지표·진단을 보고, "
        "다음 거래 사이클을 위해 *지금 고칠 가장 중요한 약점 1개*와 *구체적 코드/파라미터 수정 방향*, "
        "그리고 위험도를 평가하라. 위험도 low=분석/리포트/바운드된 임계만, high=전략/사이징/리스크게이트/주문로직. "
        '반드시 JSON: {"weakness":"...", "fix":"구체적 수정 방향(파일/함수 수준)", "risk":"low|high", "expected":"기대효과"}. '
        f"\n지표: {json.dumps(m, ensure_ascii=False)}\n진단: {json.dumps(report.get('findings'), ensure_ascii=False)}"
    )
    try:
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": os.environ.get("NEWS_LLM_MODEL", "gpt-4o-mini"),
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "response_format": {"type": "json_object"}, "max_tokens": 400},
            timeout=30)
        if r.status_code == 200:
            return json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception:
        return None
    return None


def _find_claude_cli() -> str | None:
    cand = [os.environ.get("CLAUDE_CLI_PATH", "").strip(),
            str(Path.home() / ".claude/local/claude"),
            "/usr/local/bin/claude", "/opt/homebrew/bin/claude"]
    for c in cand:
        if c and Path(c).exists():
            return c
    return None


def main() -> None:
    if os.environ.get("AUTO_IMPROVE_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
        print("AUTO_IMPROVE_ENABLED=off — 중단")
        return
    report = analyze_crypto_health(lookback=60)
    brief = _llm_weakness_brief(report)
    ts = datetime.now().strftime("%m-%d %H:%M")
    cli = _find_claude_cli()
    autocode = os.environ.get("AUTO_IMPROVE_AUTOCODE", "false").strip().lower() in ("1", "true", "yes", "on")

    if not brief:
        m = report.get("metrics", {})
        msg = f"🔁 [자율개선 {ts}] 분석만(LLM off 또는 데이터부족). 순손익 {m.get('net_krw','?')}원"
        _telegram(msg); print(msg); return

    risk = str(brief.get("risk", "high")).lower()
    head = "🔁 [자율개선 루프] 약점 브리핑"
    body = (f"{head}\n"
            f"🎯 약점: {brief.get('weakness')}\n"
            f"🔧 수정안: {brief.get('fix')}\n"
            f"📈 기대: {brief.get('expected')}\n"
            f"⚖️ 위험도: {risk}")

    # 자동 모드(완전): CLI 있고 활성화 + 저위험 → 향후 claude 호출 자리(현재는 안내)
    if autocode and cli and risk == "low":
        body += f"\n\n🤖 자동수정 모드 ON (CLI {cli}) — 저위험이라 구현 시도 예정"
        # NOTE: 실제 claude 호출/테스트/머지는 CLI 설치·검증 후 활성화. 안전을 위해 현재는 알림만.
    elif risk == "low":
        body += "\n\n👉 저위험 — '고쳐줘' 하시면 즉시 적용합니다 (CLI 미설치라 반자동)"
    else:
        body += "\n\n⚠️ 고위험 — 적용 전 검토 필요. '검토하자' 하시면 같이 봅니다"

    _telegram(body)
    # 리포트 저장
    out = Path(_ROOT) / "outputs" / "AUTO_IMPROVE_BRIEF.json"
    out.write_text(json.dumps({"at": datetime.now().isoformat(), "brief": brief,
                               "metrics": report.get("metrics")}, ensure_ascii=False, indent=1))
    print(body)


if __name__ == "__main__":
    main()
