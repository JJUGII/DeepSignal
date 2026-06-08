# DeepSignal macOS Troubleshooting

## pytest 없음

- 문제: `python3 -m pytest -q` 실행 시 `No module named pytest`.
- 원인: 시스템 Python에 테스트 의존성이 설치되어 있지 않다.
- 해결: `./scripts/setup_macos.sh` 실행 후 `source .venv/bin/activate`, 또는 `./.venv/bin/python -m pytest -q` 사용.

## pandas import 실패

- 문제: `ModuleNotFoundError: No module named 'pandas'`.
- 원인: 시스템 Python으로 `main.py`를 직접 실행했거나 `.venv`가 활성화되지 않았다.
- 해결: `source .venv/bin/activate` 후 `python main.py --help`를 실행한다. macOS에서는 시스템 `python3 main.py` 직접 실행을 피한다.

## tkinter / _tkinter missing

- 문제: `dashboard` 실행 시 `No module named '_tkinter'`.
- 원인: 현재 Python 빌드에 Tk 지원이 포함되어 있지 않다.
- 해결: 일반 CLI에는 영향이 없다. 대시보드가 필요할 때만 Tk 포함 Python 설치를 검토한다.

## permission denied

- 문제: `./scripts/setup_macos.sh` 또는 `./scripts/test_macos.sh` 실행 시 권한 오류.
- 원인: shell script 실행 권한이 없다.
- 해결:

```bash
chmod +x scripts/setup_macos.sh scripts/test_macos.sh scripts/run_live_precheck_macos.sh
```

## compileall이 ._*.py로 실패

- 문제: `python -m compileall main.py deepsignal tests`에서 `source code string cannot contain null bytes`가 발생하고 경로가 `._*.py`로 표시된다.
- 원인: macOS AppleDouble 메타파일이 Python 소스처럼 인식된다.
- 해결: 먼저 dry-run으로 후보를 확인한 뒤 AppleDouble만 정리한다.

```bash
python main.py weekly-maintenance --output-dir outputs --archive-dir outputs/archive
python main.py report-health-check --output-dir outputs --db-path data/deepsignal.db
python main.py cleanup-reports --output-dir outputs --dry-run
python main.py cleanup-reports --output-dir outputs --apply --remove-appledouble
```

`weekly-maintenance`와 `report-health-check`는 AppleDouble 존재를 경고만 하며 삭제하지 않는다. `cleanup-reports`는 `output_dir` 내부만 처리한다. `deepsignal/` 또는 `tests/` 아래 생긴 `._*.py`는 별도 삭제가 필요할 수 있으며, 소스 파일 자체를 삭제하지 않는다.

## zsh / bash 실행 차이

- 문제: zsh에서 직접 실행하거나 source 방식이 달라 스크립트가 실패한다.
- 원인: 스크립트는 `#!/usr/bin/env bash`와 `BASH_SOURCE`를 사용한다.
- 해결: 파일을 직접 실행한다. 예: `./scripts/test_macos.sh`. `sh scripts/test_macos.sh`로 실행하지 않는다.

## KIS_ENV=live 경고

- 문제: `kis-check`에서 production API host 경고가 출력된다.
- 원인: `.env`의 `KIS_ENV=live`.
- 해결: 실계좌 조회/실주문 의도인지 확인한다. 모의나 설정 점검 중이면 `.env`를 운영자가 직접 확인해 `paper` 사용 여부를 결정한다. 키 값은 문서나 채팅에 붙여넣지 않는다.

## tokenP 403 Forbidden

- 문제: `kis-check --network`는 성공했지만 곧바로 `live-sync-account --network` 또는 `reconcile-live-account --network`에서 `/oauth2/tokenP` 403이 발생한다.
- 원인: KIS가 짧은 시간 내 중복 토큰 발급을 거부할 수 있다.
- 해결: `python main.py report-health-check --output-dir outputs --db-path data/deepsignal.db`로 기본 토큰 캐시 `outputs/.kis_token_cache.json` 존재와 만료 임박 여부를 먼저 진단한다. 캐시에는 access token과 만료시각만 저장되며 app secret·계좌번호는 저장하지 않는다. 캐시가 손상되었거나 만료가 의심될 때만 운영자가 파일을 삭제한 뒤 `kis-check --network`를 다시 실행한다.

