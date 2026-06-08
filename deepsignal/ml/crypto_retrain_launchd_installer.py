"""macOS launchd — nightly crypto-retrain-lgbm (03:10 KST approx via local time)."""

from __future__ import annotations

import json
import plistlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from deepsignal.live_trading.ops.launchd_installer import (
    diagnose_project_path,
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

RETRAIN_LABEL = "com.deepsignal.crypto_retrain_lgbm"
RETRAIN_PLIST = f"{RETRAIN_LABEL}.plist"


@dataclass
class CryptoRetrainLaunchdConfig:
    output_dir: str = "outputs"
    horizon_minutes: int = 5
    hour: int = 3
    minute: int = 10

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / RETRAIN_PLIST


def log_paths() -> tuple[Path, Path]:
    d = Path.home() / ".deepsignal" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "crypto_retrain_lgbm.log", d / "crypto_retrain_lgbm.error.log"


def build_argv(launch_root: Path, cfg: CryptoRetrainLaunchdConfig) -> list[str]:
    main_py = _main_py_for_launchd(launch_root)
    return [
        str(main_py),
        "crypto-retrain-lgbm",
        "--output-dir",
        str(cfg.output_dir),
        "--horizon",
        str(int(cfg.horizon_minutes)),
        "--also-seq",
    ]


def install_crypto_retrain_launchd(
    cfg: CryptoRetrainLaunchdConfig,
    *,
    project_dir: str | Path | None = None,
    load_now: bool = True,
) -> dict[str, Any]:
    actual = project_root(project_dir)
    launch_root, _meta = resolve_launch_root(actual, sanitize=True)
    py = require_venv_python(launch_root)
    diagnostics = diagnose_project_path(launch_root, python_executable=Path(py), cfg=None)

    stdout_log, stderr_log = log_paths()
    touch_log_files(stdout_log, stderr_log)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "Label": RETRAIN_LABEL,
        "ProgramArguments": [py] + build_argv(launch_root, cfg),
        "WorkingDirectory": str(launch_root),
        "StartCalendarInterval": {"Hour": int(cfg.hour), "Minute": int(cfg.minute)},
        "StandardOutPath": str(stdout_log),
        "StandardErrorPath": str(stderr_log),
        "EnvironmentVariables": {
            "DEEPSIGNAL_PROJECT_ROOT": str(launch_root),
            "PYTHONUNBUFFERED": "1",
        },
    }
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)

    errors = validate_plist_install(path, launch_root=launch_root, python_executable=py)
    if errors:
        raise ValueError("; ".join(e["message"] for e in errors))

    load_result: dict[str, Any] = {"ok": False}
    if load_now:
        unload_plist(path)
        proc = _run_launchctl(["bootstrap", _gui_domain(), str(path)])
        load_result = {"ok": proc.returncode == 0, "detail": (proc.stdout or proc.stderr or "")[:300]}

    record = launch_root / "logs" / "crypto_retrain_launchd.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(
        json.dumps({"config": cfg.to_dict(), "plist": path.as_posix()}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "label": RETRAIN_LABEL,
        "plist_path": path.as_posix(),
        "loaded": load_result.get("ok"),
        "schedule": f"{cfg.hour:02d}:{cfg.minute:02d} daily",
        "diagnostics": diagnostics,
    }


def uninstall_crypto_retrain_launchd() -> dict[str, Any]:
    path = plist_path()
    unloaded, msg = unload_plist(path) if path.is_file() else (False, "no plist")
    removed = False
    if path.is_file():
        path.unlink()
        removed = True
    return {"unloaded": unloaded, "unload_message": msg, "plist_removed": removed}
