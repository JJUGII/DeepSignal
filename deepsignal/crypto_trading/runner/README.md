# runner/ — 트레이딩 엔진 메인 루프

트레이딩 엔진의 메인 루프. WebSocket 이벤트 드리븐 4-스레드 러너(ws_runner)와 분석 틱 유틸리티(auto_runner)를 포함한다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `ws_runner.py` | 4-스레드 WebSocket 기반 메인 러너 |
| `ws_stream.py` | 업비트 WebSocket 스트림 |
| `auto_runner.py` | 분석 틱·상태 관리 유틸리티 |
| `execute_policy.py` | 자동실행 정책 |

## 주요 공개 API

```python
from deepsignal.crypto_trading.runner import run_ws_runner
```
