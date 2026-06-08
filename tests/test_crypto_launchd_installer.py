from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deepsignal.crypto_trading.crypto_env import format_crypto_env_warnings, plist_contains_secrets
from deepsignal.crypto_trading.crypto_launchd_installer import (
    CRYPTO_LAUNCHD_LABEL,
    CryptoLaunchdRunnerConfig,
    build_plist_dict,
    build_runner_argv,
    crypto_launchd_status,
    crypto_log_paths,
    install_crypto_launchd,
    launchd_config_from_namespace,
    plist_path,
    uninstall_crypto_launchd,
    write_plist,
)
from deepsignal.live_trading.launchd_installer import (
    require_venv_python,
    resolve_launch_root,
    validate_plist_install,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "outputs").mkdir()
    return tmp_path


def test_crypto_log_paths_under_home(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    out, err = crypto_log_paths()
    assert out == tmp_path / ".deepsignal" / "logs" / "crypto_auto_runner.log"
    assert err == tmp_path / ".deepsignal" / "logs" / "crypto_auto_runner.error.log"
    assert "#" not in out.as_posix()


def test_build_runner_argv(project: Path) -> None:
    cfg = CryptoLaunchdRunnerConfig(
        poll=True,
        execute=True,
        wait_fill_seconds=60.0,
        fill_poll_interval=3.0,
        take_profit_buffer_pct=0.05,
        stop_loss_buffer_pct=0.05,
        min_volume_ratio=0.7,
    )
    argv = build_runner_argv(project, cfg)
    assert "crypto-auto-runner" in argv
    assert "--broker" in argv and "upbit" in argv
    assert "--interval-minutes" in argv
    assert "--poll" in argv
    assert "--execute" in argv
    assert "--wait-fill-seconds" in argv
    assert "60.0" in argv
    assert "--fill-poll-interval" in argv
    assert "--take-profit-buffer-pct" in argv
    assert "0.05" in argv
    assert "--stop-loss-buffer-pct" in argv
    assert "--min-volume-ratio" in argv
    assert "0.7" in argv


def test_build_runner_argv_strategy_options_in_plist(project: Path, monkeypatch, tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.launch_agents_dir",
        lambda: agents,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.plist_path",
        lambda: agents / f"{CRYPTO_LAUNCHD_LABEL}.plist",
    )
    cfg = CryptoLaunchdRunnerConfig(
        take_profit_pct=2.0,
        take_profit_buffer_pct=0.05,
        stop_loss_pct=-1.5,
        stop_loss_buffer_pct=0.05,
        min_volume_ratio=0.7,
        poll=True,
        execute=True,
    )
    path = write_plist(
        project,
        cfg,
        launch_root=project,
        sanitize_meta={"sanitized": False},
    )
    with path.open("rb") as fh:
        data = plistlib.load(fh)
    argv = data["ProgramArguments"]
    assert "--take-profit-buffer-pct" in argv
    assert "--stop-loss-buffer-pct" in argv
    assert "--min-volume-ratio" in argv
    assert argv[argv.index("--min-volume-ratio") + 1] == "0.7"
    assert "--interval-minutes" in argv
    assert "--poll" in argv
    assert "--execute" in argv
    assert "--wait-fill-seconds" in argv
    assert "60.0" in argv
    assert "--fill-poll-interval" in argv


def test_plist_keepalive_runatload_and_logs(project: Path, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    py = require_venv_python(project)
    cfg = CryptoLaunchdRunnerConfig(poll=True, execute=True)
    data = build_plist_dict(launch_root=project, python_executable=py, cfg=cfg)
    assert data["Label"] == CRYPTO_LAUNCHD_LABEL
    assert data["KeepAlive"] is True
    assert data["RunAtLoad"] is True
    assert "crypto_auto_runner.log" in data["StandardOutPath"]
    assert "#" not in data["StandardOutPath"]
    assert "#" not in data["WorkingDirectory"]


def test_resolve_launch_root_sanitize_hash(project: Path, monkeypatch, tmp_path: Path) -> None:
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


def test_write_plist_validation(project: Path, monkeypatch, tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.launch_agents_dir",
        lambda: agents,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.plist_path",
        lambda: agents / f"{CRYPTO_LAUNCHD_LABEL}.plist",
    )
    cfg = CryptoLaunchdRunnerConfig()
    path = write_plist(
        project,
        cfg,
        launch_root=project,
        sanitize_meta={"sanitized": False},
    )
    py = require_venv_python(project)
    errors = validate_plist_install(path, launch_root=project, python_executable=py)
    assert errors == []
    with path.open("rb") as fh:
        data = plistlib.load(fh)
    assert "crypto-auto-runner" in data["ProgramArguments"]


def test_install_without_load(project: Path, monkeypatch, tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.launch_agents_dir",
        lambda: agents,
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.plist_path",
        lambda: agents / f"{CRYPTO_LAUNCHD_LABEL}.plist",
    )
    result = install_crypto_launchd(
        CryptoLaunchdRunnerConfig(poll=True, execute=True),
        project_dir=project,
        load_now=False,
        sanitize_path=False,
    )
    assert result["label"] == CRYPTO_LAUNCHD_LABEL
    assert result["plist_path"]
    stdout, _ = crypto_log_paths()
    assert stdout.is_file()
    assert (project / "logs" / "crypto_launchd_runner_config.json").is_file()


def test_uninstall_crypto_launchd(monkeypatch, tmp_path: Path) -> None:
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    plist = agents / f"{CRYPTO_LAUNCHD_LABEL}.plist"
    plist.write_text("plist", encoding="utf-8")
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.plist_path",
        lambda: plist,
    )

    def fake_unload(path):
        return True, "ok"

    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.unload_plist",
        fake_unload,
    )
    out = uninstall_crypto_launchd()
    assert out["unloaded"] is True
    assert not plist.is_file()


