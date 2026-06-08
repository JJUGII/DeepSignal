"""Load project .env for crypto CLIs and launchd (no secrets in plist)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from deepsignal.live_trading.launchd_installer import project_root

HOME_ENV_FILE = Path.home() / ".deepsignal" / ".env"


@dataclass
class CryptoEnvLoadResult:
    project_root: Path
    project_env_path: Path | None = None
    home_env_path: Path | None = None
    project_env_loaded: bool = False
    home_env_loaded: bool = False
    keys_applied: list[str] = field(default_factory=list)

    @property
    def env_loaded(self) -> bool:
        return self.project_env_loaded or self.home_env_loaded


def resolve_crypto_project_root(start: str | Path | None = None) -> Path:
    return project_root(start)


def _merge_dotenv_into_environ(*paths: Path) -> list[str]:
    """Apply dotenv files: later paths override earlier; existing os.environ wins."""
    merged: dict[str, str | None] = {}
    for path in paths:
        if path.is_file():
            merged.update(dotenv_values(path))
    applied: list[str] = []
    for key, value in merged.items():
        if value is None or key in os.environ:
            continue
        os.environ[key] = str(value)
        applied.append(key)
    return applied


def load_crypto_dotenv(*, project_dir: str | Path | None = None) -> CryptoEnvLoadResult:
    root = resolve_crypto_project_root(project_dir)
    project_env = root / ".env"
    home_env = HOME_ENV_FILE
    paths: list[Path] = []
    if home_env.is_file():
        paths.append(home_env)
    if project_env.is_file():
        paths.append(project_env)
    applied = _merge_dotenv_into_environ(*paths)
    return CryptoEnvLoadResult(
        project_root=root,
        project_env_path=project_env if project_env.is_file() else None,
        home_env_path=home_env if home_env.is_file() else None,
        project_env_loaded=project_env.is_file(),
        home_env_loaded=home_env.is_file(),
        keys_applied=applied,
    )


def ensure_crypto_runtime_env(*, project_dir: str | Path | None = None) -> CryptoEnvLoadResult:
    return load_crypto_dotenv(project_dir=project_dir)


def _env_set(key: str) -> str:
    return "set" if (os.environ.get(key) or "").strip() else "missing"


def crypto_env_presence() -> dict[str, str]:
    dry = (os.environ.get("UPBIT_DRY_RUN") or "true").strip().lower()
    dry_run = dry not in ("0", "false", "no", "off")
    from deepsignal.crypto_trading.crypto_auto_execute_policy import load_crypto_auto_execute_config_from_env

    crypto_auto = load_crypto_auto_execute_config_from_env()
    from deepsignal.crypto_trading.crypto_paper_mode import crypto_paper_mode_enabled

    paper = crypto_paper_mode_enabled()
    if paper:
        dry_run = True
    return {
        "upbit_access_key": _env_set("UPBIT_ACCESS_KEY"),
        "upbit_secret_key": _env_set("UPBIT_SECRET_KEY"),
        "telegram_token": _env_set("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": _env_set("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID"),
        "dry_run": "true" if dry_run else "false",
        "crypto_paper_mode": "true" if paper else "false",
        "crypto_auto_execute": "true" if crypto_auto.enabled else "false",
    }


def diagnose_crypto_env(*, project_dir: str | Path | None = None) -> list[dict[str, str]]:
    root = resolve_crypto_project_root(project_dir)
    project_env = root / ".env"
    issues: list[dict[str, str]] = []

    def warn(code: str, message: str) -> None:
        issues.append({"severity": "warning", "code": code, "message": message})

    if not project_env.is_file() and not HOME_ENV_FILE.is_file():
        warn("env_file_missing", f"No .env at {project_env} or {HOME_ENV_FILE}")

    load_crypto_dotenv(project_dir=root)
    if not (os.environ.get("UPBIT_ACCESS_KEY") or "").strip():
        warn("upbit_access_key_missing", "UPBIT_ACCESS_KEY is not set after loading .env")
    if not (os.environ.get("UPBIT_SECRET_KEY") or "").strip():
        warn("upbit_secret_key_missing", "UPBIT_SECRET_KEY is not set after loading .env")
    if not (os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN") or "").strip():
        warn("telegram_token_missing", "DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN is not set")
    if not (os.environ.get("DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID") or "").strip():
        warn("telegram_chat_id_missing", "DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID is not set")
    return issues


def format_crypto_env_warnings(issues: list[dict[str, str]]) -> str:
    warnings = [i for i in issues if i.get("severity") == "warning"]
    if not warnings:
        return ""
    lines = ["[crypto launchd warning] install-crypto-launchd — 환경 변수 점검:"]
    for item in warnings:
        lines.append(f"  - {item.get('message', item.get('code', 'warning'))}")
    return "\n".join(lines)


def emit_crypto_runner_startup_log(*, execute: bool, env_result: CryptoEnvLoadResult | None = None) -> None:
    if env_result is None:
        env_result = ensure_crypto_runtime_env()
    presence = crypto_env_presence()
    if execute:
        dry_run = "false"
    else:
        dry_run = presence["dry_run"]
    lines = [
        "[crypto runner startup]",
        f"env loaded: {'true' if env_result.env_loaded else 'false'}",
        f"project root: {env_result.project_root}",
        f"project .env: {env_result.project_env_path or 'missing'}",
        f"home .env: {env_result.home_env_path or 'optional-missing'}",
        f"upbit access key: {presence['upbit_access_key']}",
        f"upbit secret key: {presence['upbit_secret_key']}",
        f"telegram token: {presence['telegram_token']}",
        f"telegram chat id: {presence['telegram_chat_id']}",
        f"dry_run: {dry_run}",
        f"crypto_paper_mode: {presence.get('crypto_paper_mode', 'false')}",
        f"crypto_auto_execute: {presence.get('crypto_auto_execute', 'false')}",
    ]
    text = "\n".join(lines)
    print(text, flush=True)


def startup_log_is_redacted(text: str) -> bool:
    """True if no line value looks like a raw API key/token."""
    allowed = {"set", "missing", "true", "false", "optional-missing"}
    for line in text.splitlines():
        if ":" not in line:
            continue
        _, _, value = line.partition(":")
        val = value.strip()
        if not val or val in allowed or val.startswith("/") or "missing" in val:
            continue
        if len(val) > 24 and " " not in val:
            return False
    return True


def read_log_tail(path: Path, *, max_lines: int = 80, max_bytes: int = 32_768) -> str:
    if not path.is_file():
        return ""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if len(raw) > max_bytes:
        raw = raw[-max_bytes:]
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def log_indicates_upbit_config_error(text: str) -> bool:
    return "UpbitConfigError" in text and "UPBIT_ACCESS_KEY" in text


def crypto_launchd_env_hints(*, stderr_tail: str, stdout_tail: str) -> list[str]:
    hints: list[str] = []
    combined = f"{stderr_tail}\n{stdout_tail}"
    if log_indicates_upbit_config_error(combined):
        hints.append(
            "launchd 환경에서 .env가 로드되지 않았습니다. crypto runner env loader를 확인하세요."
        )
    if "[crypto runner startup]" in stdout_tail:
        for line in stdout_tail.splitlines():
            if line.strip().startswith("env loaded:") and "false" in line:
                hints.append("stdout에 env loaded: false — 프로젝트 루트 .env 경로를 확인하세요.")
                break
    return hints


def plist_contains_secrets(plist_data: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    forbidden_keys = {
        "UPBIT_ACCESS_KEY",
        "UPBIT_SECRET_KEY",
        "DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_TOKEN",
    }

    def scan(value: Any, where: str) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if str(k).upper() in forbidden_keys:
                    violations.append(f"{where}: key {k}")
                scan(v, f"{where}.{k}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                scan(item, f"{where}[{i}]")
        elif isinstance(value, str):
            upper = value.upper()
            for key in forbidden_keys:
                if key in upper and len(value) > 16:
                    violations.append(f"{where}: possible secret in string")

    scan(plist_data, "plist")
    env_vars = plist_data.get("EnvironmentVariables") or {}
    for key in forbidden_keys:
        if key in env_vars or key.lower() in {str(k).lower() for k in env_vars}:
            violations.append(f"EnvironmentVariables contains {key}")
    return violations