## 운영 리포트가 오래되었거나 누락됨

- 문제: `OPS_DASHBOARD.html`, `REPORT_INDEX.html`, `RISK_ALERT.md` 등이 없거나 최신 JSON보다 오래되었다.
- 원인: `ops-dry-run`, `html-dashboard`, `report-index` 실행이 누락되었거나 `outputs/` 정리 후 인덱스를 재생성하지 않았다.
- 해결: 먼저 weekly maintenance dry-run 또는 health check로 현재 상태와 next actions를 확인한다.

```bash
python main.py weekly-maintenance --output-dir outputs --archive-dir outputs/archive
python main.py report-health-check --output-dir outputs --db-path data/deepsignal.db
python main.py ops-dry-run --output-dir outputs
python main.py html-dashboard --output-dir outputs
python main.py report-index --output-dir outputs --archive-dir outputs/archive
```

`weekly-maintenance`는 report-health-check, cleanup dry-run, daily summary, HTML dashboard, report-index를 묶어 실행하지만 cleanup 적용은 하지 않는다. `report-health-check`는 진단 리포트만 만들며 파일 삭제, cleanup 적용, 네트워크 조회, 알림 전송, 실주문, SELL 자동화, KIS POST를 수행하지 않는다.

## safety-audit가 WARNING 또는 BLOCKED

- 문제: `python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db` 결과가 `SAFETY_AUDIT_WARNING` 또는 `SAFETY_AUDIT_BLOCKED`.
- 원인: 체크리스트 누락, `SAFETY_RULES.md` 필수 문구 누락, 운영 리포트 누락/오래됨, reconcile mismatch, stale account snapshot, partial fill open, final confirmation 자동화 의심 흔적 등이 있을 수 있다.
- 해결: `outputs/SAFETY_AUDIT.md`와 `outputs/safety_audit_*.json`의 Issues/Next Actions를 확인한다. BLOCKED면 실주문을 중단하고 원인을 해소한 뒤 다시 실행한다.

```bash
python main.py generate-checklists --output-dir outputs/checklists
python main.py weekly-maintenance --output-dir outputs --archive-dir outputs/archive
python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db
```

`safety-audit`는 로컬 읽기 전용이다. 네트워크 호출, KIS POST, `live-approve`, `--execute`, SELL 자동화, cleanup apply, archive 이동, 파일 삭제를 수행하지 않는다.

## weekly-maintenance가 WARNING

- 문제: `weekly-maintenance` 결과가 `WEEKLY_MAINTENANCE_WARNING`.
- 원인: health warning, cleanup dry-run 후보, 오래된/누락된 리포트, dashboard stale, token cache 만료 임박 등이 있을 수 있다.
- 해결: `outputs/WEEKLY_MAINTENANCE.md`, `outputs/REPORT_HEALTH.md`, `outputs/report_cleanup_audit_*.json`, `outputs/REPORT_INDEX.html`을 순서대로 확인한다. cleanup 실제 적용은 audit 확인 후 별도 수동 명령으로만 수행한다.

```bash
python main.py cleanup-reports --output-dir outputs --dry-run
python main.py cleanup-reports --output-dir outputs --apply --archive --archive-dir outputs/archive
```

`weekly-maintenance` 자체에는 `--apply`, `--archive`, `--network`, `--send` 옵션이 없고 삭제/이동/네트워크/알림/주문을 수행하지 않는다.

## trading-session CLOSED

- 문제: `trading-session-check` 결과가 `CLOSED`.
- 원인: 주말, 휴일, 장 시작 전, 장 마감 후, 또는 `DEEPSIGNAL_MARKET_HOLIDAYS` 설정.
- 해결: 정규장 여부와 `.env`의 세션 변수를 확인한다. CLOSED 상태에서 `live-approve --execute`는 session guard로 차단되어야 한다.

## LIVE_EXECUTION_BLOCKED_BY_RUNBOOK

- 문제: `live-approve --require-pre-trade-runbook --execute`가 runbook guard에서 차단된다.
- 원인: 최근 `PRE_TRADE_READY` 리포트가 없거나 TTL 만료, plan/symbol/qty/limit 불일치.
- 해결: `pre-trade-runbook --network`를 다시 실행하고 리포트의 plan/symbol/qty/limit를 실행 명령과 대조한다.

