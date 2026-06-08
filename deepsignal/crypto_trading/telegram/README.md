# telegram/ — 텔레그램 봇 통합

텔레그램 봇을 통한 승인 요청·메뉴·콜백 처리.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `flow.py` | 승인 요청·폴링 흐름 |
| `menu.py` | 텔레그램 메뉴 처리 |
| `offset.py` | 업데이트 오프셋 관리 |

## 주요 공개 API

```python
from deepsignal.crypto_trading.telegram import request_approval
```
