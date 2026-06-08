"""EDGE_GATE.json 소비 — 검증된 엣지가 있는 전략만 live 매매 허용.

엣지 모니터(scripts/edge_monitor.py)가 매일 `outputs/EDGE_GATE.json`을 갱신한다.
deploy=true(엣지가 연속 N회 지속)인 전략만 신규 매수를 허용하고, 그 외(미평가/엣지없음/
파일없음)는 **기본 차단**한다. 즉 "검증된 엣지가 나타나는 날 자동으로 열린다".

env:
- DEEPSIGNAL_ENFORCE_EDGE_GATE (기본 true) — false면 게이트 무시(엣지 검증 없이 매매, 테스트용).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

GATE_FILE = "EDGE_GATE.json"

# live 매매 경로 → edge_monitor 전략 매핑 (#F)
# 각 자산군의 live 자동매수는 대응 전략이 deploy=true(엣지 검증)일 때만 허용된다.
# - crypto       → crypto_scalp_5m (현 코인 단타 신호)
# - kis_domestic → momentum_KR (국내주식 최선 엣지 후보; 검증 시 도메스틱 매수 허용)
# - overseas     → momentum_US (미국주식 최선 엣지 후보)
LIVE_STRATEGY_MAP = {
    "crypto": "crypto_scalp_5m",
    "kis_domestic": "momentum_KR",
    "overseas": "momentum_US",
    "regime_trend": "regime_trend_sp500",  # 지수 추세추종(유일한 robust 엣지)
}


def strategy_for_live(path: str) -> str:
    return LIVE_STRATEGY_MAP.get(path, path)


def edge_gate_enforced() -> bool:
    return os.environ.get("DEEPSIGNAL_ENFORCE_EDGE_GATE", "true").strip().lower() in ("1", "true", "yes")


def edge_gate_status(output_dir: str | Path, strategy: str) -> dict:
    """전략의 게이트 상태. {found, deploy, edge, reason}."""
    p = Path(output_dir) / GATE_FILE
    if not p.is_file():
        return {"found": False, "deploy": False, "edge": False, "reason": "EDGE_GATE.json 없음"}
    try:
        g = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"found": False, "deploy": False, "edge": False, "reason": "EDGE_GATE.json 읽기 실패"}
    s = (g.get("strategies") or {}).get(strategy)
    if not isinstance(s, dict):
        return {"found": False, "deploy": False, "edge": False, "reason": f"{strategy} 미평가"}
    return {"found": True, "deploy": bool(s.get("deploy")), "edge": bool(s.get("edge")),
            "reason": ("엣지 검증 배포(deploy)" if s.get("deploy")
                       else ("엣지 감지(미지속)" if s.get("edge") else "엣지 없음")),
            "metrics": s.get("metrics", {})}


def edge_gate_allows_buy(output_dir: str | Path, strategy: str) -> tuple[bool, str]:
    """신규 매수 허용 여부. 게이트 미적용(env off)이면 항상 허용.

    적용 시: deploy=true인 전략만 허용, 그 외 차단(기본 닫힘).
    """
    if not edge_gate_enforced():
        return (True, "엣지 게이트 미적용(env off)")
    st = edge_gate_status(output_dir, strategy)
    if st["deploy"]:
        return (True, st["reason"])
    return (False, f"엣지 게이트 차단: {st['reason']}")
