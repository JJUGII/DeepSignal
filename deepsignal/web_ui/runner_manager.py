"""러너 프로세스 관리 — macOS(launchctl) / Windows(subprocess) 크로스플랫폼."""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LAUNCHD_LABEL = "com.deepsignal.crypto_auto_runner"
_RUNNER_PID_FILE = "WEBUI_RUNNER_PID.json"
_STATE_FILE = "CRYPTO_AUTO_RUNNER_STATE.json"
_IS_MACOS = platform.system() == "Darwin"


# ──────────────────────────────────────────
# launchctl helpers (macOS only)
# ──────────────────────────────────────────

def _launchctl_list() -> dict[str, Any]:
    """launchctl list <label> 결과 파싱."""
    try:
        r = subprocess.run(
            ["launchctl", "list", _LAUNCHD_LABEL],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return {}
        out: dict[str, Any] = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k.strip().strip('"')] = v.strip().strip('";')
        return out
    except Exception:
        return {}


def _launchctl_pid() -> int | None:
    """launchctl list | grep 으로 PID 파싱."""
    try:
        r = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.splitlines():
            if _LAUNCHD_LABEL in line:
                parts = line.split()
                pid_str = parts[0] if parts else "-"
                if pid_str != "-":
                    return int(pid_str)
        return None
    except Exception:
        return None


def _launchd_service_exists() -> bool:
    try:
        r = subprocess.run(
            ["launchctl", "list", _LAUNCHD_LABEL],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


# ──────────────────────────────────────────
# subprocess PID helpers (cross-platform)
# ──────────────────────────────────────────

def _pid_file_path(output_dir: Path) -> Path:
    return output_dir / _RUNNER_PID_FILE


def _write_pid(output_dir: Path, pid: int, args: list[str]) -> None:
    _pid_file_path(output_dir).write_text(
        json.dumps({"pid": pid, "args": args, "started_at": datetime.now().isoformat()})
    )


def _read_pid(output_dir: Path) -> tuple[int | None, float | None]:
    p = _pid_file_path(output_dir)
    if not p.is_file():
        return None, None
    try:
        d = json.loads(p.read_text())
        ts_str = d.get("started_at")
        started = datetime.fromisoformat(ts_str).timestamp() if ts_str else None
        return int(d["pid"]), started
    except Exception:
        return None, None


def _is_pid_alive(pid: int) -> bool:
    # os.kill(pid, 0) on Windows sends CTRL_C_EVENT (signal 0) instead of
    # checking existence — use tasklist on Windows to avoid killing the process.
    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).decode(errors="ignore")
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ──────────────────────────────────────────
# Public API
# ──────────────────────────────────────────

def get_runner_status(output_dir: Path) -> dict[str, Any]:
    """러너 현재 상태 반환."""
    state_path = output_dir / _STATE_FILE
    state: dict[str, Any] = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text())
        except Exception:
            pass

    # PID / 실행 여부
    pid: int | None = None
    running = False
    uptime_sec: float | None = None
    source = "unknown"

    if _IS_MACOS and _launchd_service_exists():
        source = "launchd"
        pid = _launchctl_pid()
        running = pid is not None
        if running:
            # uptime from state file
            resumed_str = state.get("runner_resumed_at") or state.get("runner_paused_at")
            if resumed_str:
                try:
                    resumed = datetime.fromisoformat(resumed_str)
                    if resumed.tzinfo is None:
                        resumed = resumed.astimezone()
                    uptime_sec = (datetime.now().astimezone() - resumed).total_seconds()
                except Exception:
                    pass
    else:
        source = "subprocess"
        stored_pid, started_ts = _read_pid(output_dir)
        if stored_pid and _is_pid_alive(stored_pid):
            pid = stored_pid
            running = True
            if started_ts:
                uptime_sec = time.time() - started_ts

    return {
        "running": running,
        "pid": pid,
        "source": source,
        "uptime_sec": uptime_sec,
        "paused": bool(state.get("runner_paused", False)),
        "pause_reason": state.get("runner_pause_reason", ""),
        "orders_today": int(state.get("orders_today", 0)),
        "buy_krw_today": float(state.get("buy_krw_today", 0.0)),
        "sell_krw_today": float(state.get("sell_krw_today", 0.0)),
        "last_market": state.get("last_market"),
        "daily_key": state.get("daily_key"),
    }


