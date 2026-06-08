"""Post-login DeepSignal LaunchAgent health check (macOS)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from deepsignal.live_trading.ops.launchd_installer import (
    _gui_domain,
    _run_launchctl,
    launch_root_symlink_path,
    parse_launchctl_print,
    project_root,
    require_venv_python,
)

HEALTH_LABEL = "com.deepsignal.launchd_health_check"

REQUIRED_SERVICES: tuple[tuple[str, str], ...] = (
    ("com.deepsignal.crypto_auto_runner", "코인 러너"),
    ("com.deepsignal.auto_runner", "국내주식(KIS)"),
    ("com.deepsignal.binance_stream", "Binance 스트림"),
)

STATE_PATH = Path.home() / ".deepsignal" / "launchd_health_last.json"


def _truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def health_delay_seconds() -> float:
    raw = (os.environ.get("LAUNCHD_HEALTH_DELAY_SECONDS") or "90").strip()
    try:
        return max(0.0, min(float(raw), 600.0))
    except ValueError:
        return 90.0


def health_kickstart_enabled(*, cli_flag: bool | None = None) -> bool:
    if cli_flag is not None:
        return bool(cli_flag)
    return _truthy(os.environ.get("LAUNCHD_HEALTH_KICKSTART"), default=True)


def health_notify_on_ok(*, cli_flag: bool | None = None) -> bool:
    if cli_flag is not None:
        return bool(cli_flag)
    return _truthy(os.environ.get("LAUNCHD_HEALTH_NOTIFY_ON_OK"), default=False)


def health_send_telegram_enabled(*, cli_flag: bool | None = None) -> bool:
    if cli_flag is not None:
        return bool(cli_flag)
    return _truthy(os.environ.get("LAUNCHD_HEALTH_SEND_TELEGRAM"), default=True)


def health_check_telegram_bot_enabled(*, cli_flag: bool | None = None) -> bool:
    if cli_flag is not None:
        return bool(cli_flag)
    return _truthy(os.environ.get("LAUNCHD_HEALTH_CHECK_TELEGRAM"), default=True)


def health_telegram_boot_ok_enabled(*, cli_flag: bool | None = None, from_launchd: bool = False) -> bool:
    if cli_flag is not None:
        return bool(cli_flag)
    if from_launchd:
        return _truthy(os.environ.get("LAUNCHD_HEALTH_TELEGRAM_BOOT_OK"), default=True)
    return _truthy(os.environ.get("LAUNCHD_HEALTH_TELEGRAM_BOOT_OK"), default=False)


def health_send_menu_on_boot(*, cli_flag: bool | None = None) -> bool:
    if cli_flag is not None:
        return bool(cli_flag)
    return _truthy(os.environ.get("LAUNCHD_HEALTH_SEND_MENU"), default=True)


@dataclass
class TelegramBotStatus:
    configured: bool = False
    get_me_ok: bool = False
    bot_username: str | None = None
    send_ok: bool = False
    send_error: str | None = None
    menu_sent: bool = False
    menu_error: str | None = None
    crypto_runner_running: bool = False

    @property
    def ok(self) -> bool:
        if not self.configured:
            return True
        return self.get_me_ok and self.send_ok

    def status_icon(self) -> str:
        return "✅" if self.ok else "❌"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ServiceStatus:
    label: str
    display_name: str
    loaded: bool = False
    running: bool = False
    state: str | None = None
    pid: int | None = None
    last_exit_code: int | None = None
    kickstarted: bool = False
    error: str | None = None

    def status_icon(self) -> str:
        return "✅" if self.running else "❌"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LaunchdHealthCheckResult:
    checked_at: str
    launch_root: str
    project_root: str
    delay_seconds: float
    infrastructure_ok: bool
    infrastructure_issues: list[str] = field(default_factory=list)
    services: list[ServiceStatus] = field(default_factory=list)
    kickstart_attempted: bool = False
    all_running: bool = False
    telegram_bot: TelegramBotStatus | None = None
    telegram_sent: bool = False
    telegram_result: dict[str, Any] = field(default_factory=dict)

    @property
    def system_ok(self) -> bool:
        tg = self.telegram_bot
        tg_ok = tg.ok if tg is not None and tg.configured else True
        return self.all_running and self.infrastructure_ok and tg_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "launch_root": self.launch_root,
            "project_root": self.project_root,
            "delay_seconds": self.delay_seconds,
            "infrastructure_ok": self.infrastructure_ok,
            "infrastructure_issues": self.infrastructure_issues,
            "services": [s.to_dict() for s in self.services],
            "kickstart_attempted": self.kickstart_attempted,
            "all_running": self.all_running,
            "telegram_bot": self.telegram_bot.to_dict() if self.telegram_bot else None,
            "system_ok": self.system_ok,
            "telegram_sent": self.telegram_sent,
            "telegram_result": self.telegram_result,
        }


def launchctl_service_status(label: str, *, display_name: str) -> ServiceStatus:
    svc = ServiceStatus(label=label, display_name=display_name)
    proc = _run_launchctl(["print", f"{_gui_domain()}/{label}"])
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        svc.error = (text.strip() or f"launchctl print failed ({proc.returncode})")[:300]
        return svc
    parsed = parse_launchctl_print(text)
    svc.loaded = bool(parsed.get("loaded"))
    svc.running = bool(parsed.get("running"))
    svc.state = parsed.get("state")
    svc.pid = parsed.get("pid")
    svc.last_exit_code = parsed.get("last_exit_code")
    return svc


def kickstart_service(label: str) -> tuple[bool, str]:
    proc = _run_launchctl(["kickstart", "-k", f"{_gui_domain()}/{label}"])
    msg = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, msg[:300]


def check_infrastructure(*, launch_root: Path, project_actual: Path) -> list[str]:
    issues: list[str] = []
    if not launch_root.is_dir():
        issues.append(f"launch_root 없음: {launch_root}")
    py = launch_root / ".venv" / "bin" / "python"
    if not py.is_file():
        issues.append(f"venv python 없음: {py}")
    if not (launch_root / "main.py").is_file():
        issues.append("main.py 없음 (launch_root)")
    if not project_actual.exists():
        issues.append(f"프로젝트 경로 접근 불가: {project_actual}")
    home_env = Path.home() / ".deepsignal" / ".env"
    project_env = project_actual / ".env"
    if not home_env.is_file() and not project_env.is_file():
        issues.append(".env 없음 (~/.deepsignal/.env 또는 프로젝트 .env)")
    link = launch_root_symlink_path()
    if link.is_symlink() and not link.exists():
        issues.append(f"심볼릭 링크 깨짐: {link}")
    return issues


def check_telegram_bot(
    *,
    project_actual: Path,
    services: list[ServiceStatus],
    send_main_menu: bool = True,
) -> TelegramBotStatus:
    """Verify Bot API (getMe); menu keyboard sent after summary message if enabled."""
    from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env
    from deepsignal.crypto_trading.crypto_telegram_flow import load_crypto_telegram_config_from_env
    from deepsignal.live_trading.telegram_approval import telegram_api_post

    status = TelegramBotStatus()
    crypto_svc = next((s for s in services if s.label == "com.deepsignal.crypto_auto_runner"), None)
    status.crypto_runner_running = bool(crypto_svc and crypto_svc.running)

    ensure_crypto_runtime_env(project_dir=project_actual)
    out_dir = project_actual / "outputs"
    tg = load_crypto_telegram_config_from_env(output_dir=str(out_dir))
    if not tg.bot_token or not tg.allowed_chat_id:
        status.send_error = "Telegram token/chat_id 미설정"
        return status

    status.configured = True
    me = telegram_api_post("getMe", {}, bot_token=tg.bot_token, timeout_seconds=float(tg.timeout_seconds))
    status.get_me_ok = bool(me.get("ok"))
    if not status.get_me_ok:
        status.send_error = str(me.get("description") or me.get("status") or "getMe failed")[:200]
        return status

    return status


def send_telegram_boot_menu(*, project_actual: Path) -> tuple[bool, str | None]:
    """Send main menu keyboard (proves sendMessage + reply_markup path)."""
    from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env
    from deepsignal.crypto_trading.crypto_telegram_flow import load_crypto_telegram_config_from_env

    if not health_send_menu_on_boot():
        return False, None
    try:
        ensure_crypto_runtime_env(project_dir=project_actual)
        tg = load_crypto_telegram_config_from_env(output_dir=str(project_actual / "outputs"))
        from deepsignal.crypto_trading.crypto_telegram_menu import telegram_send_menu_message

        menu_res = telegram_send_menu_message(tg)
        if menu_res.get("ok"):
            return True, None
        return False, str(menu_res.get("description") or "menu send failed")[:200]
    except Exception as exc:
        return False, str(exc)[:200]


def format_health_telegram(result: LaunchdHealthCheckResult) -> str:
    lines = ["[DeepSignal — 재기동 점검]"]
    if result.infrastructure_issues:
        lines.append("")
        lines.append("인프라:")
        for issue in result.infrastructure_issues[:5]:
            lines.append(f"· {issue}")
    lines.append("")
    for svc in result.services:
        state = svc.state or ("없음" if not svc.loaded else "stopped")
        extra = ""
        if svc.kickstarted:
            extra = " (kickstart 시도함)"
        if svc.last_exit_code not in (None, 0) and not svc.running:
            extra += f" exit={svc.last_exit_code}"
        lines.append(f"{svc.status_icon()} {svc.display_name}: {state}{extra}")
    tg = result.telegram_bot
    if tg is not None and tg.configured:
        lines.append("")
        if tg.ok:
            menu_note = " · 메뉴 키보드 전송됨" if tg.menu_sent else ""
            runner_note = " · 코인 러너 폴링 중" if tg.crypto_runner_running else " · 코인 러너 미실행"
            lines.append(f"{tg.status_icon()} Telegram 봇: API 정상{runner_note}{menu_note}")
        else:
            lines.append(f"{tg.status_icon()} Telegram 봇: {tg.send_error or '점검 실패'}")
    lines.append("")
    if result.system_ok:
        lines.append("재부팅 점검 완료 — 메뉴 버튼으로 추천·자산을 조회할 수 있습니다.")
    elif result.all_running and result.infrastructure_ok:
        lines.append("프로세스는 정상이나 Telegram 점검에 문제가 있습니다.")
    else:
        lines.append("일부 프로세스가 없습니다. 1~2분 후 다시 확인하거나 로그를 봐 주세요.")
        lines.append("~/.deepsignal/logs/")
    return "\n".join(lines)[:4000]


def format_health_console(result: LaunchdHealthCheckResult) -> str:
    lines = [
        "DeepSignal launchd health check",
        f"Checked: {result.checked_at}",
        f"Launch root: {result.launch_root}",
        f"Project root: {result.project_root}",
        f"Waited: {result.delay_seconds:.0f}s",
        f"Infrastructure: {'OK' if result.infrastructure_ok else 'ISSUES'}",
    ]
    for issue in result.infrastructure_issues:
        lines.append(f"  - {issue}")
    for svc in result.services:
        lines.append(
            f"  {svc.display_name} ({svc.label}): "
            f"{'running' if svc.running else 'NOT RUNNING'} state={svc.state} pid={svc.pid}"
        )
    lines.append(f"All running: {result.all_running}")
    if result.telegram_bot is not None:
        lines.append(f"Telegram bot API: {'OK' if result.telegram_bot.ok else 'FAIL'}")
        if result.telegram_bot.menu_sent:
            lines.append("Telegram menu keyboard: sent")
    lines.append(f"System OK: {result.system_ok}")
    if result.telegram_sent:
        lines.append(f"Telegram notify: {result.telegram_result.get('ok', result.telegram_result.get('status'))}")
    return "\n".join(lines)


def save_health_result(result: LaunchdHealthCheckResult) -> Path:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return STATE_PATH


def run_launchd_health_check(
    *,
    project_dir: str | Path | None = None,
    wait_seconds: float | None = None,
    kickstart_missing: bool | None = None,
    send_telegram: bool | None = None,
    notify_on_ok: bool | None = None,
    check_telegram: bool | None = None,
    from_launchd: bool = False,
) -> LaunchdHealthCheckResult:
    from deepsignal.live_trading.time_utils import now_kst_iso

    actual = project_root(project_dir)
    link = launch_root_symlink_path()
    launch_root = link if link.is_dir() else actual
    try:
        require_venv_python(launch_root)
    except (FileNotFoundError, ValueError):
        pass

    delay = float(wait_seconds if wait_seconds is not None else health_delay_seconds())
    if delay > 0:
        time.sleep(delay)

    infra_issues = check_infrastructure(launch_root=launch_root, project_actual=actual)
    services = [launchctl_service_status(label, display_name=name) for label, name in REQUIRED_SERVICES]

    do_kickstart = health_kickstart_enabled(cli_flag=kickstart_missing) and not infra_issues
    kickstart_attempted = False
    if do_kickstart:
        for svc in services:
            if not svc.running and svc.loaded:
                kickstart_attempted = True
                ok, _ = kickstart_service(svc.label)
                svc.kickstarted = ok
        if kickstart_attempted:
            time.sleep(3.0)
            services = [launchctl_service_status(label, display_name=name) for label, name in REQUIRED_SERVICES]

    all_running = all(s.running for s in services) and not infra_issues

    telegram_bot: TelegramBotStatus | None = None
    if health_check_telegram_bot_enabled(cli_flag=check_telegram):
        telegram_bot = check_telegram_bot(project_actual=actual, services=services)

    result = LaunchdHealthCheckResult(
        checked_at=now_kst_iso(),
        launch_root=launch_root.as_posix(),
        project_root=actual.as_posix(),
        delay_seconds=delay,
        infrastructure_ok=not infra_issues,
        infrastructure_issues=infra_issues,
        services=services,
        kickstart_attempted=kickstart_attempted,
        all_running=all_running,
        telegram_bot=telegram_bot,
    )

    should_send = health_send_telegram_enabled(cli_flag=send_telegram)
    boot_ok = health_telegram_boot_ok_enabled(cli_flag=notify_on_ok, from_launchd=from_launchd)
    need_telegram = should_send and ((not result.system_ok) or boot_ok)
    if need_telegram:
        from deepsignal.crypto_trading.crypto_env import ensure_crypto_runtime_env
        from deepsignal.crypto_trading.crypto_telegram_flow import load_crypto_telegram_config_from_env
        from deepsignal.live_trading.telegram_auto_execute import send_runner_telegram

        ensure_crypto_runtime_env(project_dir=actual)
        tg = load_crypto_telegram_config_from_env(output_dir=str(actual / "outputs"))
        if tg.bot_token and tg.allowed_chat_id:
            result.telegram_sent = True
            result.telegram_result = send_runner_telegram(
                text=format_health_telegram(result),
                config=tg,
            )
            if result.telegram_bot is not None:
                if result.telegram_result.get("ok"):
                    result.telegram_bot.send_ok = True
                else:
                    result.telegram_bot.send_ok = False
                    result.telegram_bot.send_error = str(
                        result.telegram_result.get("description")
                        or result.telegram_result.get("status")
                        or "sendMessage failed"
                    )[:200]
                if result.telegram_bot.crypto_runner_running:
                    menu_ok, menu_err = send_telegram_boot_menu(project_actual=actual)
                    result.telegram_bot.menu_sent = menu_ok
                    result.telegram_bot.menu_error = menu_err
        else:
            result.telegram_result = {"ok": False, "status": "telegram_not_configured"}

    save_health_result(result)
    return result
