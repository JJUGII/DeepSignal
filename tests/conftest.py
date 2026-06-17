"""테스트 전역 안전 가드.

과거 launchd 설치 테스트가 실제 ~/Library/LaunchAgents의 plist를 손상시킨 사고
(auto_runner/crypto_auto_runner plist가 pytest tmp 경로로 덮어써져 러너가 죽음)가 있었다.
근본원인: 일부 테스트가 re-export 셸 모듈(deepsignal.live_trading.launchd_installer)의
launch_agents_dir/plist_path를 monkeypatch했는데, 실제 write_plist는 ops 모듈
(deepsignal.live_trading.ops.launchd_installer) 안의 진짜 함수를 호출해 패치가 안 먹었다.

아래 autouse 가드는 *모든* 테스트에서 실제 ops 모듈의 launch_agents_dir를 tmp로 강제
리디렉션한다. 개별 테스트의 패치 정확성과 무관하게, 어떤 테스트도 실제 LaunchAgents에
쓸 수 없다(plist_path는 launch_agents_dir에서 파생되므로 함께 차단됨).
개별 테스트가 자기 경로로 다시 monkeypatch하면 그게 우선한다(LIFO).
"""
from __future__ import annotations

import pytest

# (모듈경로, 함수명) — 실제 LaunchAgents 경로를 돌려주는 함수들
_LAUNCH_AGENTS_FUNCS = (
    ("deepsignal.live_trading.ops.launchd_installer", "launch_agents_dir"),
    ("deepsignal.crypto_trading.ops.launchd", "launch_agents_dir"),
)


@pytest.fixture(autouse=True)
def _guard_real_launch_agents_dir(tmp_path, monkeypatch):
    guard = tmp_path / "_LaunchAgents_guard"
    guard.mkdir(parents=True, exist_ok=True)
    for mod, name in _LAUNCH_AGENTS_FUNCS:
        monkeypatch.setattr(f"{mod}.{name}", lambda _g=guard: _g, raising=False)
    yield
