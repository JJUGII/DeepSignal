"""macOS launchd installer for binance-stream (user LaunchAgent)."""

from __future__ import annotations

import json
import plistlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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

BINANCE_STREAM_LABEL = "com.deepsignal.binance_stream"
BINANCE_STREAM_PLIST = f"{BINANCE_STREAM_LABEL}.plist"


@dataclass
class BinanceStreamLaunchdConfig:
    top_n: int = 30
    output_dir: str = "outputs/binance_stream"
    depth_levels: int = 20

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return launch_agents_dir() / BINANCE_STREAM_PLIST


def log_paths() -> tuple[Path, Path]:
    log_dir = Path.home() / ".deepsignal" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "binance_stream.log", log_dir / "binance_stream.error.log"


def build_argv(launch_root: Path, cfg: BinanceStreamLaunchdConfig) -> list[str]:
    main_py = _main_py_for_launchd(launch_root)
    return [
        str(main_py),
        "binance-stream",
        "--top",
        str(int(cfg.top_n)),
        "--output-dir",
        str(cfg.output_dir),
        "--depth-levels",
        str(int(cfg.depth_levels)),
        "--duration",
        "0",
    ]


def install_binance_stream_launchd(
    cfg: BinanceStreamLaunchdConfig,
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
        "Label": BINANCE_STREAM_LABEL,
        "ProgramArguments": [py] + build_argv(launch_root, cfg),
        "WorkingDirectory": str(launch_root),
        "RunAtLoad": True,
        "KeepAlive": True,
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

    return {
        "label": BINANCE_STREAM_LABEL,
        "plist_path": path.as_posix(),
        "project_root": actual.as_posix(),
        "launch_root": launch_root.as_posix(),
        "stdout_log": stdout_log.as_posix(),
        "stderr_log": stderr_log.as_posix(),
        "loaded": load_result.get("ok", False),
        "load_detail": load_result,
        "config": cfg.to_dict(),
        "diagnostics": diagnostics,
    }


def uninstall_binance_stream_launchd(*, unload: bool = True, remove_plist: bool = True) -> dict[str, Any]:
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


def binance_stream_launchd_status() -> dict[str, Any]:
    path = plist_path()
    if not path.is_file():
        return {"installed": False, "plist_path": path.as_posix()}
    proc = _run_launchctl(["print", f"{_gui_domain()}/{BINANCE_STREAM_LABEL}"])
    text = (proc.stdout or "") + (proc.stderr or "")
    return {
        "installed": True,
        "plist_path": path.as_posix(),
        "loaded": proc.returncode == 0,
        "detail": text.strip()[:800],
    }
