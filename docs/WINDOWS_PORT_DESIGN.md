# DeepSignal — Windows 포팅 설계서

> 목표: 현재 macOS(launchd)에서 도는 DeepSignal 전체 시스템을, **Windows에서 스크립트 한 번으로 설치·자동실행**되게 한다.
> 방식: **파이썬 슈퍼바이저(supervisor.py)** + **원클릭 부트스트랩(install_windows.ps1)**.
> 작성 기준: 2026-06-02 현재 코드.

---

## 1. 현황 분석 요약

### 1.1 그대로 가는 부분 (수정 불필요)
- 트레이딩/분석/ML/웹 로직 22개 모듈 전부 **순수 파이썬** (OS 무관).
- 의존성 전부 Windows 지원: `fastapi, uvicorn, pandas, numpy, lightgbm, scikit-learn, torch, yfinance, requests, websockets, python-dotenv, feedparser`.
- `~/.deepsignal/` (로그·상태) → `Path.home()` 기반이라 Windows에서 `C:\Users\<user>\.deepsignal\`로 자동 동작.
- LightGBM: macOS는 `libomp` 필요했으나 **Windows 휠은 OpenMP 내장** → 추가 설치 불필요.
- cloudflared: 코드가 `cloudflared` 명령만 호출 → `cloudflared.exe`만 PATH에 있으면 동작.

### 1.2 새로 만들거나 고쳐야 하는 부분
| 항목 | 현재(macOS) | Windows 대응 |
|------|------------|-------------|
| 프로세스 자동실행·감시 | launchd plist 12종 + `launchctl` | **supervisor.py 신규** |
| `os.getuid()` 3곳 | Unix 전용 | OS 분기 (아래 2.3) |
| Homebrew python 가드 | `main.py:3673`, `launchd_installer.py` | launchd 경로에서만 쓰임 → Windows는 우회 |
| 부팅/로그인 시 자동시작 | launchd RunAtLoad | 작업 스케줄러 항목 1개 |

> launchd 관련 모듈(약 12개)은 **Windows에서 호출하지 않으면 그만**. 삭제할 필요 없음(맥 호환 유지).

---

## 2. 관리 대상 프로세스 (총 11개)

현재 launchd에 등록된 실제 실행 명령 기준. `PY = .venv\Scripts\python.exe`, `ROOT = 설치 경로`.

### 2.1 상시 실행 (죽으면 재시작)
| 이름 | 명령 (인자) |
|------|------------|
| `web_ui` | `main.py web-ui --port 8765 --tunnel --no-browser --host 0.0.0.0` |
| `crypto_auto_runner` | `main.py crypto-auto-runner --broker upbit --interval-minutes 1.0 --take-profit-pct 2.0 --take-profit-buffer-pct 0.05 --stop-loss-pct -1.5 --stop-loss-buffer-pct 0.05 --min-volume-ratio 0.3 --crypto-universe all_krw --max-scan-markets 100 --output-dir outputs --wait-fill-seconds 60 --fill-poll-interval 3 --execute` |
| `overseas_auto_runner` | `main.py overseas-auto-runner --output-dir outputs` |
| `regime_trend_runner` | `main.py regime-trend-runner --execute --interval-minutes 5 --output-dir outputs` |
| `binance_stream` | `main.py binance-stream --top 30 --output-dir outputs/binance_stream --depth-levels 20 --duration 0` |
| `kis_stream` | `main.py kis-stream --paper` |
| `kis_overseas_stream` | `main.py overseas-stream --live` |
| `auto_runner` (국내주식 일일) | `main.py daily-ai-auto-runner --broker kis --output-dir outputs --plan-time 09:05 --report-time 15:40 --max-order-value 300000 --max-single-order-value 300000 --max-total-order-value 300000 --max-orders 3 --expires-minutes 420 --poll-interval 3 --loop-sleep-seconds 15 --timeout-seconds 10 --network` |

### 2.2 예약 실행 (하루 1회)
| 이름 | 시각(KST) | 명령 |
|------|----------|------|
| `edge_monitor` | 16:30 | `scripts/daily_edge_cycle.py` |
| `crypto_retrain_lgbm` | 03:10 | `main.py crypto-retrain-lgbm --output-dir outputs --horizon 5 --also-seq` |

### 2.3 제외/대체
- `launchd_health_check` → 슈퍼바이저 자체가 헬스체크 역할을 흡수하므로 **Windows에선 불필요**.

---

## 3. 슈퍼바이저 설계 (`supervisor.py`)

### 3.1 책임
1. **상시 프로세스 8개**를 자식으로 spawn → 죽으면 백오프 후 재시작.
2. **예약 프로세스 2개**를 지정 시각(KST)에 1회 실행.
3. 각 프로세스 stdout/stderr를 `~/.deepsignal/logs/<name>.log`로 리다이렉트(맥과 동일 경로).
4. Ctrl+C / 종료 신호 시 모든 자식 정리(graceful kill).
5. 상태 파일 `~/.deepsignal/supervisor_status.json` 기록(웹UI/디버깅용).

### 3.2 구조 (의사코드)
```python
# supervisor.py — OS 무관 (Windows/macOS 공통)
PROCESSES = [
  Proc("web_ui", [PY, "main.py", "web-ui", "--port", "8765", "--tunnel", ...], always=True),
  Proc("crypto_auto_runner", [...], always=True),
  ... # 2.1 표의 8개
]
SCHEDULED = [
  Sched("edge_monitor", [PY, "scripts/daily_edge_cycle.py"], hour=16, minute=30),
  Sched("crypto_retrain_lgbm", [...], hour=3, minute=10),
]

