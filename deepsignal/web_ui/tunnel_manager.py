"""Cloudflare Tunnel 프로세스 관리."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path


_URL_RE = re.compile(r"https://[\w-]+\.trycloudflare\.com")
_tunnel_proc: subprocess.Popen | None = None
_tunnel_url: str | None = None


def is_cloudflared_installed() -> bool:
    try:
        subprocess.run(["cloudflared", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def start_quick_tunnel(port: int = 8765) -> tuple[subprocess.Popen | None, str | None]:
    """
    계정 불필요 Quick Tunnel 시작.
    반환: (process, url) — url은 최대 20초 대기 후 파싱
    """
    global _tunnel_proc, _tunnel_url

    if not is_cloudflared_installed():
        print("[tunnel] cloudflared not found. Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        return None, None

    cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _tunnel_proc = proc

    url: list[str] = []

    def _reader() -> None:
        global _tunnel_url
        for line in proc.stdout:  # type: ignore[union-attr]
            print(f"[cloudflared] {line.rstrip()}")
            m = _URL_RE.search(line)
            if m and not url:
                url.append(m.group())
                _tunnel_url = m.group()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    # URL 등장까지 최대 20초 대기
    deadline = time.time() + 20
    while time.time() < deadline and not url:
        time.sleep(0.3)

    return proc, url[0] if url else None


def start_named_tunnel(token: str) -> subprocess.Popen | None:
    """Cloudflare Zero Trust 네임드 터널 (토큰 방식)."""
    global _tunnel_proc
    if not is_cloudflared_installed():
        print("[tunnel] cloudflared not found.")
        return None
    cmd = ["cloudflared", "tunnel", "run", "--token", token]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    _tunnel_proc = proc

    def _reader() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            print(f"[cloudflared] {line.rstrip()}")
    threading.Thread(target=_reader, daemon=True).start()
    return proc


def get_tunnel_url() -> str | None:
    return _tunnel_url


def stop_tunnel() -> None:
    global _tunnel_proc
    if _tunnel_proc:
        _tunnel_proc.terminate()
        _tunnel_proc = None


def update_env_public_url(url: str, env_path: Path) -> None:
    """DEEPSIGNAL_WEBUI_PUBLIC_URL 을 .env 파일에 업서트."""
    key = "DEEPSIGNAL_WEBUI_PUBLIC_URL"
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        replaced = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"#{key}="):
                lines[i] = f"{key}={url}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={url}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"{key}={url}\n", encoding="utf-8")
