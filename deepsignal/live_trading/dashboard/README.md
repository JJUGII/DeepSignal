# dashboard/ — 대시보드·리포트 뷰어

HTML 대시보드, 운영 현황 뷰어, 드라이런 시뮬레이션 결과 표시를 담당한다. 매매 현황과 리포트를 시각화하여 운영자가 빠르게 상태를 파악할 수 있도록 한다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `html_dashboard.py` | HTML 형식 대시보드 생성 |
| `ops_dashboard.py` | 운영 현황 대시보드 |
| `local_viewer.py` | 로컬 브라우저 기반 뷰어 |
| `archive_viewer.py` | 과거 리포트 아카이브 뷰어 |
| `ops_dry_run.py` | 드라이런 시뮬레이션 결과 표시 |

## 주요 공개 API

```python
from deepsignal.live_trading.dashboard import HtmlDashboard, OpsDashboard
```
