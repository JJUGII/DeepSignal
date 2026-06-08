# risk/ — 리스크 및 포지션 관리

과매매 방지, 포지션 사이징, 페이퍼 모드 등 리스크 관리 레이어.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `overtrading.py` | 과매매 가드 (빈도·손실 제한) |
| `sizing.py` | 켈리 기반 포지션 사이징 |
| `gate_config.py` | 진입 게이트 설정 |
| `fund_policy.py` | 펀드형 자금 관리 정책 |
| `paper_mode.py` | 페이퍼 모드 설정 |
| `paper_state.py` | 페이퍼 상태 추적 |

## 주요 공개 API

```python
from deepsignal.crypto_trading.risk import OvertradingGuard, PositionSizer
```
