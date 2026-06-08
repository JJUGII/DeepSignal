# broker/ — KIS 한국투자증권 REST API 어댑터

KIS 한국투자증권 REST API 어댑터 및 드라이런 브로커. 토큰 캐시, 주문 상태 조회, 자동실행 정책을 포함한다. 모든 주식 주문은 이 패키지를 통해서만 집행된다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `kis_broker.py` | KIS REST API 기반 실제 브로커 구현 |
| `kis_config.py` | KIS API 접속 설정 및 환경 변수 |
| `kis_token_cache.py` | KIS OAuth 토큰 발급 및 캐싱 |
| `kis_order_status.py` | 주문 상태 조회 및 파싱 |
| `kis_auto_execute_policy.py` | 자동 실행 정책 (종목별 조건) |
| `kis_recommendation_config.py` | AI 추천 연동 설정 |
| `dry_run_broker.py` | 실주문 없이 로직만 검증하는 드라이런 브로커 |
| `interface.py` | 브로커 추상 인터페이스 정의 |

## 주요 공개 API

```python
from deepsignal.live_trading.broker import KISBroker, DryRunBroker, BrokerInterface
```