## duplicate order blocked

- 문제: `LIVE_ORDER_BLOCKED_BY_GUARD` 또는 duplicate 관련 경고.
- 원인: 최근 동일 종목 주문, pending 상태, reconcile mismatch, stale snapshot.
- 해결: 증권사 앱에서 주문/체결 상태를 확인하고 `live-sync-account --network` 및 `reconcile-live-account --network`를 다시 수행한다. 자동 재주문하지 않는다.

## live-sync positions none인데 reconcile mismatch

- 문제: `live-sync-account --network`는 `Positions: (none)` 및 `real_positions rows=0`인데, 직후 `reconcile-live-account --network`는 `Missing in DB`를 출력한다.
- 원인: KIS 잔고조회 응답이 두 명령 사이에 달라졌거나, 오래된 DB 포지션을 최신처럼 읽는 정책 문제가 있을 수 있다.
- 해결: 최신 DB 포지션은 최신 `real_account_snapshots.snapshot_time` 기준으로만 읽는다. 최신 계좌 스냅샷에 포지션이 0개면 빈 목록이 정상이다. 아래 명령으로 KIS raw 구조의 행 수와 키 목록만 확인한다.

```bash
python main.py live-sync-account --broker kis --network --debug-raw
python main.py reconcile-live-account --broker kis --network --debug-raw
```

`--debug-raw`는 원문 값을 저장하지 않고 `output1`/`output2`의 row count와 keys만 저장한다. 계좌번호, 토큰, 키 값은 출력/저장하지 않는다.

## KIS position 파싱 기준

- 문제: `output1`에 보유 수량 0인 행이나 현금/요약성 행이 섞여 포지션으로 오인될 수 있다.
- 원인: KIS 응답 필드가 계좌/환경에 따라 다르며, `hldg_qty`와 `ord_psbl_qty` 의미가 다를 수 있다.
- 해결: DeepSignal은 `pdno` 6자리 숫자 행만 포지션 후보로 보고, `hldg_qty > 0`을 실보유 기준으로 사용한다. `hldg_qty`가 없을 때만 `ord_psbl_qty`를 fallback으로 사용하며, `quantity <= 0`은 제외한다.

## partial fill blocked

- 문제: partial fill 관련 guard 차단.
- 원인: `real_fill_history` 기준으로 미체결 잔량이 남아 있거나 부분 체결 상태가 감지됨.
- 해결: `live-order-status --network`와 `live-fill-summary`로 체결/잔량을 확인한다. 잔량이 해결되기 전 재주문하지 않는다.

## notify-alerts가 전송하지 않음

- 문제: `python main.py notify-alerts --dry-run`을 실행했지만 Telegram/Discord에 메시지가 오지 않는다.
- 원인: 기본값이 dry-run이므로 네트워크 호출을 하지 않는다.
- 해결: audit log `outputs/notification_audit_*.json`을 먼저 확인한다. 실제 alert-only 전송이 필요하면 `.env`의 알림 토큰/webhook 값을 설정한 뒤 `--send`를 명시한다.

```bash
python main.py notify-alerts --dry-run --output-dir outputs
python main.py notify-alerts --channel telegram --send --output-dir outputs
python main.py notify-alerts --channel discord --send --output-dir outputs
```

`notify-alerts`는 알림 전용이며 주문 실행, SELL 자동화, 시장가, KIS POST를 수행하지 않는다.

## Telegram / Discord 설정 누락

- 문제: `notify-alerts --send`가 `missing_config`로 실패한다.
- 원인: Telegram bot token/chat id 또는 Discord webhook URL이 `.env`에 없다.
- 해결: `.env.example`의 아래 키를 `.env`에 복사해 실제 값을 운영자가 직접 설정한다. 실제 토큰과 webhook URL은 커밋하거나 채팅에 붙여넣지 않는다.

```bash
DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN=
DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID=
DEEPSIGNAL_NOTIFY_DISCORD_WEBHOOK_URL=
DEEPSIGNAL_NOTIFY_DEFAULT_CHANNEL=telegram
```
