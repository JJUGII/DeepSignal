# execution/ — 주문 실행 레이어

주문 계획 생성부터 실제 업비트 API 호출까지의 실행 레이어. 지정가 주문 타임아웃, 미체결 매도 추적·재접수, 미체결 매수 취소를 담당한다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `order_plan.py` | 주문 계획 데이터 구조 |
| `engine.py` | 매수/매도 실행 엔진 |
| `quality.py` | 실행 품질 평가 |
| `order_manager.py` | 미체결 주문 관리 및 재접수 |
| `order_fill.py` | 체결 추적 |

## 주요 공개 API

```python
from deepsignal.crypto_trading.execution import ExecutionEngine
```
