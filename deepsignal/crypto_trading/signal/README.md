# signal/ — 시그널 생성 레이어

시장 데이터·보유 포지션을 분석해 매수/매도 추천(CryptoRecommendation)을 생성한다. 시그널 스코어링, 손익 분류, 호가 단위 가격 계산, 매수 품질 필터를 포함한다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `recommendation.py` | 추천 생성 (CryptoRecommendation) |
| `recommendation_quality.py` | 추천 품질 평가 |
| `diagnostics.py` | 추천 진단·디버깅 유틸리티 |
| `scorer.py` | 기술적·매크로 점수 계산 |
| `sell_triggers.py` | TP/SL 분류 및 매도 트리거 |
| `sell_pricing.py` | 지정가 매도 가격 계산 |
| `buy_quality.py` | 매수 품질 필터 |
| `universe.py` | 마켓 유니버스 관리 |

## 주요 공개 API

```python
from deepsignal.crypto_trading.signal import CryptoRecommendation
```
