from __future__ import annotations

import os
import plistlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deepsignal.live_trading.launchd_installer import (
    build_plist_dict,
    install_launchd,
    is_homebrew_python_path,
    require_venv_python,
    resolve_python_executable,
    run_launchd_runner_test,
    validate_plist_install,
    venv_python_plist_path,
    write_plist,
)
from deepsignal.live_trading.daily_ai_auto_runner import emit_runner_startup_check


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(py, 0o755)
    return tmp_path


def test_venv_python_plist_path_no_resolve(project: Path) -> None:
    path = venv_python_plist_path(project)
    assert path.endswith("/.venv/bin/python")
    assert "Cellar" not in path
    assert path == os.path.join(os.path.normpath(str(project)), ".venv", "bin", "python")


def test_require_venv_blocks_missing(project: Path) -> None:
    empty = project / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        require_venv_python(empty)


def test_resolve_python_no_homebrew_fallback(project: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_installer.sys.executable",
        "/opt/homebrew/Cellar/python@3.11/3.11.15/bin/python3.11",
    )
    (project / ".venv" / "bin" / "python").unlink()
    with pytest.raises(FileNotFoundError):
        resolve_python_executable(project)


def test_plist_argv_uses_venv_python(project: Path) -> None:
    py = require_venv_python(project)
    data = build_plist_dict(root=project, python_executable=py, cfg=__import__(
        "deepsignal.live_trading.launchd_installer", fromlist=["LaunchdRunnerConfig"]
    ).LaunchdRunnerConfig())
    assert data["ProgramArguments"][0] == py
    assert ".venv/bin/python" in data["ProgramArguments"][0]
    assert not is_homebrew_python_path(data["ProgramArguments"][0])


def test_validate_plist_rejects_homebrew_argv(project: Path, monkeypatch, tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr("deepsignal.live_trading.launchd_installer.launch_agents_dir", lambda: agents)
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_installer.plist_path",
        lambda: agents / "com.deepsignal.auto_runner.plist",
    )
    py = require_venv_python(project)
    write_plist(project, __import__(
        "deepsignal.live_trading.launchd_installer", fromlist=["LaunchdRunnerConfig"]
    ).LaunchdRunnerConfig(), launch_root=project, python_executable=py)
    bad = agents / "com.deepsignal.auto_runner.plist"
    data = plistlib.loads(bad.read_bytes())
    data["ProgramArguments"][0] = "/opt/homebrew/Cellar/python@3.11/3.11.15/bin/python3.11"
    bad.write_bytes(plistlib.dumps(data))
    errors = validate_plist_install(bad, launch_root=project, python_executable=py)
    codes = {e["code"] for e in errors}
    assert "homebrew_python" in codes


def test_runner_startup_check_ok(monkeypatch) -> None:
    import sys as _sys

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name in ("pandas", "numpy"):
            return MagicMock()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    monkeypatch.setattr(_sys, "executable", "/proj/.venv/bin/python")
    monkeypatch.setattr(_sys, "prefix", "/proj/.venv")
    monkeypatch.setattr(_sys, "base_prefix", "/usr")
    monkeypatch.setattr(_sys, "argv", ["/proj/.venv/bin/python", "main.py"])
    assert emit_runner_startup_check() is True


def test_install_blocks_without_venv(project: Path, monkeypatch, tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    (project / ".venv" / "bin" / "python").unlink()
    monkeypatch.setattr("deepsignal.live_trading.launchd_installer.launch_agents_dir", lambda: agents)
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_installer.plist_path",
        lambda: agents / "com.deepsignal.auto_runner.plist",
    )
    cfg = __import__(
        "deepsignal.live_trading.launchd_installer", fromlist=["LaunchdRunnerConfig"]
    ).LaunchdRunnerConfig()
    with pytest.raises(FileNotFoundError):
        install_launchd(cfg, project_dir=project, load_now=False, sanitize_path=False)


def test_runner_test_uses_venv_python(project: Path, monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = "ok\n"
        proc.stderr = ""
        return proc

    monkeypatch.setattr("deepsignal.live_trading.launchd_installer.subprocess.run", fake_run)
    cfg = __import__(
        "deepsignal.live_trading.launchd_installer", fromlist=["LaunchdRunnerConfig"]
    ).LaunchdRunnerConfig()
    out = run_launchd_runner_test(cfg, project_dir=project, timeout_seconds=5.0)
    assert ".venv/bin/python" in out["python_executable"]
    assert "Cellar" not in out["python_executable"]
