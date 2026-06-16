"""매일 유지보수 — 장부 동기화 + 진입기준 재학습. (launchd 매일 1회)

1) 로컬 매매 장부에 실제 체결 누적 + 손익 집계 출력
2) 적응형 진입밴드 재학습 → ADAPTIVE_ENTRY_BAND.json (기준 매일 갱신)
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
_PY = os.path.join(_ROOT, ".venv", "bin", "python")


def main() -> None:
    print(f"\n===== 매일 유지보수 {datetime.now().strftime('%Y-%m-%d %H:%M')} =====")
    # 1) 장부
    print("\n[1] 매매 장부 동기화")
    subprocess.run([_PY, os.path.join(_ROOT, "scripts", "trade_ledger_sync.py")], cwd=_ROOT)
    # 2) 진입기준 재학습
    print("\n[2] 진입기준 재학습")
    subprocess.run([_PY, os.path.join(_ROOT, "scripts", "adaptive_entry_learn.py"), "14"], cwd=_ROOT)


if __name__ == "__main__":
    main()
