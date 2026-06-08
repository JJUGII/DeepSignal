from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deepsignal.live_trading.launchd_installer import (
    LAUNCHD_LABEL,
    LaunchdRunnerConfig,
    build_plist_dict,
    build_runner_argv,
    diagnose_project_path,
    ensure_runtime_directories,
    format_diagnostic_warnings,
    guess_running_false_causes,
    install_launchd,
    launchd_status,
    load_plist,
    log_paths,
    parse_launchctl_print,
    path_needs_launch_sanitize,
    require_venv_python,
    resolve_launch_root,
    run_launchd_runner_test,
    touch_log_files,
    uninstall_launchd,
    validate_plist_install,
    write_plist,
)

SAMPLE_LAUNCHCTL_PRINT = """
gui/501/com.deepsignal.auto_runner = {
\tactive count = 0
\tstate = spawn scheduled
\tpid = 0
\tprogram = /opt/homebrew/bin/python3.11
\tworking directory = /Users/me/DeepSignal/project_root
\tlast exit code = 78: EX_CONFIG
\truns = 9
}
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    return tmp_path


def test_path_hash_diagnosis(tmp_path: Path) -> None:
    root = tmp_path / "foo" / "#bar" / "baz"
    root.mkdir(parents=True)
    (root / "main.py").write_text("x", encoding="utf-8")
    issues = diagnose_project_path(root)
    codes = {i["code"] for i in issues}
    assert "hash_in_path" in codes
    text = format_diagnostic_warnings(issues)
    assert "[launchd warning]" in text
    assert "#" in text


def test_ensure_runtime_directories_and_touch(project: Path) -> None:
    cfg = LaunchdRunnerConfig(output_dir="outputs")
    ensure_runtime_directories(project, cfg)
    assert (project / "logs").is_dir()
    assert (project / "outputs").is_dir()
    stdout, stderr = log_paths(project)
    touch_log_files(stdout, stderr)
    assert stdout.is_file()
    assert stderr.is_file()


def test_home_log_paths_when_sanitized(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    out, err = log_paths(tmp_path / "proj", use_home_logs=True)
    assert out == tmp_path / ".deepsignal" / "logs" / "daily_ai_auto_runner.log"
    assert "#" not in out.as_posix()


def test_resolve_launch_root_symlink_for_hash(project: Path, monkeypatch, tmp_path: Path) -> None:
    hash_root = tmp_path / "vol" / "#Project" / "app"
    hash_root.mkdir(parents=True)
    (hash_root / "main.py").write_text("ok\n", encoding="utf-8")
    (hash_root / ".venv" / "bin").mkdir(parents=True)
    (hash_root / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")

    link_parent = tmp_path / ".deepsignal"
    link_parent.mkdir(parents=True)
    link = link_parent / "project_root"
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_installer.launch_root_symlink_path",
        lambda: link,
    )
    launch_root, meta = resolve_launch_root(hash_root, sanitize=True)
    assert meta["sanitized"] is True
    assert "#" not in launch_root.as_posix()
    assert launch_root.readlink() == hash_root.resolve() or launch_root.resolve() == hash_root.resolve()


def test_parse_launchctl_print_exit_78() -> None:
    parsed = parse_launchctl_print(SAMPLE_LAUNCHCTL_PRINT)
    assert parsed["loaded"] is True
    assert parsed["running"] is False
    assert parsed["state"] == "spawn"
    assert parsed["last_exit_code"] == 78
    assert parsed["runs"] == 9


def test_guess_running_false_causes_hash() -> None:
    status = {
        "project_root": "/Volumes/JJU/#Project/Mac/Deepsignal",
        "launch_root": "/Volumes/JJU/#Project/Mac/Deepsignal",
        "path_sanitized": False,
        "stdout_log": "/tmp/out.log",
        "stderr_log": "/tmp/err.log",
        "launchctl": {"last_exit_code": 78, "state": "spawn", "running": False},
    }
    causes = guess_running_false_causes(status)
    assert any("78" in c or "EX_CONFIG" in c for c in causes)
    assert any("#" in c for c in causes)


def test_load_plist_captures_bootstrap_stderr(monkeypatch, tmp_path: Path) -> None:
    plist = tmp_path / "test.plist"
    plist.write_text("<?xml version='1.0'?><plist/>", encoding="utf-8")

    def fake_run(args):
        proc = MagicMock()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "bootstrap error: service already loaded"
        if args[0] == "load":
            proc.returncode = 0
            proc.stderr = ""
        return proc

    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_installer._run_launchctl",
        fake_run,
    )
    result = load_plist(plist)
    assert result["bootstrap_stderr"] == "bootstrap error: service already loaded"
    assert result["fallback_used"] is True
    assert result["ok"] is True


def test_plist_contains_keepalive_and_logs(project: Path) -> None:
    cfg = LaunchdRunnerConfig()
    py = require_venv_python(project)
    data = build_plist_dict(root=project, python_executable=py, cfg=cfg)
    assert data["Label"] == LAUNCHD_LABEL
    assert data["KeepAlive"] is True


def test_write_plist_validation(project: Path, monkeypatch, tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr("deepsignal.live_trading.launchd_installer.launch_agents_dir", lambda: agents)
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_installer.plist_path",
        lambda: agents / "com.deepsignal.auto_runner.plist",
    )
    path = write_plist(project, LaunchdRunnerConfig())
    py = require_venv_python(project)
    errors = validate_plist_install(path, launch_root=project, python_executable=py)
    assert errors == []


def test_install_without_load(project: Path, monkeypatch, tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr("deepsignal.live_trading.launchd_installer.launch_agents_dir", lambda: agents)
    monkeypatch.setattr(
        "deepsignal.live_trading.launchd_installer.plist_path",
        lambda: agents / "com.deepsignal.auto_runner.plist",
    )
    result = install_launchd(LaunchdRunnerConfig(), project_dir=project, load_now=False, sanitize_path=False)
    assert result["plist_path"]
    assert (project / "logs" / "daily_ai_auto_runner.log").is_file()


def test_runner_test_subprocess(project: Path, monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = "DeepSignal daily-ai-auto-runner started\n"
        proc.stderr = ""
        return proc

    monkeypatch.setattr("deepsignal.live_trading.launchd_installer.subprocess.run", fake_run)
    out = run_launchd_runner_test(LaunchdRunnerConfig(), project_dir=project, timeout_seconds=5.0)
    assert out["import_ok"] is True
    assert out["returncode"] == 0
    assert "--max-iterations" in " ".join(out["argv"])


def test_path_needs_sanitize() -> None:
    assert path_needs_launch_sanitize(Path("/a/#b")) is True
    assert path_needs_launch_sanitize(Path("/a/b")) is False
