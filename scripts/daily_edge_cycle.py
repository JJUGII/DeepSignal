"""매일 엣지 사이클 (파이썬 entrypoint — launchd가 venv python으로 직접 실행).

bash 래퍼는 macOS launchd가 외장 볼륨에서 실행을 막아(Operation not permitted),
기존에 동작하는 agent들처럼 venv python으로 직접 구동한다.

단계: ① 주식 최신 일봉 append(1mo) → ② 엣지 모니터 재평가.
매매는 하지 않는다 (데이터 수집 + 분석/게이팅 판단만).
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(script: str, argv: list[str]) -> None:
    sys.argv = argv
    try:
        runpy.run_path(str(ROOT / "scripts" / script), run_name="__main__")
    except SystemExit:
        pass
    except Exception as e:  # noqa: BLE001 — 한 단계 실패해도 다음 단계 계속
        print(f"[{script}] 오류: {e!r}", flush=True)


def main() -> None:
    print("=== 데일리 엣지 사이클 시작 ===", flush=True)
    run("backfill_stock_data.py", ["backfill_stock_data.py", "1mo"])   # 주식 일봉 append
    run("backfill_macro_data.py", ["backfill_macro_data.py", "1mo"])   # 거시지표 append
    run("edge_monitor.py", ["edge_monitor.py"])                        # 성장한 데이터로 재평가
    print("=== 데일리 엣지 사이클 완료 ===", flush=True)
    # 뉴스: RSS는 최근만 제공 → 과거 백필 불가. 전방 축적이 필요하면 별도로
    # `python main.py collect-news`를 스케줄에 추가(현 엣지 전략엔 미사용).


if __name__ == "__main__":
    main()