def test_crypto_launchd_status_not_installed(project: Path, monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "no.plist"
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.plist_path",
        lambda: missing,
    )
    status = crypto_launchd_status(project_dir=project)
    assert status["label"] == CRYPTO_LAUNCHD_LABEL
    assert status["plist_installed"] is False


def test_launchd_config_from_namespace() -> None:
    ns = MagicMock(
        broker="upbit",
        interval_minutes=30,
        max_order_value=10000,
        take_profit_pct=2.0,
        take_profit_buffer_pct=0.05,
        stop_loss_pct=-1.5,
        stop_loss_buffer_pct=0.05,
        min_volume_ratio=0.7,
        max_orders_per_day=3,
        poll=True,
        execute=True,
        output_dir="outputs",
        wait_fill_seconds=60,
        fill_poll_interval=3,
    )
    cfg = launchd_config_from_namespace(ns)
    assert cfg.execute is True
    assert cfg.wait_fill_seconds == 60.0
    assert cfg.take_profit_buffer_pct == 0.05
    assert cfg.stop_loss_buffer_pct == 0.05
    assert cfg.min_volume_ratio == 0.7


def test_crypto_launchd_status_shows_strategy_options(
    project: Path, monkeypatch, tmp_path: Path
) -> None:
    import json

    from deepsignal.crypto_trading.crypto_launchd_installer import (
        CONFIG_RECORD_NAME,
        format_status_console,
    )

    record = {
        "runner_config": {
            "broker": "upbit",
            "interval_minutes": 30.0,
            "max_order_value": 10000.0,
            "take_profit_pct": 2.0,
            "take_profit_buffer_pct": 0.05,
            "stop_loss_pct": -1.5,
            "stop_loss_buffer_pct": 0.05,
            "min_volume_ratio": 0.7,
            "poll": True,
            "execute": True,
            "wait_fill_seconds": 60.0,
            "fill_poll_interval": 3.0,
        }
    }
    (project / "logs").mkdir(exist_ok=True)
    (project / "logs" / CONFIG_RECORD_NAME).write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.plist_path",
        lambda: tmp_path / "missing.plist",
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.crypto_log_paths",
        lambda: (tmp_path / "out.log", tmp_path / "err.log"),
    )
    status = crypto_launchd_status(project_dir=project)
    assert status["take_profit_buffer_pct"] == 0.05
    assert status["stop_loss_buffer_pct"] == 0.05
    assert status["min_volume_ratio"] == 0.7
    assert status["fill_poll_interval"] == 3.0
    text = format_status_console(status)
    assert "take_profit_buffer_pct: 0.05" in text
    assert "stop_loss_buffer_pct: 0.05" in text
    assert "min_volume_ratio: 0.7" in text


def test_plist_path_name() -> None:
    assert plist_path().name == f"{CRYPTO_LAUNCHD_LABEL}.plist"


def test_format_crypto_env_warnings() -> None:
    text = format_crypto_env_warnings(
        [{"severity": "warning", "code": "upbit_access_key_missing", "message": "UPBIT_ACCESS_KEY is not set"}]
    )
    assert "[crypto launchd warning]" in text
    assert "UPBIT_ACCESS_KEY" in text


def test_crypto_launchd_status_env_hint(project: Path, monkeypatch, tmp_path: Path) -> None:
    err_log = tmp_path / "crypto_auto_runner.error.log"
    err_log.write_text(
        "UpbitConfigError: UPBIT_ACCESS_KEY and UPBIT_SECRET_KEY are required\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.crypto_log_paths",
        lambda: (tmp_path / "out.log", err_log),
    )
    monkeypatch.setattr(
        "deepsignal.crypto_trading.crypto_launchd_installer.plist_path",
        lambda: tmp_path / "missing.plist",
    )
    from deepsignal.crypto_trading.crypto_launchd_installer import format_status_console

    status = crypto_launchd_status(project_dir=project)
    text = format_status_console(status)
    assert "env loader" in text or status.get("env_hints")
