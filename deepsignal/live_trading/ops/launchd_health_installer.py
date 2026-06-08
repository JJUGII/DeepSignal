"""Install macOS LaunchAgent for post-login launchd health check."""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any

from deepsignal.live_trading.launchd_health_check import HEALTH_LABEL
from deepsignal.live_trading.ops.launchd_installer import (
    diagnose_project_path,
    load_plist,
    project_root,
    require_venv_python,
    resolve_launch_root,
    touch_log_files,
    unload_plist,
    validate_plist_install,
    _gui_domain,
    _main_py_for_launchd,
    _run_launchctl,
)

HEALTH_PLIST_NAME = f"{HEALTH_LABEL}.plist"


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return launch_agents_dir() / HEALTH_PLIST_NAME


def log_paths() -> tuple[Path, Path]:
    log_dir = Path.home() / ".deepsignal" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "launchd_health_check.log", log_dir / "launchd_health_check.error.log"


def build_health_argv(launch_root: Path) -> list[str]:
    main_py = _main_py_for_launchd(launch_root)
    return [
        str(main_py),
        "launchd-health-check",
        "--from-launchd",
    ]


def install_launchd_health_check(
    *,
    project_dir: str | Path | None = None,
    load_now: bool = True,
    sanitize_path: bool = True,
) -> dict[str, Any]:
    actual = project_root(project_dir)
    launch_root, sanitize_meta = resolve_launch_root(actual, sanitize=sanitize_path)
    py = require_venv_python(launch_root)
    diagnostics = diagnose_project_path(launch_root, python_executable=Path(py), cfg=None)
    blocking = [i for i in diagnostics if i.get("severity") == "error"]
    if blocking:
        raise ValueError("; ".join(i["message"] for i in blocking))

    stdout_log, stderr_log = log_paths()
    touch_log_files(stdout_log, stderr_log)
    launch_agents_dir().mkdir(parents=True, exist_ok=True)

    payload = {
        "Label": HEALTH_LABEL,
        "ProgramArguments": [py] + build_health_argv(launch_root),
        "WorkingDirectory": str(launch_root),
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(stdout_log),
        "StandardErrorPath": str(stderr_log),
        "EnvironmentVariables": {
            "DEEPSIGNAL_PROJECT_ROOT": str(launch_root),
            "PYTHONUNBUFFERED": "1",
        },
    }
    path = plist_path()
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)

    errors = validate_plist_install(path, launch_root=launch_root, python_executable=py)
    if errors:
        raise ValueError("; ".join(e["message"] for e in errors))

    load_result: dict[str, Any] = {"ok": False, "message": "skipped"}
    if load_now:
        unload_plist(path)
        proc = _run_launchctl(["bootstrap", _gui_domain(), str(path)])
        load_result = {
            "ok": proc.returncode == 0,
            "message": (proc.stdout or proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }
        if not load_result["ok"]:
            load_result = load_plist(path)

    return {
        "label": HEALTH_LABEL,
        "plist_path": path.as_posix(),
        "project_root": actual.as_posix(),
        "launch_root": launch_root.as_posix(),
        "path_sanitize": sanitize_meta,
        "stdout_log": stdout_log.as_posix(),
        "stderr_log": stderr_log.as_posix(),
        "loaded": load_result.get("ok", False),
        "load_detail": load_result,
        "diagnostics": diagnostics,
    }


def uninstall_launchd_health_check(*, unload: bool = True, remove_plist: bool = True) -> dict[str, Any]:
    path = plist_path()
    unloaded = False
    msg = "skipped"
    if unload and path.is_file():
        unloaded, msg = unload_plist(path)
    removed = False
    if remove_plist and path.is_file():
        path.unlink()
        removed = True
    return {"unloaded": unloaded, "unload_message": msg, "plist_removed": removed}


def launchd_health_check_install_status() -> dict[str, Any]:
    path = plist_path()
    if not path.is_file():
        return {"installed": False, "plist_path": path.as_posix()}
    proc = _run_launchctl(["print", f"{_gui_domain()}/{HEALTH_LABEL}"])
    text = (proc.stdout or "") + (proc.stderr or "")
    from deepsignal.live_trading.launchd_installer import parse_launchctl_print

    parsed = parse_launchctl_print(text) if proc.returncode == 0 else {}
    return {
        "installed": True,
        "plist_path": path.as_posix(),
        "loaded": proc.returncode == 0,
        "running": parsed.get("running", False),
        "state": parsed.get("state"),
        "detail": text.strip()[:500],
    }


def format_install_console(result: dict[str, Any]) -> str:
    lines = [
        "DeepSignal launchd health-check install finished",
        f"Label: {result.get('label')}",
        f"Plist: {result.get('plist_path')}",
        f"Launch root: {result.get('launch_root')}",
        f"Stdout log: {result.get('stdout_log')}",
        f"Stderr log: {result.get('stderr_log')}",
        f"Load: {result.get('load_detail', {}).get('message', 'n/a')}",
        "",
        "로그인 후 약 90초 뒤 필수 프로세스 점검 → 실패 시 Telegram",
        "수동 점검: python main.py launchd-health-check",
        "상태: python main.py launchd-health-check-status",
    ]
    return "\n".join(lines)
