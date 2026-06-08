"""macOS launchd installer for daily-ai-auto-runner (user LaunchAgent)."""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LAUNCHD_LABEL = "com.deepsignal.auto_runner"
PLIST_NAME = f"{LAUNCHD_LABEL}.plist"
LAUNCH_ROOT_SYMLINK_NAME = "project_root"
PATH_HASH_WARNING = (
    "Project path contains '#'. launchd may fail to start runner on some macOS environments "
    "(last exit code 78 EX_CONFIG). install-launchd uses ~/.deepsignal/project_root symlink without '#'."
)


@dataclass
class LaunchdRunnerConfig:
    broker: str = "kis"
    network: bool = True
    output_dir: str = "outputs"
    plan_time: str = "09:05"
    report_time: str = "15:40"
    max_order_value: float = 300_000.0
    max_single_order_value: float = 300_000.0
    max_total_order_value: float = 300_000.0
    max_orders: int = 1
    expires_minutes: int = 420
    poll_interval: float = 3.0
    loop_sleep_seconds: float = 15.0
    timeout_seconds: float = 10.0
    allow_test_plan_order: bool = False
    ignore_safety_block_for_test: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_root(start: str | Path | None = None) -> Path:
    if start:
        path = Path(start).expanduser().resolve()
        if path.is_dir():
            return path
    env = os.environ.get("DEEPSIGNAL_PROJECT_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "main.py").is_file():
        return cwd
    here = Path(__file__).resolve().parents[2]
    if (here / "main.py").is_file():
        return here
    return cwd


def launch_root_symlink_path() -> Path:
    """No '#' and no spaces — safe for launchd WorkingDirectory and log paths."""
    return Path.home() / ".deepsignal" / LAUNCH_ROOT_SYMLINK_NAME


def path_needs_launch_sanitize(path: Path) -> bool:
    text = path.as_posix()
    return "#" in text


def resolve_launch_root(
    root: Path,
    *,
    sanitize: bool = True,
) -> tuple[Path, dict[str, Any]]:
    """Return path used in plist (symlink without # when needed)."""
    meta: dict[str, Any] = {
        "project_root": root.as_posix(),
        "launch_root": root.as_posix(),
        "sanitized": False,
        "symlink_path": None,
        "symlink_created": False,
        "sanitize_message": "",
    }
    if not sanitize or not path_needs_launch_sanitize(root):
        return root, meta

    link = launch_root_symlink_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    target = root.resolve()
    if link.is_symlink():
        existing_target = link.resolve()
        if existing_target != target:
            meta["sanitize_message"] = (
                f"Symlink exists but points elsewhere: {link} -> {existing_target} (expected {target})"
            )
            return link, meta
    elif link.exists():
        meta["sanitize_message"] = f"Cannot create launch symlink; path exists and is not a symlink: {link}"
        return root, meta
    else:
        link.symlink_to(target)
        meta["symlink_created"] = True

    # Use symlink path (not .resolve()) so plist paths do not contain '#'.
    launch_root = link
    meta.update(
        {
            "launch_root": launch_root.as_posix(),
            "sanitized": True,
            "symlink_path": link.as_posix(),
            "sanitize_message": f"Using launch symlink (no '#'): {launch_root}",
        }
    )
    return launch_root, meta


def is_homebrew_python_path(path: str | Path) -> bool:
    lowered = str(path).lower()
    return "cellar/python" in lowered or "/opt/homebrew/" in lowered or "/usr/local/Cellar/" in lowered


def venv_python_plist_path(launch_root: Path) -> str:
    """
    Literal <launch_root>/.venv/bin/python for launchd ProgramArguments[0].
    No resolve(), realpath(), absolute(), or Homebrew fallback.
    """
    base = os.path.normpath(os.path.expanduser(str(launch_root)))
    py = os.path.join(base, ".venv", "bin", "python")
    if not os.path.isfile(py):
        raise FileNotFoundError(
            f"venv python missing: {py}. Run ./scripts/setup_macos.sh from project root first."
        )
    if is_homebrew_python_path(py):
        raise ValueError(f"refusing Homebrew python for launchd: {py}")
    return py


def require_venv_python(launch_root: Path) -> str:
    """Block install when venv python is absent; never fall back to sys.executable."""
    path = venv_python_plist_path(launch_root)
    if not path.endswith("/.venv/bin/python") and ".venv/bin/python" not in path:
        raise ValueError(f"invalid venv python path: {path}")
    return path


def resolve_python_executable(root: Path) -> Path:
    """Launchd/runner-test: project venv only (no Homebrew fallback)."""
    return Path(require_venv_python(root))


def probe_python_runtime(python_path: str, *, cwd: str) -> dict[str, Any]:
    script = (
        "import sys\n"
        "print(sys.executable)\n"
        "print(getattr(sys, 'prefix', ''))\n"
        "print('true' if (getattr(sys, 'base_prefix', sys.prefix) != sys.prefix "
        "or hasattr(sys, 'real_prefix')) else 'false')\n"
    )
    proc = subprocess.run(
        [python_path, "-c", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30.0,
        check=False,
    )
    lines = (proc.stdout or "").strip().splitlines()
    return {
        "returncode": proc.returncode,
        "sys_executable": lines[0] if len(lines) > 0 else "",
        "sys_prefix": lines[1] if len(lines) > 1 else "",
        "venv_detected": (lines[2] if len(lines) > 2 else "").lower() == "true",
        "stderr": (proc.stderr or "").strip()[:500],
    }


def read_plist_program_python() -> str | None:
    path = plist_path()
    if not path.is_file():
        return None
    try:
        data = plistlib.loads(path.read_bytes())
        args = data.get("ProgramArguments") or []
        return str(args[0]) if args else None
    except (OSError, plistlib.InvalidFileException):
        return None


def read_startup_log_excerpt(log_path: Path, *, max_lines: int = 30) -> list[str]:
    if not log_path.is_file():
        return []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    block: list[str] = []
    for line in lines:
        if "[runner startup]" in line:
            block = [line]
        elif block and (line.startswith("[runner startup]") or line.startswith("python:") or line.startswith("sys.executable:") or line.startswith("venv:") or "import:" in line):
            block.append(line)
        elif block and line.strip() == "":
            break
        elif block and not line.startswith("["):
            block.append(line)
    if block:
        return block[-max_lines:]
    return [ln for ln in lines if "[runner startup]" in ln or "import:" in ln][-max_lines:]


def verify_after_bootstrap(
    stdout_log: Path,
    *,
    launch_root: str,
    wait_seconds: float = 4.0,
) -> dict[str, Any]:
    import time

    time.sleep(max(wait_seconds, 0.5))
    ctl = _launchctl_print_status()
    plist_py = read_plist_program_python()
    runtime: dict[str, Any] = {}
    if plist_py and os.path.isfile(plist_py):
        runtime = probe_python_runtime(plist_py, cwd=launch_root)
    startup_lines = read_startup_log_excerpt(stdout_log)
    pandas_ok = any("pandas import: OK" in ln for ln in startup_lines)
    return {
        "launchctl": ctl,
        "plist_python": plist_py,
        "runtime_probe": runtime,
        "startup_log_lines": startup_lines,
        "startup_pandas_ok": pandas_ok,
        "running": bool(ctl.get("running")),
    }


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return launch_agents_dir() / PLIST_NAME


def ensure_runtime_directories(root: Path, cfg: LaunchdRunnerConfig) -> dict[str, str]:
    created: dict[str, str] = {}
    for rel in ("logs", cfg.output_dir):
        path = root / rel
        existed = path.is_dir()
        path.mkdir(parents=True, exist_ok=True)
        if not existed:
            created[rel] = path.as_posix()
    return created


def launchd_log_dir(*, use_home_logs: bool) -> Path:
    if use_home_logs:
        return Path.home() / ".deepsignal" / "logs"
    return Path("logs")


def log_paths(root: Path, *, use_home_logs: bool = False) -> tuple[Path, Path]:
    if use_home_logs:
        log_dir = launchd_log_dir(use_home_logs=True)
    else:
        log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "daily_ai_auto_runner.log", log_dir / "daily_ai_auto_runner.error.log"


def touch_log_files(stdout_log: Path, stderr_log: Path) -> tuple[Path, Path]:
    for path in (stdout_log, stderr_log):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
    return stdout_log, stderr_log


def config_record_path(root: Path) -> Path:
    return root / "logs" / "launchd_runner_config.json"


def diagnose_project_path(
    root: Path,
    *,
    python_executable: Path | None = None,
    cfg: LaunchdRunnerConfig | None = None,
) -> list[dict[str, str]]:
    cfg = cfg or LaunchdRunnerConfig()
    if python_executable is not None:
        py = python_executable
    else:
        try:
            py = Path(require_venv_python(root))
        except (FileNotFoundError, ValueError):
            py = Path(os.path.join(os.path.normpath(str(root)), ".venv", "bin", "python"))
    issues: list[dict[str, str]] = []
    text = root.as_posix()

    def add(severity: str, code: str, message: str) -> None:
        issues.append({"severity": severity, "code": code, "message": message})

    if not root.is_dir():
        add("error", "missing_project_root", f"Project root does not exist: {root}")
    if not (root / "main.py").is_file():
        add("error", "missing_main_py", f"main.py not found: {root / 'main.py'}")
    if "#" in text:
        add("warning", "hash_in_path", PATH_HASH_WARNING)
    if " " in text:
        add("warning", "space_in_path", "Project path contains spaces; launchd may mis-parse paths on some setups.")
    if re.search(r"[^\w./\-+#% ]", text):
        add("warning", "special_chars", "Project path contains unusual characters; verify launchd paths manually.")
    if not py.is_file():
        add("error", "missing_python", f"Python executable not found: {py}")
    logs_dir = root / "logs"
    if not logs_dir.is_dir():
        add("warning", "logs_missing", f"logs/ directory missing (will be created): {logs_dir}")
    out_dir = root / cfg.output_dir
    if not out_dir.is_dir():
        add("warning", "outputs_missing", f"{cfg.output_dir}/ directory missing (will be created): {out_dir}")
    return issues


def format_diagnostic_warnings(issues: list[dict[str, str]]) -> str:
    warnings = [i for i in issues if i.get("severity") in ("warning", "error")]
    if not warnings:
        return ""
    lines = ["[launchd warning]"]
    for item in warnings:
        lines.append(item.get("message", ""))
    return "\n".join(lines)


def _main_py_for_launchd(root: Path) -> Path:
    """Prefer non-resolved path so launchd never sees '#' via symlink target."""
    main_py = root / "main.py"
    if main_py.is_file():
        return main_py.absolute()
    resolved = main_py.resolve()
    if resolved.is_file():
        return resolved
    return main_py


def build_runner_argv(root: Path, cfg: LaunchdRunnerConfig) -> list[str]:
    main_py = _main_py_for_launchd(root)
    argv = [
        str(main_py),
        "daily-ai-auto-runner",
        "--broker",
        cfg.broker,
        "--output-dir",
        str(cfg.output_dir),
        "--plan-time",
        cfg.plan_time,
        "--report-time",
        cfg.report_time,
        "--max-order-value",
        str(cfg.max_order_value),
        "--max-single-order-value",
        str(cfg.max_single_order_value),
        "--max-total-order-value",
        str(cfg.max_total_order_value),
        "--max-orders",
        str(int(cfg.max_orders)),
        "--expires-minutes",
        str(int(cfg.expires_minutes)),
        "--poll-interval",
        str(cfg.poll_interval),
        "--loop-sleep-seconds",
        str(cfg.loop_sleep_seconds),
        "--timeout-seconds",
        str(cfg.timeout_seconds),
    ]
    if cfg.network:
        argv.append("--network")
    if cfg.allow_test_plan_order:
        argv.append("--allow-test-plan-order")
    if cfg.ignore_safety_block_for_test:
        argv.append("--ignore-safety-block-for-test")
    return argv


def build_plist_dict(
    *,
    root: Path,
    python_executable: str,
    cfg: LaunchdRunnerConfig,
    use_home_logs: bool = False,
) -> dict[str, Any]:
    stdout_log, stderr_log = log_paths(root, use_home_logs=use_home_logs)
    if is_homebrew_python_path(python_executable):
        raise ValueError(f"plist python must be venv shim, not Homebrew: {python_executable}")
    if ".venv/bin/python" not in python_executable:
        raise ValueError(f"plist python must end with .venv/bin/python: {python_executable}")
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [python_executable] + build_runner_argv(root, cfg),
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(stdout_log),
        "StandardErrorPath": str(stderr_log),
        "EnvironmentVariables": {
            "DEEPSIGNAL_PROJECT_ROOT": str(root),
            "PYTHONUNBUFFERED": "1",
        },
    }


def validate_plist_install(
    plist_file: Path,
    *,
    launch_root: Path,
    python_executable: str,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    def err(code: str, message: str) -> None:
        errors.append({"severity": "error", "code": code, "message": message})

    if not plist_file.is_file():
        err("plist_missing", f"Plist not found after write: {plist_file}")
        return errors

    try:
        data = plistlib.loads(plist_file.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        err("plist_invalid", f"Plist unreadable: {exc}")
        return errors

    wd = Path(str(data.get("WorkingDirectory", "")))
    if not wd.is_dir():
        err("working_directory_missing", f"WorkingDirectory does not exist: {wd}")

    for key in ("StandardOutPath", "StandardErrorPath"):
        log_path = Path(str(data.get(key, "")))
        if not log_path.parent.is_dir():
            err(f"{key}_parent_missing", f"{key} parent directory missing: {log_path.parent}")

    args = data.get("ProgramArguments") or []
    if not args:
        err("empty_program_arguments", "ProgramArguments is empty")
    else:
        prog0s = str(args[0])
        if is_homebrew_python_path(prog0s):
            err("homebrew_python", f"ProgramArguments[0] must not be Homebrew python: {prog0s}")
        if ".venv/bin/python" not in prog0s:
            err("not_venv_python", f"ProgramArguments[0] must be launch_root/.venv/bin/python: {prog0s}")
        if not os.path.isfile(prog0s):
            err("python_missing", f"ProgramArguments[0] not found: {prog0s}")
        if len(args) > 1:
            main_py = Path(str(args[1]))
            if not main_py.is_file():
                err("main_py_missing", f"ProgramArguments[1] main.py not found: {main_py}")

    if not os.path.isfile(python_executable):
        err("python_executable_missing", f"Python executable not found: {python_executable}")
    if is_homebrew_python_path(python_executable):
        err("homebrew_python", f"Python executable must be venv shim: {python_executable}")
    if not (launch_root / "main.py").is_file():
        err("launch_root_main_missing", f"main.py missing under launch root: {launch_root}")

    return errors


def write_plist(
    root: Path,
    cfg: LaunchdRunnerConfig,
    *,
    python_executable: str | None = None,
    launch_root: Path | None = None,
    project_root_actual: Path | None = None,
    sanitize_meta: dict[str, Any] | None = None,
) -> Path:
    launch_agents_dir().mkdir(parents=True, exist_ok=True)
    actual = project_root_actual or root
    effective = launch_root or root
    py = python_executable or require_venv_python(effective)
    if not (effective / "main.py").is_file():
        raise FileNotFoundError(f"main.py not found under launch root: {effective}")

    use_home_logs = bool(sanitize_meta and sanitize_meta.get("sanitized"))
    stdout_log, stderr_log = log_paths(effective, use_home_logs=use_home_logs)
    touch_log_files(stdout_log, stderr_log)
    payload = build_plist_dict(
        root=effective,
        python_executable=py,
        cfg=cfg,
        use_home_logs=use_home_logs,
    )
    path = plist_path()
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)

    validation_errors = validate_plist_install(path, launch_root=effective, python_executable=py)
    if validation_errors:
        messages = "; ".join(e["message"] for e in validation_errors)
        raise ValueError(f"Plist validation failed: {messages}")

    record = {
        "label": LAUNCHD_LABEL,
        "plist_path": path.as_posix(),
        "project_root": actual.as_posix(),
        "launch_root": effective.as_posix(),
        "path_sanitize": sanitize_meta or {},
        "python_executable": py,
        "stdout_log": payload["StandardOutPath"],
        "stderr_log": payload["StandardErrorPath"],
        "runner_config": cfg.to_dict(),
        "program_arguments": payload["ProgramArguments"],
    }
    config_record_path(actual).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _gui_domain() -> str:
    uid = os.getuid()
    return f"gui/{uid}"


def _run_launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def parse_launchctl_print(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {
        "loaded": False,
        "running": False,
        "state": None,
        "pid": None,
        "last_exit_code": None,
        "last_exit_reason": None,
        "active_count": None,
        "runs": None,
        "program": None,
        "working_directory": None,
        "stdout_path": None,
        "stderr_path": None,
        "raw_excerpt": (text or "").strip()[:2000],
    }
    if not text.strip():
        return parsed

    parsed["loaded"] = "path = " in text or "program = " in text

    m = re.search(r"state\s*=\s*(\S+)", text)
    if m:
        parsed["state"] = m.group(1)
        parsed["running"] = m.group(1) == "running"

    m = re.search(r"pid\s*=\s*(\d+)", text)
    if m:
        parsed["pid"] = int(m.group(1))

    m = re.search(r"active count\s*=\s*(\d+)", text)
    if m:
        parsed["active_count"] = int(m.group(1))
        if parsed["active_count"] > 0:
            parsed["running"] = True

    m = re.search(r"last exit code\s*=\s*([^\n]+)", text, re.IGNORECASE)
    if m:
        raw = m.group(1).strip()
        parsed["last_exit_reason"] = raw
        code_m = re.match(r"(\d+)", raw)
        if code_m:
            parsed["last_exit_code"] = int(code_m.group(1))

    m = re.search(r"runs\s*=\s*(\d+)", text)
    if m:
        parsed["runs"] = int(m.group(1))

    m = re.search(r"program\s*=\s*(.+)", text)
    if m:
        parsed["program"] = m.group(1).strip()

    m = re.search(r"working directory\s*=\s*(.+)", text)
    if m:
        parsed["working_directory"] = m.group(1).strip()

    m = re.search(r"stdout path\s*=\s*(.+)", text, re.IGNORECASE)
    if m:
        parsed["stdout_path"] = m.group(1).strip()

    m = re.search(r"stderr path\s*=\s*(.+)", text, re.IGNORECASE)
    if m:
        parsed["stderr_path"] = m.group(1).strip()

    return parsed


def _launchctl_print_status() -> dict[str, Any]:
    proc = _run_launchctl(["print", f"{_gui_domain()}/{LAUNCHD_LABEL}"])
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return {
            "loaded": False,
            "running": False,
            "detail": text.strip(),
            "print_returncode": proc.returncode,
        }
    parsed = parse_launchctl_print(text)
    parsed["detail"] = text.strip()[:500]
    parsed["print_returncode"] = 0
    return parsed


def load_plist(path: Path | None = None) -> dict[str, Any]:
    target = path or plist_path()
    result: dict[str, Any] = {
        "ok": False,
        "message": "",
        "method": None,
        "bootstrap_returncode": None,
        "bootstrap_stdout": "",
        "bootstrap_stderr": "",
        "fallback_used": False,
        "load_returncode": None,
        "load_stdout": "",
        "load_stderr": "",
    }
    if not target.is_file():
        result["message"] = f"plist not found: {target}"
        return result

    domain = _gui_domain()
    boot = _run_launchctl(["bootstrap", domain, str(target)])
    result["bootstrap_returncode"] = boot.returncode
    result["bootstrap_stdout"] = (boot.stdout or "").strip()
    result["bootstrap_stderr"] = (boot.stderr or "").strip()
    if boot.returncode == 0:
        result.update({"ok": True, "message": f"launchctl bootstrap {domain} OK", "method": "bootstrap"})
        return result

    load = _run_launchctl(["load", "-w", str(target)])
    result["fallback_used"] = True
    result["load_returncode"] = load.returncode
    result["load_stdout"] = (load.stdout or "").strip()
    result["load_stderr"] = (load.stderr or "").strip()
    if load.returncode == 0:
        result.update({"ok": True, "message": "launchctl load -w OK (fallback after bootstrap failed)", "method": "load"})
        return result

    parts = [
        f"bootstrap failed (rc={boot.returncode})",
        result["bootstrap_stderr"] or result["bootstrap_stdout"] or "(no bootstrap output)",
        f"load fallback failed (rc={load.returncode})",
        result["load_stderr"] or result["load_stdout"] or "(no load output)",
    ]
    result["message"] = " | ".join(p for p in parts if p)
    return result


def unload_plist(path: Path | None = None) -> tuple[bool, str]:
    target = path or plist_path()
    if not target.is_file():
        return True, "plist already absent"
    domain = _gui_domain()
    boot = _run_launchctl(["bootout", domain, str(target)])
    if boot.returncode == 0:
        return True, f"launchctl bootout {domain} OK"
    unload = _run_launchctl(["unload", "-w", str(target)])
    if unload.returncode == 0:
        return True, f"launchctl unload -w OK"
    if "No such process" in (boot.stderr or "") or "Could not find" in (boot.stderr or ""):
        return True, "service was not loaded"
    msg = (boot.stderr or boot.stdout or unload.stderr or unload.stdout or "launchctl failed").strip()
    return False, msg


def guess_running_false_causes(status: dict[str, Any]) -> list[str]:
    causes: list[str] = []
    ctl = status.get("launchctl") if isinstance(status.get("launchctl"), dict) else {}
    code = ctl.get("last_exit_code")
    state = ctl.get("state")
    project = str(status.get("project_root") or "")
    launch_root = str(status.get("launch_root") or project)
    stdout_log = Path(str(status.get("stdout_log") or ""))
    stderr_log = Path(str(status.get("stderr_log") or ""))

    if code == 78:
        causes.append(
            "last exit code 78 (EX_CONFIG): launchd could not apply plist paths — often '#' in WorkingDirectory or log paths."
        )
    elif code is not None and code != 0:
        causes.append(f"last exit code {code} ({ctl.get('last_exit_reason') or 'unknown'}): runner exited before staying up.")

    if "#" in project and not status.get("path_sanitized"):
        causes.append("Project path still contains '#'; re-run install-launchd to use Application Support symlink.")
    if "#" in launch_root:
        causes.append("Launch root still contains '#'; launchd may keep failing until a symlink path is used.")

    if state and state != "running":
        causes.append(f"launchd state is '{state}' (not running).")

    if not stdout_log.is_file() and not stderr_log.is_file():
        causes.append("Stdout/stderr log files were never created — process likely never spawned (config/path issue).")
    elif not stdout_log.stat().st_size and not stderr_log.stat().st_size:
        causes.append("Log files exist but are empty — runner may crash immediately or never start.")

    py = status.get("python_executable")
    if py and not Path(str(py)).is_file():
        causes.append(f"Python executable missing: {py}")
    if py and "Cellar/python" in str(py):
        launch = Path(str(status.get("launch_root") or ""))
        if (launch / ".venv" / "bin" / "python").is_file():
            causes.append(
                "Plist uses Homebrew Python without venv site-packages — run install-launchd again."
            )

    if not causes:
        causes.append("Run: python main.py launchd-runner-test — separates launchd/OS issues from runner import errors.")
    return causes


def install_launchd(
    cfg: LaunchdRunnerConfig,
    *,
    project_dir: str | Path | None = None,
    load_now: bool = True,
    sanitize_path: bool = True,
) -> dict[str, Any]:
    actual = project_root(project_dir)
    launch_root, sanitize_meta = resolve_launch_root(actual, sanitize=sanitize_path)
    py = require_venv_python(launch_root)
    diagnostics = diagnose_project_path(
        launch_root,
        python_executable=Path(py),
        cfg=cfg,
    )
    blocking = [i for i in diagnostics if i.get("severity") == "error"]
    if blocking:
        raise ValueError("; ".join(i["message"] for i in blocking))

    ensure_runtime_directories(actual, cfg)
    ensure_runtime_directories(launch_root, cfg)
    use_home = bool(sanitize_meta.get("sanitized"))
    stdout_log, stderr_log = log_paths(launch_root, use_home_logs=use_home)
    touch_log_files(stdout_log, stderr_log)

    path = write_plist(
        actual,
        cfg,
        python_executable=py,
        launch_root=launch_root,
        project_root_actual=actual,
        sanitize_meta=sanitize_meta,
    )

    load_result: dict[str, Any] = {"ok": False, "message": "skipped (--no-load)"}
    post_install: dict[str, Any] = {}
    if load_now:
        load_result = load_plist(path)
        if load_result.get("ok"):
            post_install = verify_after_bootstrap(stdout_log, launch_root=str(launch_root))

    use_home = bool(sanitize_meta.get("sanitized"))
    stdout_log, stderr_log = log_paths(launch_root, use_home_logs=use_home)
    return {
        "plist_path": path.as_posix(),
        "project_root": actual.as_posix(),
        "launch_root": launch_root.as_posix(),
        "path_sanitized": bool(sanitize_meta.get("sanitized")),
        "sanitize_meta": sanitize_meta,
        "python_executable": py,
        "stdout_log": stdout_log.as_posix(),
        "stderr_log": stderr_log.as_posix(),
        "loaded": load_result.get("ok", False),
        "load_message": load_result.get("message"),
        "load_detail": load_result,
        "post_install": post_install,
        "label": LAUNCHD_LABEL,
        "diagnostics": diagnostics,
        "validation_ok": True,
    }


def uninstall_launchd(*, unload: bool = True, remove_plist: bool = True) -> dict[str, Any]:
    path = plist_path()
    unloaded = False
    unload_message = "skipped"
    if unload and path.is_file():
        unloaded, unload_message = unload_plist(path)
    removed = False
    if remove_plist and path.is_file():
        path.unlink()
        removed = True
    return {
        "plist_path": path.as_posix(),
        "unloaded": unloaded,
        "unload_message": unload_message,
        "plist_removed": removed,
    }


def launchd_status(*, project_dir: str | Path | None = None) -> dict[str, Any]:
    actual = project_root(project_dir)
    path = plist_path()
    record_path = config_record_path(actual)
    record: dict[str, Any] = {}
    if record_path.is_file():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {}

    launch_root = Path(record.get("launch_root") or actual)
    sanitize_meta = record.get("path_sanitize") if isinstance(record.get("path_sanitize"), dict) else {}
    use_home = bool(sanitize_meta.get("sanitized")) or bool(record.get("stdout_log", "").find("/.deepsignal/logs/") >= 0)
    stdout_log, stderr_log = log_paths(launch_root, use_home_logs=use_home)
    if record.get("stdout_log"):
        stdout_log = Path(str(record["stdout_log"]))
    if record.get("stderr_log"):
        stderr_log = Path(str(record["stderr_log"]))
    ctl = _launchctl_print_status() if path.is_file() else {"loaded": False, "detail": "plist not installed"}
    plist_py = read_plist_program_python()
    configured_py = record.get("python_executable") or plist_py
    runtime_probe: dict[str, Any] = {}
    if configured_py and os.path.isfile(str(configured_py)):
        runtime_probe = probe_python_runtime(str(configured_py), cwd=str(launch_root))

    status = {
        "label": LAUNCHD_LABEL,
        "plist_installed": path.is_file(),
        "plist_path": path.as_posix(),
        "project_root": actual.as_posix(),
        "launch_root": launch_root.as_posix(),
        "path_sanitized": bool(sanitize_meta.get("sanitized")),
        "stdout_log": stdout_log.as_posix(),
        "stderr_log": stderr_log.as_posix(),
        "stdout_log_exists": stdout_log.is_file(),
        "stderr_log_exists": stderr_log.is_file(),
        "stdout_log_bytes": stdout_log.stat().st_size if stdout_log.is_file() else 0,
        "stderr_log_bytes": stderr_log.stat().st_size if stderr_log.is_file() else 0,
        "install_record": record,
        "launchctl": ctl,
        "python_executable": configured_py,
        "plist_python": plist_py,
        "runtime_probe": runtime_probe,
        "startup_log_lines": read_startup_log_excerpt(stdout_log),
    }
    if ctl.get("loaded") and not ctl.get("running"):
        status["likely_causes"] = guess_running_false_causes(status)
    return status


def runner_test_env(launch_root: Path, actual_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DEEPSIGNAL_PROJECT_ROOT"] = str(actual_root)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run_launchd_runner_test(
    cfg: LaunchdRunnerConfig,
    *,
    project_dir: str | Path | None = None,
    timeout_seconds: float = 45.0,
    max_iterations: int = 1,
) -> dict[str, Any]:
    actual = project_root(project_dir)
    record_path = config_record_path(actual)
    launch_root = actual
    if record_path.is_file():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            launch_root = Path(record.get("launch_root") or actual)
        except (OSError, json.JSONDecodeError):
            record = {}
    else:
        record = {}
        launch_root, _ = resolve_launch_root(actual, sanitize=True)

    py = require_venv_python(launch_root)
    argv = [py] + build_runner_argv(launch_root, cfg) + ["--max-iterations", str(int(max_iterations))]
    env = runner_test_env(launch_root, actual)
    result: dict[str, Any] = {
        "project_root": actual.as_posix(),
        "launch_root": launch_root.as_posix(),
        "cwd": launch_root.as_posix(),
        "python_executable": py,
        "argv": argv,
        "import_ok": False,
        "import_error": None,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "exception": None,
        "timed_out": False,
    }

    try:
        imp = subprocess.run(
            [str(py), "-c", "import deepsignal; print('import_ok')"],
            cwd=launch_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=min(timeout_seconds, 30.0),
            check=False,
        )
        result["import_ok"] = imp.returncode == 0
        if imp.returncode != 0:
            result["import_error"] = (imp.stderr or imp.stdout or "").strip()
    except subprocess.TimeoutExpired:
        result["import_error"] = "import check timed out"
    except OSError as exc:
        result["import_error"] = str(exc)

    result["runtime_probe"] = probe_python_runtime(py, cwd=str(launch_root))
    try:
        pandas_proc = subprocess.run(
            [py, "-c", "import pandas, numpy; print('pandas_ok')"],
            cwd=str(launch_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=min(timeout_seconds, 30.0),
            check=False,
        )
        result["pandas_import_ok"] = pandas_proc.returncode == 0
        if pandas_proc.returncode != 0:
            result["pandas_import_error"] = (pandas_proc.stderr or pandas_proc.stdout or "").strip()
    except OSError as exc:
        result["pandas_import_ok"] = False
        result["pandas_import_error"] = str(exc)

    try:
        proc = subprocess.run(
            argv,
            cwd=launch_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        result["returncode"] = proc.returncode
        result["stdout"] = (proc.stdout or "")[-4000:]
        result["stderr"] = (proc.stderr or "")[-4000:]
    except subprocess.TimeoutExpired as exc:
        result["timed_out"] = True
        result["returncode"] = -1
        result["stdout"] = (exc.stdout or "")[-4000:] if exc.stdout else ""
        result["stderr"] = (exc.stderr or "")[-4000:] if exc.stderr else "runner subprocess timed out"
    except OSError as exc:
        result["exception"] = "".join(traceback.format_exception_only(type(exc), exc)).strip()

    return result


def format_runner_test_console(test: dict[str, Any]) -> str:
    lines = [
        "DeepSignal launchd-runner-test",
        f"Project root: {test.get('project_root')}",
        f"Launch root (cwd): {test.get('launch_root')}",
        f"Python: {test.get('python_executable')}",
        f"Argv: {' '.join(test.get('argv') or [])}",
        f"Import OK: {test.get('import_ok')}",
    ]
    if test.get("import_error"):
        lines.append(f"Import error: {test.get('import_error')}")
    probe = test.get("runtime_probe") if isinstance(test.get("runtime_probe"), dict) else {}
    if probe:
        lines.append(f"sys.executable (probe): {probe.get('sys_executable')}")
        lines.append(f"venv detected (probe): {probe.get('venv_detected')}")
    if "pandas_import_ok" in test:
        lines.append(f"pandas/numpy import: {'OK' if test.get('pandas_import_ok') else 'FAIL'}")
        if test.get("pandas_import_error"):
            lines.append(f"pandas error: {test.get('pandas_import_error')}")
    if test.get("timed_out"):
        lines.append("Runner: TIMED OUT")
    elif test.get("exception"):
        lines.append(f"Runner exception: {test.get('exception')}")
    else:
        lines.append(f"Runner exit code: {test.get('returncode')}")
    if test.get("stdout"):
        lines.extend(["--- stdout ---", test["stdout"]])
    if test.get("stderr"):
        lines.extend(["--- stderr ---", test["stderr"]])
    return "\n".join(lines)


def format_install_console(result: dict[str, Any]) -> str:
    warn = format_diagnostic_warnings(result.get("diagnostics") or [])
    lines: list[str] = []
    if warn:
        lines.append(warn)
        lines.append("")

    lines.extend(
        [
            "DeepSignal launchd install finished",
            f"Label: {result.get('label')}",
            f"Plist: {result.get('plist_path')}",
            f"Project: {result.get('project_root')}",
            f"Launch root (plist): {result.get('launch_root')}",
        ]
    )
    if result.get("path_sanitized"):
        meta = result.get("sanitize_meta") or {}
        lines.append(f"Path sanitize: {meta.get('sanitize_message')}")
    lines.extend(
        [
            f"Python: {result.get('python_executable')}",
            f"Stdout log: {result.get('stdout_log')}",
            f"Stderr log: {result.get('stderr_log')}",
            f"Load: {result.get('load_message')}",
        ]
    )
    detail = result.get("load_detail") if isinstance(result.get("load_detail"), dict) else {}
    if detail.get("fallback_used"):
        lines.append("Load fallback: launchctl load -w (bootstrap failed)")
    if detail.get("bootstrap_stderr"):
        lines.append(f"Bootstrap stderr: {detail['bootstrap_stderr']}")
    if detail.get("bootstrap_stdout") and not detail.get("ok"):
        lines.append(f"Bootstrap stdout: {detail['bootstrap_stdout']}")
    if detail.get("load_stderr") and detail.get("fallback_used"):
        lines.append(f"Load stderr: {detail['load_stderr']}")

    post = result.get("post_install") if isinstance(result.get("post_install"), dict) else {}
    if post:
        lines.append(f"Post-install running: {post.get('running')}")
        probe = post.get("runtime_probe") if isinstance(post.get("runtime_probe"), dict) else {}
        if probe:
            lines.append(f"Probe sys.executable: {probe.get('sys_executable')}")
            lines.append(f"Probe venv detected: {probe.get('venv_detected')}")
        for ln in post.get("startup_log_lines") or []:
            lines.append(f"  {ln}")
        if post.get("startup_pandas_ok") is False:
            lines.append("WARNING: startup log missing pandas import OK — check stderr log")

    if not result.get("loaded"):
        lines.append("수동 로드: launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.deepsignal.auto_runner.plist")
    lines.extend(
        [
            "",
            "상태 확인: python main.py launchd-status",
            "Runner dry-run: python main.py launchd-runner-test",
            "제거: python main.py uninstall-launchd",
        ]
    )
    return "\n".join(lines)


def launchd_config_from_namespace(args: Any) -> LaunchdRunnerConfig:
    return LaunchdRunnerConfig(
        broker=str(getattr(args, "broker", "kis") or "kis"),
        network=bool(getattr(args, "network", False)),
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
        plan_time=str(getattr(args, "plan_time", "09:05") or "09:05"),
        report_time=str(getattr(args, "report_time", "15:40") or "15:40"),
        max_order_value=float(getattr(args, "max_order_value", 300_000.0) or 300_000.0),
        max_single_order_value=float(getattr(args, "max_single_order_value", 300_000.0) or 300_000.0),
        max_total_order_value=float(getattr(args, "max_total_order_value", 300_000.0) or 300_000.0),
        max_orders=int(getattr(args, "max_orders", 1) or 1),
        expires_minutes=int(getattr(args, "expires_minutes", 420) or 420),
        poll_interval=float(getattr(args, "poll_interval", 3.0) or 3.0),
        loop_sleep_seconds=float(getattr(args, "loop_sleep_seconds", 15.0) or 15.0),
        timeout_seconds=float(getattr(args, "timeout_seconds", 10.0) or 10.0),
        allow_test_plan_order=bool(getattr(args, "allow_test_plan_order", False)),
        ignore_safety_block_for_test=bool(getattr(args, "ignore_safety_block_for_test", False)),
    )


def add_launchd_runner_arguments(parser: Any) -> None:
    parser.add_argument("--broker", type=str, choices=["kis"], default="kis", metavar="NAME")
    parser.add_argument("--network", action="store_true", help="KIS 조회로 plan 생성")
    parser.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    parser.add_argument("--plan-time", type=str, default="09:05", metavar="HH:MM")
    parser.add_argument("--report-time", type=str, default="15:40", metavar="HH:MM")
    parser.add_argument("--max-order-value", type=float, default=300_000.0, metavar="AMT")
    parser.add_argument("--max-single-order-value", type=float, default=300_000.0, metavar="AMT")
    parser.add_argument("--max-total-order-value", type=float, default=300_000.0, metavar="AMT")
    parser.add_argument("--max-orders", type=int, default=1, metavar="N")
    parser.add_argument("--expires-minutes", type=int, default=420, metavar="N")
    parser.add_argument("--poll-interval", type=float, default=3.0, metavar="SEC")
    parser.add_argument("--loop-sleep-seconds", type=float, default=15.0, metavar="SEC")
    parser.add_argument("--timeout-seconds", type=float, default=10.0, metavar="SEC")
    parser.add_argument("--allow-test-plan-order", action="store_true")
    parser.add_argument("--ignore-safety-block-for-test", action="store_true")


def format_status_console(status: dict[str, Any]) -> str:
    ctl = status.get("launchctl") if isinstance(status.get("launchctl"), dict) else {}
    lines = [
        "DeepSignal launchd status",
        f"Label: {status.get('label')}",
        f"Plist installed: {status.get('plist_installed')}",
        f"Plist path: {status.get('plist_path')}",
        f"Project root: {status.get('project_root')}",
        f"Launch root: {status.get('launch_root')}",
        f"Path sanitized (no # in plist): {status.get('path_sanitized')}",
        f"launchctl loaded: {ctl.get('loaded')}",
        f"launchctl running: {ctl.get('running', 'n/a')}",
        f"launchctl state: {ctl.get('state', 'n/a')}",
        f"launchctl pid: {ctl.get('pid', 'n/a')}",
        f"last exit code: {ctl.get('last_exit_code', 'n/a')}",
        f"last exit reason: {ctl.get('last_exit_reason', 'n/a')}",
        f"active count: {ctl.get('active_count', 'n/a')}",
        f"runs: {ctl.get('runs', 'n/a')}",
        f"Stdout log: {status.get('stdout_log')} ({status.get('stdout_log_bytes', 0)} bytes)",
        f"Stderr log: {status.get('stderr_log')} ({status.get('stderr_log_bytes', 0)} bytes)",
        f"Configured python: {status.get('python_executable')}",
        f"Plist ProgramArguments[0]: {status.get('plist_python')}",
    ]
    probe = status.get("runtime_probe") if isinstance(status.get("runtime_probe"), dict) else {}
    if probe:
        lines.append(f"Probe sys.executable: {probe.get('sys_executable')}")
        lines.append(f"Probe venv detected: {probe.get('venv_detected')}")
    startup = status.get("startup_log_lines") or []
    if startup:
        lines.append("Startup log:")
        for ln in startup:
            lines.append(f"  {ln}")
    record = status.get("install_record")
    if isinstance(record, dict) and record.get("runner_config"):
        rc = record["runner_config"]
        lines.append(f"Plan/Report: {rc.get('plan_time')} / {rc.get('report_time')}")

    if ctl.get("loaded") and not ctl.get("running"):
        lines.append("")
        lines.append("[likely causes]")
        for cause in status.get("likely_causes") or guess_running_false_causes(status):
            lines.append(f"- {cause}")
        lines.append("")
        lines.append("Next: python main.py launchd-runner-test")
        lines.append("Fix: python main.py install-launchd ...  (re-applies symlink + log touch)")

    return "\n".join(lines)
