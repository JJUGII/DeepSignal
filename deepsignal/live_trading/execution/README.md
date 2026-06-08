# execution/ — 주문 실행 레이어

주문 실행·체결 추적·계좌 동기화 레이어. 비활성 시간대 자동 실행, 승인된 주문 처리, 미체결 주문 추적을 담당한다. 모든 실제 주문 집행 로직이 이 패키지에 집중된다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `order_executor.py` | 실주문 실행 엔진 |
| `order_plan.py` | 주문 계획 생성 및 검증 |
| `inactive_auto_execute.py` | 운영자 비활성 시간대 자동 실행 |
| `approved_execution.py` | 텔레그램 승인 이후 주문 처리 |
| `fill_tracker.py` | 미체결·부분체결 주문 추적 |
| `account_sync.py` | 실계좌 잔고·포지션 동기화 |
| `reconcile.py` | 계획 vs 실행 결과 대조 |

## 주요 공개 API

```python
from deepsignal.live_trading.execution import LiveOrderExecutor, FillTracker
```