def start_runner(output_dir: Path, project_root: Path) -> tuple[bool, str]:
    """러너 시작."""
    status = get_runner_status(output_dir)
    if status["running"]:
        return False, "이미 실행 중입니다"

    try:
        from dotenv import load_dotenv
        from deepsignal.crypto_trading.broker.selection import normalize_crypto_broker_name

        load_dotenv(str(project_root / ".env"), override=False)
        broker_name = normalize_crypto_broker_name()
    except Exception:
        broker_name = "upbit"

    if _IS_MACOS and _launchd_service_exists():
        try:
            uid = os.getuid()
            r = subprocess.run(
                ["launchctl", "start", _LAUNCHD_LABEL],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0, r.stderr.strip() or "시작 완료"
        except Exception as e:
            return False, str(e)

    # subprocess fallback
    python = sys.executable
    cmd = [
        python, str(project_root / "main.py"),
        "crypto-auto-runner",
        "--broker", broker_name,
        "--interval-minutes", "1.0",
        "--output-dir", str(output_dir),
        "--execute",
    ]
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_fp = open(output_dir / "webui_runner.log", "a")
        kwargs: dict[str, Any] = dict(
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            cwd=str(project_root),
        )
        if platform.system() == "Windows":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            kwargs["startupinfo"] = si
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(cmd, **kwargs)
        _write_pid(output_dir, proc.pid, cmd)
        return True, f"PID {proc.pid} 시작됨"
    except Exception as e:
        return False, str(e)


def stop_runner(output_dir: Path) -> tuple[bool, str]:
    """러너 중지."""
    status = get_runner_status(output_dir)
    if not status["running"]:
        return False, "실행 중이 아닙니다"

    if _IS_MACOS and _launchd_service_exists():
        try:
            r = subprocess.run(
                ["launchctl", "stop", _LAUNCHD_LABEL],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0, r.stderr.strip() or "중지 완료"
        except Exception as e:
            return False, str(e)

    # subprocess fallback
    pid, _ = _read_pid(output_dir)
    if pid:
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
            _pid_file_path(output_dir).unlink(missing_ok=True)
            return True, f"PID {pid} 중지됨"
        except Exception as e:
            return False, str(e)
    return False, "PID 파일 없음"


def restart_runner(output_dir: Path, project_root: Path) -> tuple[bool, str]:
    """러너 재시작."""
    if _IS_MACOS and _launchd_service_exists():
        try:
            uid = os.getuid()
            r = subprocess.run(
                ["launchctl", "kickstart", "-k",
                 f"gui/{uid}/{_LAUNCHD_LABEL}"],
                capture_output=True, text=True, timeout=15,
            )
            return r.returncode == 0, r.stderr.strip() or "재시작 완료"
        except Exception as e:
            return False, str(e)
    ok, msg = stop_runner(output_dir)
    time.sleep(1)
    return start_runner(output_dir, project_root)


def set_pause_state(
    output_dir: Path,
    *,
    paused: bool,
    reason: str = "",
) -> tuple[bool, str]:
    """runner_paused 플래그 토글 (틱 자체 skip)."""
    state_path = output_dir / _STATE_FILE
    try:
        state: dict[str, Any] = {}
        if state_path.is_file():
            state = json.loads(state_path.read_text())
        state["runner_paused"] = paused
        state["runner_pause_reason"] = reason
        now = datetime.now().isoformat()
        if paused:
            state["runner_paused_at"] = now
        else:
            state["runner_resumed_at"] = now
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        return True, "일시정지" if paused else "재개"
    except Exception as e:
        return False, str(e)
