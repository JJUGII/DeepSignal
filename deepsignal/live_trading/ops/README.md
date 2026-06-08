# ops/ — 운영·리포트·launchd 관리

macOS launchd 설치·헬스체크, 일간/주간 리포트 생성·정리, 운영 요약을 담당한다. 자동매매 시스템의 지속적인 운영과 모니터링을 지원하는 모든 운영 도구가 포함된다.

## 포함 모듈

| 파일 | 설명 |
|------|------|
| `launchd_installer.py` | macOS launchd plist 설치 |
| `launchd_health_check.py` | launchd 서비스 헬스 체크 |
| `launchd_health_installer.py` | 헬스체크 서비스 설치 |
| `report_cleanup.py` | 오래된 리포트 파일 정리 |
| `report_health.py` | 리포트 시스템 상태 점검 |
| `report_index.py` | 리포트 인덱스 생성 |
| `weekly_maintenance.py` | 주간 유지보수 작업 |
| `weekly_report_bundle.py` | 주간 리포트 번들 생성 |
| `daily_ops_summary.py` | 일간 운영 요약 |
| `sell_plan.py` | 매도 계획 생성 및 관리 |

## 주요 공개 API

```python
from deepsignal.live_trading.ops import LaunchdInstaller, DailyOpsSummary
```
