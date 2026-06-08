# ops/ — 운영 환경 관리

운영 환경 설정 및 macOS launchd 자동 시작 관리.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `env.py` | 환경 변수 로드·검증 |
| `launchd.py` | launchd plist 생성·설치 |

## 주요 공개 API

```python
from deepsignal.crypto_trading.ops import load_env, install_launchd
```
