from __future__ import annotations

import os
from pathlib import Path

import pytest

from deepsignal.crypto_trading.crypto_env import (
    crypto_launchd_env_hints,
    diagnose_crypto_env,
    emit_crypto_runner_startup_log,
    load_crypto_dotenv,
    plist_contains_secrets,
    startup_log_is_redacted,
)
from deepsignal.crypto_trading.crypto_launchd_installer import build_plist_dict, CryptoLaunchdRunnerConfig
from deepsignal.live_trading.launchd_installer import require_venv_python


def test_load_crypto_dotenv_project_priority(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    (home / ".deepsignal").mkdir()
    (home / ".deepsignal" / ".env").write_text("UPBIT_ACCESS_KEY=from-home\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "UPBIT_ACCESS_KEY=from-project\nUPBIT_SECRET_KEY=sec\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSIGNAL_PROJECT_ROOT", str(tmp_path))
    result = load_crypto_dotenv()
    assert result.env_loaded is True
    assert os.environ.get("UPBIT_ACCESS_KEY") == "from-project"


def test_os_environ_wins_over_dotenv(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("UPBIT_ACCESS_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSIGNAL_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("UPBIT_ACCESS_KEY", "from-shell")
    load_crypto_dotenv()
    assert os.environ.get("UPBIT_ACCESS_KEY") == "from-shell"


def test_diagnose_crypto_env_missing_warnings(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("DEEPSIGNAL_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UPBIT_SECRET_KEY", raising=False)
    issues = diagnose_crypto_env(project_dir=tmp_path)
    codes = {i["code"] for i in issues}
    assert "env_file_missing" in codes
    assert "upbit_access_key_missing" in codes


def test_startup_log_redaction(monkeypatch, tmp_path: Path, capsys) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "UPBIT_ACCESS_KEY=abcdefghijklmnopqrstuvwxyz\n"
        "UPBIT_SECRET_KEY=secretvalue123456789\n"
        "DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN=123456:ABCdef\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSIGNAL_PROJECT_ROOT", str(tmp_path))
    from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env

    env = ensure_crypto_runtime_env()
    emit_crypto_runner_startup_log(execute=False, env_result=env)
    out = capsys.readouterr().out
    assert "[crypto runner startup]" in out
    assert "abcdefghijklmnopqrstuvwxyz" not in out
    assert "secretvalue123456789" not in out
    assert "upbit access key: set" in out
    assert startup_log_is_redacted(out)


def test_plist_no_secrets(project: Path) -> None:
    py = require_venv_python(project)
    data = build_plist_dict(
        launch_root=project,
        python_executable=py,
        cfg=CryptoLaunchdRunnerConfig(),
    )
    assert plist_contains_secrets(data) == []
    env = data.get("EnvironmentVariables") or {}
    assert "UPBIT_ACCESS_KEY" not in env
    assert "UPBIT_SECRET_KEY" not in env


def test_launchd_env_hint_on_upbit_error() -> None:
    stderr = "UpbitConfigError: UPBIT_ACCESS_KEY and UPBIT_SECRET_KEY are required"
    hints = crypto_launchd_env_hints(stderr_tail=stderr, stdout_tail="")
    assert any("env loader" in h for h in hints)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("x\n", encoding="utf-8")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    return tmp_path
