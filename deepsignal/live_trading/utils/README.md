# utils/ — 공통 유틸리티

시간 유틸리티(KST 변환), 장 운영 세션 판단, 운영자 비활성 창 감지 등 공통 유틸리티. 여러 서브패키지에서 공유하는 헬퍼 함수와 데이터 구조를 제공한다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `time_utils.py` | KST 시간대 변환 및 시간 유틸리티 |
| `operator_inactive_window.py` | 운영자 비활성 시간대 감지 |
| `operator_labels.py` | 운영자 레이블 및 태그 관리 |
| `trading_session.py` | 장 운영 세션 판단 (개장·폐장·휴장) |
| `test_order_plan.py` | 주문 계획 테스트 유틸리티 |

## 주요 공개 API

```python
from deepsignal.live_trading.utils import get_kst_now, is_trading_session
```