def run():
    for p in PROCESSES: p.start()           # 자식 spawn
    while not stopping:
        for p in PROCESSES:
            if p.dead(): p.restart_with_backoff()   # 재시작(지수 백오프, 상한)
        for s in SCHEDULED:
            if s.due_now(): s.run_once()
        write_status()
        sleep(5)
    for p in PROCESSES: p.terminate()        # 종료 시 정리
```

### 3.3 핵심 구현 포인트
- **CREATE_NO_WINDOW**: Windows에서 자식 콘솔 창이 안 뜨게 `subprocess.Popen(..., creationflags=CREATE_NO_WINDOW)` (Windows 전용 분기).
- **작업 디렉터리**: 모든 자식 `cwd=ROOT`.
- **환경변수**: `.env`는 각 CLI가 알아서 로드하므로 supervisor는 `PYTHONUNBUFFERED=1`만 주입.
- **백오프**: 1s → 2s → 5s → 15s → 30s(상한). 60초 내 5회 이상 죽으면 "비정상" 표시(로그 남기고 계속 시도).
- **예약 중복 방지**: `due_now()`는 "오늘 이미 실행했는지"를 상태 파일로 가드.
- **단일 인스턴스 락**: `~/.deepsignal/supervisor.lock` (중복 실행 방지).

---

## 4. 신규/수정 파일 목록

### 4.1 신규
| 파일 | 내용 |
|------|------|
| `supervisor.py` | 위 3장 슈퍼바이저 본체 (루트) |
| `scripts/windows/install_windows.ps1` | 부트스트랩(아래 5장) |
| `scripts/windows/START.bat` | supervisor 수동 시작 |
| `scripts/windows/STOP.bat` | supervisor + 자식 일괄 종료 |
| `scripts/windows/register_autostart.ps1` | 작업 스케줄러 "로그인 시 START" 등록 |
| `requirements-windows.txt` | torch CPU 휠 인덱스 포함 윈도우 의존성 |
| `.env.example` | 키 입력 템플릿(이미 있으면 재사용) |

### 4.2 수정 (OS 분기 — 최소)
| 파일 | 수정 |
|------|------|
| `deepsignal/web_ui/runner_manager.py` (`os.getuid()` 2곳) | `if hasattr(os,'getuid')` 가드 또는 Windows는 PID만 사용 |
| `deepsignal/live_trading/ops/launchd_installer.py:522` | launchd 경로 전용 → Windows에서 import 안 되게 지연 import (이미 그럴 수도) |

> `os.getuid()`는 launchd 도메인 계산·러너 PID 검증용. Windows에선 `os.getpid()` 기반으로 대체하거나 해당 분기를 건너뛰면 됨.

---

## 5. 설치 흐름 (`install_windows.ps1`)

```
1. 관리자 권한 확인(작업 스케줄러 등록용)
2. Python 3.11 존재 확인 → 없으면 안내(또는 winget install Python.Python.3.11)
3. python -m venv .venv
4. .venv\Scripts\pip install -r requirements-windows.txt
5. cloudflared.exe 다운로드 → scripts\windows\bin\ 에 배치 (또는 winget)
6. .env 없으면 .env.example 복사 → 사용자에게 "키 입력하세요" 안내
7. data\ outputs\ logs\ 디렉터리 생성
8. register_autostart.ps1 실행 → 로그인 시 supervisor 자동시작 등록
9. 완료 메시지: ".env에 키 입력 후 START.bat 실행 또는 재로그인"
```

**사용자 체감 절차**: `install_windows.ps1` 우클릭 실행 → `.env`에 API 키 입력 → 끝.

---

## 6. 주의/리스크

| 항목 | 내용 |
|------|------|
| **torch 용량** | Windows torch 휠 ~2.5GB. LSTM 안 쓰면 `requirements-windows.txt`에서 torch 제외해 경량화 가능(ML은 LightGBM만으로 동작). |
| **KIS/업비트 키** | `.env`에 평문 저장은 현재 구조 그대로. 1인 사용엔 무방. |
| **인라인 주석 금지** | `.env`에 `KEY=값 # 주석` 쓰면 값에 주석이 섞이는 버그 있음(과거 KIS 계좌번호 사고). 템플릿에 경고 주석으로 명시. |
| **시간대** | 예약 실행은 KST 기준. Windows 시스템 시계가 KST가 아니면 supervisor 내부에서 `zoneinfo("Asia/Seoul")`로 계산(코드에 이미 패턴 있음). |
| **방화벽** | 첫 실행 시 Python 네트워크 허용 팝업 → 허용 필요. |
| **테스트** | 실제 Windows 동작 검증은 사용자 PC에서 진행(개발 환경은 macOS만 실행 가능). |

---

## 7. 작업 순서 (구현 시)

1. `supervisor.py` 작성 (상시 8 + 예약 2, 백오프·로그·락·상태파일)
2. `os.getuid()` 3곳 OS 분기
3. `requirements-windows.txt` 작성 (torch CPU/제외 옵션 결정)
4. `install_windows.ps1` + `register_autostart.ps1` + START/STOP.bat
5. `.env.example` 정비(주석 경고 포함)
6. macOS에서 `supervisor.py` 자체 구동만 스모크 테스트(맥에서도 동작하므로 로직 검증 가능)
7. 사용자 Windows PC 실기 테스트 → 피드백 반영

---

## 8. 확정 필요 사항 (구현 전)

- [ ] **torch 포함 여부**: LSTM 시퀀스 모델 쓰는지? 안 쓰면 제외해 설치 2.5GB 절감.
- [ ] **설치 경로**: 고정(`C:\DeepSignal`) vs 사용자 선택.
- [ ] **자동시작 트리거**: 로그인 시 vs 부팅 시(서비스화 필요).
- [ ] **web_ui 터널**: Windows에서도 cloudflared 터널 유지할지(외부 접속용) vs 로컬만.
