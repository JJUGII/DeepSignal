# broker/ — 업비트 REST API 어댑터

업비트 REST API 어댑터. 인증(JWT), 설정 로드, 주문/잔고/시세 조회를 담당한다. 브로커 레이어 위의 코드는 이 패키지를 통해서만 업비트와 통신한다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `auth.py` | JWT 서명 및 인증 헤더 생성 |
| `config.py` | API 키·설정 로드 |
| `broker.py` | 잔고·주문·시세 REST 호출 |

## 주요 공개 API

```python
from deepsignal.crypto_trading.broker import UpbitBroker
```
