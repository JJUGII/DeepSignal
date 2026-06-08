"""macOS launchd installer for crypto-auto-runner (user LaunchAgent)."""

from __future__ import annotations

import json
import os
import plistlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from deepsignal.crypto_trading.crypto_env import (
    crypto_launchd_env_hints,
    diagnose_crypto_env,
    format_crypto_env_warnings,
    plist_contains_secrets,
    read_log_tail,
)
from deepsignal.scoring.analysis_conditions import DEFAULT_ANALYSIS_CONDITIONS
from deepsignal.live_trading.ops.launchd_installer import (
    diagnose_project_path,
    format_diagnostic_warnings,
    is_homebrew_python_path,
    load_plist,
    parse_launchctl_print,
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

CRYPTO_LAUNCHD_LABEL = "com.deepsignal.crypto_auto_runner"
CRYPTO_PLIST_NAME = f"{CRYPTO_LAUNCHD_LABEL}.plist"
CONFIG_RECORD_NAME = "crypto_launchd_runner_config.json"

_CRYPTO = DEFAULT_ANALYSIS_CONDITIONS.crypto


@dataclass
class CryptoLaunchdRunnerConfig:
    broker: str = "upbit"
    interval_minutes: float = 1.0
    max_order_value: float = 0.0
    take_profit_pct: float = _CRYPTO.take_profit_pct
    take_profit_buffer_pct: float = _CRYPTO.take_profit_buffer_pct
    stop_loss_pct: float = _CRYPTO.stop_loss_pct
    stop_loss_buffer_pct: float = _CRYPTO.stop_loss_buffer_pct
    min_volume_ratio: float = _CRYPTO.min_volume_ratio
    crypto_universe: str = _CRYPTO.market_universe
    max_buy_scan_markets: int = _CRYPTO.max_buy_scan_markets
    max_orders_per_day: int = 0
    poll: bool = True
    execute: bool = False
    output_dir: str = "outputs"
    wait_fill_seconds: float = 60.0
    fill_poll_interval: float = 3.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return launch_agents_dir() / CRYPTO_PLIST_NAME


def crypto_log_paths() -> tuple[Path, Path]:
    log_dir = Path.home() / ".deepsignal" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "crypto_auto_runner.log", log_dir / "crypto_auto_runner.error.log"


def config_record_path(root: Path) -> Path:
    path = root / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path / CONFIG_RECORD_NAME


def build_runner_argv(launch_root: Path, cfg: CryptoLaunchdRunnerConfig) -> list[str]:
    main_py = _main_py_for_launchd(launch_root)
    argv = [
        str(main_py),
        "crypto-auto-runner",
        "--broker",
        cfg.broker,
        "--interval-minutes",
        str(cfg.interval_minutes),
        "--max-order-value",
        str(cfg.max_order_value),
        "--take-profit-pct",
        str(cfg.take_profit_pct),
        "--take-profit-buffer-pct",
        str(cfg.take_profit_buffer_pct),
        "--stop-loss-pct",
        str(cfg.stop_loss_pct),
        "--stop-loss-buffer-pct",
        str(cfg.stop_loss_buffer_pct),
        "--min-volume-ratio",
        str(cfg.min_volume_ratio),
        "--crypto-universe",
        str(cfg.crypto_universe),
        "--max-scan-markets",
        str(int(cfg.max_buy_scan_markets)),
        "--max-orders-per-day",
        str(int(cfg.max_orders_per_day)),
        "--output-dir",
        str(cfg.output_dir),
        "--wait-fill-seconds",
        str(cfg.wait_fill_seconds),
        "--fill-poll-interval",
        str(cfg.fill_poll_interval),
    ]
    if cfg.poll:
        argv.append("--poll")
    if cfg.execute:
        argv.append("--execute")
    return argv


def build_plist_dict(
    *,
    launch_root: Path,
    python_executable: str,
    cfg: CryptoLaunchdRunnerConfig,
) -> dict[str, Any]:
    stdout_log, stderr_log = crypto_log_paths()
    if is_homebrew_python_path(python_executable):
        raise ValueError(f"plist python must be venv shim, not Homebrew: {python_executable}")
    if ".venv/bin/python" not in python_executable:
        raise ValueError(f"plist python must include .venv/bin/python: {python_executable}")
    return {
        "Label": CRYPTO_LAUNCHD_LABEL,
        "ProgramArguments": [python_executable] + build_runner_argv(launch_root, cfg),
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


def write_plist(
    actual_root: Path,
    cfg: CryptoLaunchdRunnerConfig,
    *,
    launch_root: Path,
    sanitize_meta: dict[str, Any],
) -> Path:
    launch_agents_dir().mkdir(parents=True, exist_ok=True)
    py = require_venv_python(launch_root)
    if not (launch_root / "main.py").is_file():
        raise FileNotFoundError(f"main.py not found under launch root: {launch_root}")

    stdout_log, stderr_log = crypto_log_paths()
    touch_log_files(stdout_log, stderr_log)
    payload = build_plist_dict(launch_root=launch_root, python_executable=py, cfg=cfg)
    path = plist_path()
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)

    validation_errors = validate_plist_install(path, launch_root=launch_root, python_executable=py)
    if validation_errors:
        raise ValueError("; ".join(e["message"] for e in validation_errors))

    with path.open("rb") as fh:
        plist_data = plistlib.load(fh)
    secret_violations = plist_contains_secrets(plist_data)
    if secret_violations:
        raise ValueError("plist must not contain secrets: " + "; ".join(secret_violations[:3]))

    record = {
        "label": CRYPTO_LAUNCHD_LABEL,
        "plist_path": path.as_posix(),
        "project_root": actual_root.as_posix(),
        "launch_root": launch_root.as_posix(),
        "path_sanitize": sanitize_meta,
        "python_executable": py,
        "stdout_log": payload["StandardOutPath"],
        "stderr_log": payload["StandardErrorPath"],
        "runner_config": cfg.to_dict(),
        "program_arguments": payload["ProgramArguments"],
    }
    config_record_path(actual_root).write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _launchctl_print_status() -> dict[str, Any]:
    proc = _run_launchctl(["print", f"{_gui_domain()}/{CRYPTO_LAUNCHD_LABEL}"])
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


def install_crypto_launchd(
    cfg: CryptoLaunchdRunnerConfig,
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

    env_diagnostics = diagnose_crypto_env(project_dir=actual)
    diagnostics = list(diagnostics) + list(env_diagnostics)

    (actual / cfg.output_dir).mkdir(parents=True, exist_ok=True)
    path = write_plist(actual, cfg, launch_root=launch_root, sanitize_meta=sanitize_meta)

    load_result: dict[str, Any] = {"ok": False, "message": "skipped (--no-load)"}
    if load_now:
        load_result = load_plist(path)

    stdout_log, stderr_log = crypto_log_paths()
    return {
        "label": CRYPTO_LAUNCHD_LABEL,
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
        "runner_config": cfg.to_dict(),
        "diagnostics": diagnostics,
        "env_diagnostics": env_diagnostics,
    }


def uninstall_crypto_launchd(*, unload: bool = True, remove_plist: bool = True) -> dict[str, Any]:
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


def crypto_launchd_status(*, project_dir: str | Path | None = None) -> dict[str, Any]:
    actual = project_root(project_dir)
    record: dict[str, Any] = {}
    rec_path = config_record_path(actual)
    if rec_path.is_file():
        try:
            record = json.loads(rec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {}

    launch_root = Path(record.get("launch_root") or actual)
    stdout_log, stderr_log = crypto_log_paths()
    ctl = _launchctl_print_status() if plist_path().is_file() else {"loaded": False, "detail": "plist not installed"}
    rc = record.get("runner_config") if isinstance(record.get("runner_config"), dict) else {}
    stderr_tail = read_log_tail(stderr_log)
    stdout_tail = read_log_tail(stdout_log)
    env_hints = crypto_launchd_env_hints(stderr_tail=stderr_tail, stdout_tail=stdout_tail)

    return {
        "label": CRYPTO_LAUNCHD_LABEL,
        "plist_installed": plist_path().is_file(),
        "plist_path": plist_path().as_posix(),
        "project_root": actual.as_posix(),
        "launch_root": launch_root.as_posix(),
        "path_sanitized": bool((record.get("path_sanitize") or {}).get("sanitized")),
        "stdout_log": stdout_log.as_posix(),
        "stderr_log": stderr_log.as_posix(),
        "stdout_log_bytes": stdout_log.stat().st_size if stdout_log.is_file() else 0,
        "stderr_log_bytes": stderr_log.stat().st_size if stderr_log.is_file() else 0,
        "install_record": record,
        "launchctl": ctl,
        "python_executable": record.get("python_executable"),
        "broker": rc.get("broker", "upbit"),
        "interval_minutes": rc.get("interval_minutes"),
        "poll": rc.get("poll"),
        "execute": rc.get("execute"),
        "max_order_value": rc.get("max_order_value"),
        "take_profit_pct": rc.get("take_profit_pct"),
        "take_profit_buffer_pct": rc.get("take_profit_buffer_pct"),
        "stop_loss_pct": rc.get("stop_loss_pct"),
        "stop_loss_buffer_pct": rc.get("stop_loss_buffer_pct"),
        "min_volume_ratio": rc.get("min_volume_ratio"),
        "max_order_value": rc.get("max_order_value"),
        "wait_fill_seconds": rc.get("wait_fill_seconds"),
        "fill_poll_interval": rc.get("fill_poll_interval"),
        "stderr_tail": stderr_tail,
        "stdout_tail": stdout_tail,
        "env_hints": env_hints,
    }


def launchd_config_from_namespace(args: Any) -> CryptoLaunchdRunnerConfig:
    return CryptoLaunchdRunnerConfig(
        broker=str(getattr(args, "broker", "upbit") or "upbit"),
        interval_minutes=float(getattr(args, "interval_minutes", 1.0) or 1.0),
        max_order_value=float(getattr(args, "max_order_value", 0.0) if hasattr(args, "max_order_value") else 0.0),
        take_profit_pct=float(getattr(args, "take_profit_pct", _CRYPTO.take_profit_pct) or _CRYPTO.take_profit_pct),
        take_profit_buffer_pct=float(
            getattr(args, "take_profit_buffer_pct", _CRYPTO.take_profit_buffer_pct) or _CRYPTO.take_profit_buffer_pct
        ),
        stop_loss_pct=float(getattr(args, "stop_loss_pct", _CRYPTO.stop_loss_pct) or _CRYPTO.stop_loss_pct),
        stop_loss_buffer_pct=float(getattr(args, "stop_loss_buffer_pct", 0.05) or 0.05),
        min_volume_ratio=float(getattr(args, "min_volume_ratio", _CRYPTO.min_volume_ratio) or _CRYPTO.min_volume_ratio),
        crypto_universe=str(getattr(args, "crypto_universe", _CRYPTO.market_universe) or _CRYPTO.market_universe),
        max_buy_scan_markets=int(getattr(args, "max_scan_markets", _CRYPTO.max_buy_scan_markets) or _CRYPTO.max_buy_scan_markets),
        max_orders_per_day=int(getattr(args, "max_orders_per_day", 0)),
        poll=bool(getattr(args, "poll", False)),
        execute=bool(getattr(args, "execute", False)),
        output_dir=str(getattr(args, "output_dir", "outputs") or "outputs"),
        wait_fill_seconds=float(getattr(args, "wait_fill_seconds", 60.0) or 60.0),
        fill_poll_interval=float(getattr(args, "fill_poll_interval", 3.0) or 3.0),
    )


def add_crypto_launchd_arguments(parser: Any) -> None:
    parser.add_argument("--broker", type=str, default="upbit", choices=["upbit"])
    parser.add_argument("--interval-minutes", type=float, default=1.0, metavar="MIN")
    parser.add_argument(
        "--max-order-value",
        type=float,
        default=0.0,
        metavar="KRW",
        help="0=가용잔고·점수 기반 자동 (기본), >0 이면 건당 상한",
    )
    parser.add_argument("--take-profit-pct", type=float, default=_CRYPTO.take_profit_pct, metavar="PCT")
    parser.add_argument(
        "--take-profit-buffer-pct",
        type=float,
        default=0.05,
        metavar="PCT",
        help="익절 근접 buffer(%%p)",
    )
    parser.add_argument("--stop-loss-pct", type=float, default=_CRYPTO.stop_loss_pct, metavar="PCT")
    parser.add_argument(
        "--stop-loss-buffer-pct",
        type=float,
        default=0.05,
        metavar="PCT",
        help="손절 근접 buffer(%%p)",
    )
    parser.add_argument(
        "--min-volume-ratio",
        type=float,
        default=_CRYPTO.min_volume_ratio,
        metavar="RATIO",
        help="BUY 거래량 ratio 최소값",
    )
    parser.add_argument(
        "--max-orders-per-day",
        type=int,
        default=0,
        metavar="N",
        help="0=자동(가용·macro), >0 이면 일일 BUY 상한",
    )
    from deepsignal.crypto_trading.crypto_universe import add_crypto_universe_cli_args

    add_crypto_universe_cli_args(parser)
    parser.add_argument("--output-dir", type=str, default="outputs", metavar="DIR")
    parser.add_argument("--poll", action="store_true", help="Telegram 승인 폴링")
    parser.add_argument("--execute", action="store_true", help="승인 시 실주문")
    parser.add_argument("--wait-fill-seconds", type=float, default=60.0, metavar="SEC")
    parser.add_argument("--fill-poll-interval", type=float, default=3.0, metavar="SEC")


def format_install_console(result: dict[str, Any]) -> str:
    warn = format_diagnostic_warnings(result.get("diagnostics") or [])
    env_warn = format_crypto_env_warnings(result.get("env_diagnostics") or [])
    lines: list[str] = []
    if env_warn:
        lines.append(env_warn)
        lines.append("")
    if warn:
        lines.append(warn.replace("install-launchd", "install-crypto-launchd"))
        lines.append("")
    lines.extend(
        [
            "DeepSignal crypto launchd install finished",
            f"Label: {result.get('label')}",
            f"Plist: {result.get('plist_path')}",
            f"Project: {result.get('project_root')}",
            f"Launch root: {result.get('launch_root')}",
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
            "",
            "상태 확인: python main.py crypto-launchd-status",
            "제거: python main.py uninstall-crypto-launchd",
        ]
    )
    return "\n".join(lines)


def format_status_console(status: dict[str, Any]) -> str:
    ctl = status.get("launchctl") if isinstance(status.get("launchctl"), dict) else {}
    interval = status.get("interval_minutes")
    lines = [
        "DeepSignal crypto launchd status",
        f"Label: {status.get('label')}",
        f"Plist installed: {status.get('plist_installed')}",
        f"Plist path: {status.get('plist_path')}",
        f"Project root: {status.get('project_root')}",
        f"Launch root: {status.get('launch_root')}",
        f"Path sanitized: {status.get('path_sanitized')}",
        f"launchctl loaded: {ctl.get('loaded')}",
        f"launchctl running: {ctl.get('running', 'n/a')}",
        f"launchctl state: {ctl.get('state', 'n/a')}",
        f"launchctl pid: {ctl.get('pid', 'n/a')}",
        f"interval: {interval}m" if interval is not None else "interval: n/a",
        f"broker: {status.get('broker', 'n/a')}",
        f"execute: {status.get('execute', 'n/a')}",
        f"poll: {status.get('poll', 'n/a')}",
        f"take_profit_pct: {status.get('take_profit_pct', 'n/a')}",
        f"take_profit_buffer_pct: {status.get('take_profit_buffer_pct', 'n/a')}",
        f"stop_loss_pct: {status.get('stop_loss_pct', 'n/a')}",
        f"stop_loss_buffer_pct: {status.get('stop_loss_buffer_pct', 'n/a')}",
        f"min_volume_ratio: {status.get('min_volume_ratio', 'n/a')}",
        f"max_order_value: {status.get('max_order_value', 'n/a')}",
        f"wait_fill_seconds: {status.get('wait_fill_seconds', 'n/a')}",
        f"fill_poll_interval: {status.get('fill_poll_interval', 'n/a')}",
        f"Configured python: {status.get('python_executable')}",
        f"Stdout log: {status.get('stdout_log')} ({status.get('stdout_log_bytes', 0)} bytes)",
        f"Stderr log: {status.get('stderr_log')} ({status.get('stderr_log_bytes', 0)} bytes)",
    ]
    for hint in status.get("env_hints") or []:
        lines.append("")
        lines.append(f"[hint] {hint}")
    if ctl.get("loaded") and not ctl.get("running"):
        lines.append("")
        lines.append("[hint] running=False — check stderr log: ~/.deepsignal/logs/crypto_auto_runner.error.log")
    stderr_tail = str(status.get("stderr_tail") or "")
    if stderr_tail.strip():
        lines.append("")
        lines.append("--- stderr tail ---")
        for ln in stderr_tail.strip().splitlines()[-8:]:
            lines.append(ln)
    return "\n".join(lines)
