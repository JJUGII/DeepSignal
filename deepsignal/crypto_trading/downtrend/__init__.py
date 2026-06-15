"""하락장 반등 단타 엔진 (downtrend rebound scalp).

기존 L10 상승추격 스캘퍼와 별개의 보수 엔진. 설계 근거: AI_CONTEXT/L10_SCALPING_ENGINE.md +
외부 검토(하락장은 '더 사는' 게 아니라 '대부분 안 사고 고확률 반등만 짧게'). 전 모듈 순수·
테스트 가능하게 작성하고, 라이브 배선은 env-gated(기본 off) + Phase5 검증 통과 시에만 deploy.

구성:
  profile.py     — 하락장 보수 진입/청산 프로파일 + L10 paper-only 가드 (Phase1)
  regime.py      — 코인-네이티브 4장세 판정 (Phase2)
  setups.py      — 반등 셋업 3종 (Phase3)
  ev_gate.py     — 기대값 게이트 + 장세별 점수기준 (Phase4)
  validation.py  — 배포 검증 게이트 (Phase5)
  engine.py      — 오케스트레이터 (Phase5)
"""
