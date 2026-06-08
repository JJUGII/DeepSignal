# telegram/ — Telegram 통합

텔레그램 봇을 통한 승인 요청, 자동 실행, 진행 상황 알림, 운영자 메시지를 처리한다. 운영자가 모바일에서 매매를 승인하거나 취소할 수 있는 인터페이스를 제공한다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `approval.py` | 텔레그램 승인 요청 및 응답 처리 |
| `auto_execute.py` | 승인 없이 자동 실행되는 텔레그램 트리거 |
| `menu_cache.py` | 텔레그램 인라인 메뉴 캐싱 |
| `operator_messages.py` | 운영자용 메시지 포맷 및 전송 |
| `progress_notify.py` | 매매 진행 상황 실시간 알림 |
| `test_sender.py` | 텔레그램 메시지 전송 테스트 |
| `user_format.py` | 사용자 향 메시지 포맷팅 |
| `notification_center.py` | 중앙 알림 허브 |

## 주요 공개 API

```python
from deepsignal.live_trading.telegram import TelegramApproval, NotificationCenter
```
