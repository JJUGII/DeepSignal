# outcomes/ — 결과 추적 및 성과 튜닝

추천 결과 추적, 보유 현황, 성과 기반 임계값 자동 튜닝.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `trades.py` | 거래 기록 |
| `holdings.py` | 보유 코인 현황 |
| `recommendation_outcomes.py` | 추천 결과 DB |
| `threshold_tuning.py` | 성과 기반 TP/SL 자동 조정 |

## 주요 공개 API

```python
from deepsignal.crypto_trading.outcomes import RecommendationOutcomes
```
