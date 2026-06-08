# runner/ — 주식 일봉 AI 자동매매 러너

주식 일봉 AI 자동매매 러너. 매일 장 시작 전 AI 추천을 받아 조건 충족 시 자동 실행하는 메인 루프와 거래 워크플로우를 담당한다. 신선도 검사와 상태 조회를 통해 안정적인 실행 흐름을 보장한다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `daily_ai_auto_runner.py` | 일봉 AI 자동매매 메인 루프 |
| `trading_workflow.py` | 거래 워크플로우 단계별 조율 |
| `freshness.py` | AI 추천 데이터 신선도 검사 |
| `status_reader.py` | 자동매매 실행 상태 조회 |

## 주요 공개 API

```python
from deepsignal.live_trading.runner import DailyAIAutoRunner
```
