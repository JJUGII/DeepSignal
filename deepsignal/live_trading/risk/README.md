# risk/ — 리스크·안전 관리

주문 안전 가드, 실행 권한 검증, 런북(운영 절차서), 안전 감사를 담당한다. 모든 실주문은 이 레이어의 체크를 통과해야 한다. 안전 원칙의 최후 방어선이다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `risk_guard.py` | 글로벌 리스크 한도 검사 |
| `execution_guard.py` | 실행 권한 및 환경 검증 |
| `order_guard.py` | 개별 주문 유효성 가드 |
| `runbook_guard.py` | 런북 절차 준수 검사 |
| `runbook.py` | 운영 절차서(런북) 정의 |
| `safety_audit.py` | 안전 감사 로그 및 보고 |
| `checklist_generator.py` | 운영 체크리스트 생성 |

## 주요 공개 API

```python
from deepsignal.live_trading.risk import RiskGuard, ExecutionGuard, OrderGuard
```
