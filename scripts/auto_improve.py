"""자율 개선 루프 (하루 2회) — 약점 분석 → 개선 브리핑/자동수정.

2모드:
  ① 브리핑 모드(기본, CLI 불필요): 약점+구체 코드수정안+위험도를 LLM이 도출 →
     텔레그램 통지. 저위험은 "수정?" 제안, 고위험은 검토요청. (사람이 트리거)
  ② 자동 모드(CLAUDE_CLI_PATH 설정 시): claude CLI를 worktree에서 호출해 저위험만
     테스트 통과 시 자동 적용. (※ CLI 설치 + 명시적 활성화 필요)

안전: 저위험만 자동(코드 경로 allowlist), 테스트 게이트, 브랜치 격리, 텔레그램 통지,
env AUTO_IMPROVE_AUTOCODE 로 자동모드 on/off (기본 off=브리핑만).
(파이프라인 검증 완료 2026-06-17)
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
# ★자기 가드레일 자기수정 금지(self-modification 차단): 루프의 안전정책을 정의하는 파일은
#  SAFE_PATHS(scripts/) 안에 있어도 자동수정에서 *절대* 제외. 사람만 바꿀 수 있음.
PROTECTED_PATHS = (
    "scripts/auto_improve.py",                       # allowlist·테스트게이트·자기 자신
    "scripts/com.deepsignal.auto_improve.plist",     # 스케줄·env 스위치
    "deepsignal/ai/trade_supervisor.py",             # autotune 가드 범위(TP/SL bound)
    "scripts/ai_supervisor_run.py",
)


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
    import shutil
    cand = [os.environ.get("CLAUDE_CLI_PATH", "").strip(),
            str(Path.home() / ".claude/local/claude"),
            "/usr/local/bin/claude", "/opt/homebrew/bin/claude",
            str(Path.home() / ".npm-global/bin/claude")]
    for c in cand:
        if c and Path(c).exists():
            return c
    return shutil.which("claude")


def _git(args: list[str], cwd: str) -> tuple[int, str]:
    """(rc, stdout) — stderr는 의도적으로 제외. macOS의 'non-monotonic index .git/...._pack'
    경고가 stderr로 나와 diff 파일목록 등 파싱대상을 오염시키므로 stdout만 반환한다.
    에러 상세가 필요하면 _git_err 사용."""
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout.strip()


def _git_err(args: list[str], cwd: str) -> tuple[int, str]:
    """에러 메시지용 — stdout+stderr 합치되 non-monotonic 잡음만 제거."""
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)
    out = "\n".join(l for l in (r.stdout + r.stderr).splitlines() if "non-monotonic" not in l)
    return r.returncode, out.strip()


def _pytest_failed_set(cwd: str) -> set[str]:
    """tests/ 실행 후 FAILED/ERROR 노드ID 집합 반환(수집에러는 무시).

    repo 베이스라인에 이미 다수 실패가 있어 '전부 통과'는 게이트로 못 씀 → 변경 전후
    실패집합을 비교(새 실패 0건이어야 통과)하는 차등 게이트용.
    """
    # worktree엔 .venv가 없음(gitignore) → 메인 인터프리터(절대경로) 사용. cwd=worktree라
    # sys.path[0]=''가 worktree/deepsignal을 가리켜 *변경된 코드*가 실제로 테스트됨.
    # ★시스템 부작용 테스트 격리: launchd/installer 테스트는 실제 ~/Library/LaunchAgents를
    #  건드릴 위험이 있어(과거 plist 손상 사고) 게이트에서 제외. 어차피 SAFE_PATHS 밖이라
    #  자율개선이 해당 코드를 수정하지 않으므로 커버리지 손실 없음.
    DANGEROUS = ("tests/test_launchd_venv_python.py", "tests/test_launchd_installer.py",
                 "tests/test_crypto_launchd_installer.py")
    cmd = ([sys.executable, "-m", "pytest", "tests/", "-q", "--continue-on-collection-errors",
            "-p", "no:cacheprovider", "--no-header", "-rfE", "-p", "no:randomly"]
           + [a for f in DANGEROUS for a in ("--ignore", f)])
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        return {"__timeout__"}
    failed = set()
    for line in (r.stdout + r.stderr).splitlines():
        s = line.strip()
        if s.startswith("FAILED ") or s.startswith("ERROR "):
            node = s.split(" ", 1)[1].split(" ")[0].strip()
            if node:
                failed.add(node)
    return failed


def _all_in_safe_paths(files: list[str]) -> bool:
    """변경 파일이 전부 SAFE_PATHS 안 + RISKY_PATHS 밖 + PROTECTED 아님이어야 자동허용.

    PROTECTED는 루프의 안전정책 정의 파일(자기 자신 등) — 자기수정 차단.
    """
    for f in files:
        f = f.strip()
        if not f:
            continue
        if f in PROTECTED_PATHS:        # 자기 가드레일 수정 시도 → 거부
            return False
        if any(rp in f for rp in RISKY_PATHS):
            return False
        if not any(f.startswith(sp) for sp in SAFE_PATHS):
            return False
    return True


def _run_autocode(brief: dict, cli: str) -> dict:
    """저위험 개선을 worktree 격리에서 claude로 구현 → allowlist+테스트 통과 시 main 머지.

    안전: ①worktree 격리(main 직접수정X) ②변경파일 SAFE_PATHS allowlist 강제(실행/리스크/
    브로커 경로면 거부) ③pytest 게이트 ④실패 시 worktree 폐기·머지 안 함.
    반환: {ok, status, detail, branch, files}
    """
    date = datetime.now().strftime("%Y%m%d-%H%M")
    branch = f"auto-improve/{date}"
    wt = f"/tmp/deepsignal_autoimprove_{date}"
    # 깨끗한 베이스에서만 (uncommitted 변경 있으면 중단 — 출력파일 제외)
    rc, base = _git_err(["worktree", "add", "-b", branch, wt, "HEAD"], _ROOT)
    if rc != 0:
        return {"ok": False, "status": "worktree_fail", "detail": base[:300]}
    try:
        # 변경 전 베이스라인 실패집합 (repo에 기존 실패 다수 → 차등 비교용)
        baseline_failed = _pytest_failed_set(wt)
        if "__timeout__" in baseline_failed:
            return {"ok": False, "status": "baseline_timeout",
                    "detail": "베이스라인 테스트 타임아웃", "branch": branch}
        prompt = (
            "DeepSignal 자동매매 시스템의 저위험 개선 1건을 구현하라. 아래 약점/수정안을 보고 "
            "*최소 변경*으로 고쳐라. 절대 규칙: deepsignal/live_trading, execution, risk, broker, "
            "order_manager, sizing 경로는 건드리지 마라(고위험). 분석/리포트/스크립트/임계파일만 수정. "
            "기존 코드 스타일을 따르고, 변경 후 한 줄로 무엇을 바꿨는지 설명하라.\n\n"
            f"약점: {brief.get('weakness')}\n수정안: {brief.get('fix')}\n기대: {brief.get('expected')}"
        )
        cp = subprocess.run(
            [cli, "-p", prompt, "--permission-mode", "acceptEdits",
             "--allowedTools", "Edit", "Read", "Grep", "Glob"],
            cwd=wt, capture_output=True, text=True, timeout=600,
            env={**os.environ, "CLAUDE_PROJECT_DIR": wt})
        claude_out = (cp.stdout or "")[-800:]
        rc, changed = _git(["diff", "--name-only", "HEAD"], wt)
        files = [f for f in changed.splitlines() if f.strip()]
        if not files:
            return {"ok": False, "status": "no_change", "detail": claude_out, "branch": branch}
        if not _all_in_safe_paths(files):
            return {"ok": False, "status": "blocked_unsafe_path",
                    "detail": f"변경파일이 안전경로 밖: {files}", "branch": branch, "files": files}
        # 차등 테스트 게이트: 변경 후 '새로 깨진' 테스트가 있으면 거부
        post_failed = _pytest_failed_set(wt)
        if "__timeout__" in post_failed:
            return {"ok": False, "status": "test_timeout", "branch": branch, "files": files,
                    "detail": "변경 후 테스트 타임아웃"}
        new_failures = sorted(post_failed - baseline_failed)
        if new_failures:
            return {"ok": False, "status": "test_fail", "branch": branch, "files": files,
                    "detail": f"새 실패 {len(new_failures)}건: " + ", ".join(new_failures[:8])}
        # 통과 → 변경 소스파일만 main에 복사+커밋 (git merge는 churn하는 output/*.jsonl·
        # 정크파일이 깔린 dirty 워킹트리에 취약 → 변경파일만 좁게 반영해 오염과 무관하게).
        # 단, 대상파일이 main에서 이미 dirty면 사용자 작업 덮어쓸 위험 → 중단.
        import shutil
        _rc, dirty = _git(["diff", "--name-only"], _ROOT)
        dirty_set = {d.strip() for d in dirty.splitlines() if d.strip()}
        clash = [f for f in files if f in dirty_set]
        if clash:
            return {"ok": False, "status": "target_dirty", "branch": branch, "files": files,
                    "detail": f"대상파일이 main에서 미커밋 상태 — 수동 검토: {clash}"}
        for f in files:
            shutil.copy2(Path(wt) / f, Path(_ROOT) / f)
        _git(["add", *files], _ROOT)
        rcc, cout = _git_err(["commit", "-m",
              f"[자율개선] {brief.get('weakness','')[:60]}\n\n{brief.get('fix','')[:200]}\n\n"
              "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"], _ROOT)
        if rcc != 0:
            return {"ok": False, "status": "commit_fail", "detail": cout[:300], "branch": branch, "files": files}
        return {"ok": True, "status": "deployed", "branch": branch, "files": files, "detail": claude_out}
    finally:
        _git(["worktree", "remove", "--force", wt], _ROOT)
        _git(["branch", "-D", branch], _ROOT)   # 머지본은 main에 남음(--no-ff), 브랜치 ref만 정리


_STATE_FILE = Path(_ROOT) / "outputs" / "AUTO_IMPROVE_STATE.json"


def _read_state() -> dict:
    """웹 킬스위치 상태(env보다 우선). {autocode, loop} — 없으면 {} (env 따름)."""
    try:
        if _STATE_FILE.is_file():
            return json.loads(_STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def main() -> None:
    state = _read_state()
    # 완전 중단 킬스위치(웹): loop=false면 분석·자동수정 모두 정지
    if state.get("loop") is False or \
       os.environ.get("AUTO_IMPROVE_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
        print("자율개선 OFF (웹 킬스위치 또는 AUTO_IMPROVE_ENABLED=off) — 중단")
        return
    report = analyze_crypto_health(lookback=60)
    brief = _llm_weakness_brief(report)
    ts = datetime.now().strftime("%m-%d %H:%M")
    cli = _find_claude_cli()
    autocode = os.environ.get("AUTO_IMPROVE_AUTOCODE", "false").strip().lower() in ("1", "true", "yes", "on")
    # 웹 킬스위치가 env보다 우선: autocode=false면 코드 자동수정 끄고 브리핑만(거래는 무관)
    if state.get("autocode") is False:
        autocode = False

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

    # 자동 모드(완전): CLI 있고 활성화 + 저위험 → worktree에서 구현·테스트·자동머지
    if autocode and cli and risk == "low":
        res = _run_autocode(brief, cli)
        if res.get("ok"):
            body += (f"\n\n🤖 자동수정 배포됨 ✅ (브랜치 {res['branch']})\n"
                     f"변경: {', '.join(res.get('files', []))}\n"
                     "→ 다음 사이클부터 반영. 성과 나빠지면 알려주세요(롤백)")
        else:
            st = res.get("status")
            label = {"test_fail": "테스트 실패", "blocked_unsafe_path": "안전경로 위반 차단",
                     "no_change": "변경 없음", "merge_fail": "머지 충돌",
                     "worktree_fail": "worktree 생성 실패"}.get(st, st)
            body += f"\n\n🤖 자동수정 보류({label}) — 수동 검토 필요\n{str(res.get('detail',''))[:300]}"
        _telegram(body); print(body)
        out = Path(_ROOT) / "outputs" / "AUTO_IMPROVE_BRIEF.json"
        out.write_text(json.dumps({"at": datetime.now().isoformat(), "brief": brief,
                                   "autocode": res, "metrics": report.get("metrics")},
                                  ensure_ascii=False, indent=1))
        return
    elif autocode and cli:
        body += "\n\n⚠️ 고위험 — 자동수정 안 함(allowlist 정책). 검토 후 적용"
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
