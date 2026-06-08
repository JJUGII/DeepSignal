# DeepSignal macOS Operation Guide

## 1. 기본 원칙

- macOS 운영은 반드시 프로젝트 `.venv`를 사용한다.
- 시스템 `python3`로 직접 실행하지 않는다. macOS 기본 Python은 버전이나 의존성이 부족할 수 있다.
- 실주문은 기존 `live-approve --execute` 경로에서만 가능하다.
- Telegram approval workflow는 승인 요청/승인 기록/수동 실행 명령 안내만 수행한다. final-confirm을 대체하지 않으며 `live-approve`, `--execute`, `--allow-live-env`, KIS POST를 자동 호출하지 않는다.
- `scripts/run_live_precheck_macos.sh`는 조회/점검 전용이다. `live-approve` 또는 `--execute`를 호출하지 않는다.
- 자동 주문 스크립트, cron, launchd 실주문 자동화는 만들지 않는다.

## 2. 설치

프로젝트 루트에서 실행한다.

```bash
chmod +x scripts/setup_macos.sh
./scripts/setup_macos.sh
source .venv/bin/activate
```

수동 설치가 필요하면 다음 순서를 사용한다.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-macos.txt
```

## 3. 기본 테스트

```bash
source .venv/bin/activate
python -m compileall main.py deepsignal tests
python -m pytest -q
python main.py --help
python main.py trading-session-check --now 2026-05-15T10:00:00+09:00
python main.py kis-check
```

또는 macOS 테스트 스크립트를 사용한다.

```bash
./scripts/test_macos.sh
```

`kis-check` 기본 실행은 오프라인 설정 검증이다. `--network`는 OAuth HTTP 테스트가 필요할 때만 수동으로 실행한다.

KIS OAuth access token은 기본 `outputs/.kis_token_cache.json`에 저장된다. 이 파일에는 access token과 만료시각, `KIS_ENV`, app key hash만 저장되며 app secret, 계좌번호, app key 원문은 저장하지 않는다. `kis-check --network` 후 이어지는 `live-sync-account --network` / `reconcile-live-account --network`는 이 캐시를 재사용해 `tokenP` 반복 호출을 줄인다.

## 4. 실전 운영 전 체크

아래 순서는 운영자가 직접 확인하며 진행한다. 자동 실주문 스크립트로 묶지 않는다.

1. `source .venv/bin/activate`
2. 장 시작 전 AI 운영 계획: `python main.py daily-ai-trade-plan --broker kis --network --output-dir outputs`
3. Telegram 승인 요청: `python main.py telegram-approval-request --plan outputs/live_order_plan_ai_latest.json --send --output-dir outputs`
4. 운영자가 Telegram 승인 후 터미널에서 직접 실행: `python main.py execute-last-approved --output-dir outputs`
5. 장 종료 후 일일 리포트: `python main.py daily-ai-trade-report --broker kis --network --output-dir outputs`
6. 상태 확인: `python main.py daily-ai-status --output-dir outputs` (plan/latest order plan freshness·`generated_at` source 확인)
7. stale plan이면 `daily-ai-trade-plan`을 다시 실행한 뒤 Telegram 승인부터 재진행
8. 안전 점검: `python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db` (`AI 일일 매매 운영 Freshness` 섹션 확인)
9. 주말/장외 안전 점검: `python main.py ops-dry-run --output-dir outputs`
10. 로컬 대시보드 열기: `python main.py open-dashboard --output-dir outputs --open`
11. 장중 조회까지 포함한 점검: `python main.py ops-dry-run --network --broker kis --output-dir outputs --archive-dir outputs/archive`
12. 개별 확인이 필요하면 `python main.py trading-session-check`
13. `python main.py kis-check` 또는 필요한 경우 `python main.py kis-check --network`
14. `python main.py live-sync-account --broker kis --network`
15. `python main.py reconcile-live-account --broker kis --network`
16. `python main.py pre-trade-runbook --broker kis --network --plan outputs/live_order_plan.json --symbol 005930 --quantity 1 --limit-price 70000 --allow-symbol 005930 --output-dir outputs`
17. `PRE_TRADE_READY` 리포트와 plan/symbol/qty/limit 일치를 수동 확인
18. 필요 시에만 기존 guard 기반 CLI에서 `live-approve --require-pre-trade-runbook --execute`를 수동 실행
    - Telegram 승인 경로를 사용할 경우 먼저 `telegram-approval-request --plan ...` dry-run 산출물을 확인하고, `--send` / `telegram-approval-listen`으로 승인 audit을 남긴다. 이후 운영자가 터미널에서 `python main.py execute-last-approved --output-dir outputs`를 직접 실행한다.
19. `python main.py post-trade-runbook --broker kis --network --audit outputs/live_approval_audit_YYYYMMDD_HHMMSS.json --with-summary --output-dir outputs`
    - 임계값 조정이 필요하면 예: `--stop-loss-pct -0.05 --take-profit-pct 0.12 --warn-loss-pct -0.02 --warn-profit-pct 0.08`
20. `python main.py notify-alerts --dry-run --output-dir outputs`
21. 필요 시에만 `python main.py notify-alerts --channel telegram --send --output-dir outputs` 또는 Discord 채널로 alert-only 전송
22. `python main.py open-dashboard --output-dir outputs --open`
23. 주간/월간 dry-run 점검: `python main.py weekly-maintenance --output-dir outputs --archive-dir outputs/archive`
24. `WEEKLY_MAINTENANCE.md`, `REPORT_HEALTH.md`, cleanup audit, `REPORT_INDEX.html` 확인
25. 확인 후 필요 시에만 `python main.py cleanup-reports --output-dir outputs --apply --archive --archive-dir outputs/archive` 수동 실행
26. `python main.py open-dashboard --output-dir outputs --open-index`

`post-trade-runbook --with-summary`는 기존 post-trade 조회 범위 안에서 order status, fill, account sync, reconcile, risk-check를 수행한 뒤 `ops-dashboard`, `sell-plan`, `daily-ops-summary`, `html-dashboard`를 순서대로 생성한다. 기본 `post-trade-runbook` 동작은 유지되며, `--with-summary`를 붙였을 때만 사후 리포트 체인을 실행한다.

`post-trade-runbook`의 risk 단계는 `risk-check`와 동일한 `--stop-loss-pct`, `--take-profit-pct`, `--warn-loss-pct`, `--warn-profit-pct` 옵션을 받는다. 지정한 값은 post-trade JSON summary와 Markdown Risk Policy에 기록된다.

`ops-dry-run`은 세션·KIS 설정·risk/ops/sell/daily/html/index 생성을 한 번에 묶는 점검 명령이다. 기본 실행은 네트워크 없이 로컬 파일과 DB만 읽고, `--network`를 붙였을 때만 KIS OAuth·잔고조회·reconcile 조회를 포함한다. 장외/주말에는 session closed를 warning으로 기록하고 리포트 생성을 계속한다.

`open-dashboard`는 `outputs/OPS_DASHBOARD.html`, `outputs/REPORT_INDEX.html`, 주요 Markdown 리포트 경로를 보여준다. 기본은 목록 출력만 하며, `--open` 또는 `--open-index`를 붙였을 때만 로컬 `file://` HTML을 기본 브라우저로 연다.

`report-health-check`는 운영 산출물/DB/token cache 상태를 진단해 `REPORT_HEALTH.md`와 `report_health_*.json`을 생성한다. 오래된 account/reconcile/risk 리포트, stale dashboard, AppleDouble, token cache 만료 임박, output 파일 수 초과를 경고하지만 파일 삭제·cleanup 실행·네트워크 조회는 하지 않는다.

`weekly-maintenance`는 주간 점검용 dry-run 명령이다. 내부에서 `report-health-check`, `cleanup-reports` dry-run, `daily-ops-summary`, `html-dashboard`, `report-index`를 순서대로 실행하고 `WEEKLY_MAINTENANCE.md`를 만든다. `--apply`, `--archive`, `--network`, `--send` 옵션이 없으며 파일 삭제/이동/네트워크/알림/주문을 하지 않는다.

`weekly-report-bundle`은 주간 점검과 maintenance 알림 dry-run을 실행한 뒤 핵심 Markdown/HTML/JSON을 `outputs/weekly_bundles/weekly_bundle_YYYYMMDD_HHMMSS/` 폴더로 복사하고 `BUNDLE_INDEX.md` / `BUNDLE_INDEX.html`을 만든다. `.env`, token cache, DB, 소스 코드는 포함하지 않는다.

`generate-checklists`는 daily/weekly/pre-market/post-trade/weekly maintenance 운영 순서를 Markdown 체크리스트로 생성한다. 실제 스케줄러가 아니며 cron, launchd, plist, 자동 실행 파일을 만들지 않는다. 매일/매주 운영은 이 체크리스트를 참고해 운영자가 직접 수동 실행하는 방식을 권장한다.

`safety-audit`는 실주문 전 또는 주간 점검 전 로컬 산출물과 선택 DB를 읽기 전용으로 감사한다. 체크리스트/SAFETY_RULES, 주요 운영 리포트, 최신 reconcile/account/risk/fill/audit 파일, pre-trade readiness, partial fill, stale snapshot, final confirmation 자동화 의심 흔적을 확인하고 `SAFETY_AUDIT.md`를 생성한다.

`report-index`와 `html-dashboard`는 Safety Audit 정보가 있으면 status, latest audit time, `SAFETY_AUDIT.md`, 최신 `safety_audit_*.json`, warning/blocked count를 로컬 상대 링크로 표시한다. Safety Audit 파일이 없어도 dashboard/index 생성은 실패하지 않고 `NOT_AVAILABLE`로 표시한다.

`archive-viewer`는 `outputs/`와 `outputs/archive/` 아래 과거 운영 리포트를 metadata 중심으로 스캔해 `ARCHIVE_VIEWER.html`을 생성한다. Safety Audit, weekly maintenance, report health, risk, reconcile, live account snapshot, live approval audit, fill summary, cleanup audit, weekly bundle index 등을 로컬 상대 링크로 탐색할 수 있다.

Archive Viewer HTML은 기본 표를 항상 표시하고, inline JS가 동작하는 환경에서는 report type/status/severity/text/date range 필터, Only warnings/errors, Latest only 토글, Modified Time/Type/Status/Severity/Size 정렬을 제공한다. 상단 `Needs Attention`은 warning 이상, blocked/error/failed 계열, reconcile mismatch, partial fill open, stale snapshot, safety audit blocked를 빠르게 모아 보여준다. JSON export에는 `summary`, `filters_available`, `entries`, `needs_attention`, `latest_by_type`만 저장하며 민감 원문은 포함하지 않는다.

[실전-34] Archive Viewer와 일부 report/dashboard 표시 문구는 한국어 운영 UI label을 사용한다. `safety_audit`, `SAFETY_AUDIT_WARNING`, `warning` 같은 내부 raw 값은 JSON export와 data attribute에 그대로 유지하고, 화면 표시만 `안전 점검`, `안전 점검 경고`, `경고`처럼 변환한다.

[실전-35] Archive Viewer는 기본 실행 시 `ARCHIVE_VIEWER.csv`와 `ARCHIVE_VIEWER_SUMMARY.md`도 함께 생성한다. CSV는 리포트 유형/status/severity/수정 시간/크기/상대 경로 등 metadata만 포함하고, Markdown summary는 운영 요약·최근 상태·유형별 최신 리포트·주의 필요 항목을 회의/점검용으로 정리한다. HTML에는 `@media print`가 포함되어 인쇄 시 필터 UI를 숨기고 summary/table 중심으로 출력한다.

[실전-36] Archive Viewer는 `ARCHIVE_VIEWER_PRESETS.json` 정적 파일과 HTML inline JS 기반 Saved Filter Presets를 제공한다. 기본 프리셋은 주의 필요 항목, 최신 리포트만, 안전 점검만, 리스크/정합성, 실거래 감사이며 운영자가 자주 보는 조합을 빠르게 적용하기 위한 기능이다. localStorage, 외부 서버, 네트워크 fetch, DB 조회는 사용하지 않는다.

[실전-37] Archive Trend Analytics는 Archive Viewer entries metadata만으로 경고/차단 추세를 계산한다. `by_day`, `by_report_type`, `by_severity`, `by_status`, 최근 경고/차단 추세, 유형별 주의 항목, 반복 문제 유형을 HTML/Markdown/JSON에 표시한다. `--trend-days` 기본값은 7이며, 14 등으로 늘려 반복 문제 유형과 추세 window를 조정할 수 있다. 이 통계는 과거 로컬 리포트 흐름 해석용이며 실계좌 상태 재조회가 아니다.

[실전-50] Archive Viewer Freshness Source는 각 리포트의 생성 시각과 기준 소스를 표시한다. JSON `generated_at`이 가장 신뢰도가 높고, Markdown 헤더(`- 생성 시각:`)는 그다음, 없으면 파일 수정시간 fallback이다. mtime fallback 비중이 높으면 구버전/복사 파일 가능성이 있으니 Daily AI workflow는 JSON `generated_at`을 우선 확인한다. HTML 테이블·CSV·JSON entries·`ARCHIVE_VIEWER_SUMMARY.md`·`report-index`·`open-dashboard`에 freshness summary가 포함된다.

[실전-37] AI Live Trade Recommendation은 `ai-live-recommend`로 최신 signals/market/macro/실계좌 snapshot/운영 리포트 metadata를 읽어 BUY/SELL/HOLD/REDUCE/INCREASE/SKIP 후보를 만든다. 산출물은 `AI_LIVE_TRADE_RECOMMENDATION.md`, `ai_live_trade_recommendation_*.json`, `live_order_plan_ai_*.json`이다. AI는 주문 후보와 승인 대기 주문안만 만들며, 최종 실주문은 운영자가 기존 `live-approve --execute` 절차에서 직접 승인해야 한다.

[실전-38] AI Recommendation Validation은 `validate-ai-recommendation`으로 `ai-live-recommend` v1 정책을 로컬 DB의 `market_prices`/`signals`/macro metadata 기준으로 검증한다. 가상 포트폴리오는 메모리에서만 갱신하고 `paper_*` 또는 실계좌 테이블은 수정하지 않는다. 기본은 BUY/INCREASE만 반영하고, `--include-sell-reduce`일 때만 SELL/REDUCE도 가상 반영한다.

[실전-39] Advanced Validation Metrics는 `validate-ai-recommendation` 결과에 Sharpe, volatility, profit factor, expectancy, max drawdown 구간, 연속 손실, exposure, turnover, average holding days, action/symbol별 PnL을 추가한다. 기본 benchmark는 동일 기간 대상 종목 동일비중 buy-and-hold이며, `--benchmark`는 기본 ON, `--risk-free-rate` 기본값은 0.0이다.

[실전-40] Cost / Slippage Validation은 `validate-ai-recommendation`에 기본 비용 가정(수수료율 0.1%, 세금 0%, 슬리피지 5bps, 최소 주문금액 10,000 KRW)을 반영한다. 필요하면 `--commission-rate`, `--tax-rate`, `--slippage-bps`, `--min-order-value`, `--max-order-value`, `--currency`로 조정하고, `--no-costs`로 비용 모델을 끌 수 있다. 리포트는 비용 차감 전/후 수익률과 총 비용, 비용으로 스킵된 거래 수를 표시한다. 이 기능도 로컬 DB read-only + in-memory validation이며 실주문, KIS, live-approve, paper_* 운영 테이블 수정과 무관하다.

[실전-41] Portfolio Risk Validation은 `validate-ai-recommendation` 결과에 단일 종목 비중, 섹터 비중, 고상관 종목쌍, 집중도/분산 점수를 추가한다. 섹터 정보는 `--sector-map config/sector_map.json` 같은 로컬 JSON만 사용하고, 파일이 없으면 `UNKNOWN`으로 처리한다. `--max-symbol-weight`, `--max-sector-weight`, `--correlation-threshold`, `--correlation-lookback-days`로 기준을 조정할 수 있으며, `blocked` 상태도 검증 경고일 뿐 주문 차단이나 실행 기능이 아니다.

[실전-42] Liquidity Constraint Validation은 `market_prices.volume`을 사용해 평균 거래량/거래대금 기준의 주문 가능성을 검증한다. `--liquidity-limit-pct`를 지정하면 최근 평균 거래량의 일부까지만 주문 수량을 허용하고, `--min-daily-volume`, `--min-daily-value`, `--volume-lookback-days`로 저유동성 종목을 축소/스킵할 수 있다. 기본값은 제한 없음이라 기존 검증 동작을 유지한다. 외부 유동성 API, yfinance info, KIS, live-approve, 실주문은 호출하지 않는다.

[실전-43] FX / Currency-Aware Validation은 `validate-ai-recommendation`에서 종목별 통화와 날짜별 환율을 로컬 JSON 또는 `--fallback-fx`로 지정해 기준 통화 평가액을 계산한다. `--base-currency KRW`, `--default-symbol-currency USD`, `--fx-rates config/fx_rates.json`, `--symbol-currency-map config/symbol_currency_map.json`, `--fallback-fx USD=1350,KRW=1` 형식을 지원한다. 환율 파일이 없으면 기존 단일통화 방식처럼 동작하며, 외부 FX API나 네트워크 조회는 하지 않는다.

체크리스트 생성:

```bash
python main.py generate-checklists --output-dir outputs/checklists
```

안전 감사:

```bash
python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db
python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db --strict
python main.py report-index --output-dir outputs --archive-dir outputs/archive
python main.py html-dashboard --output-dir outputs
python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive
python main.py open-dashboard --output-dir outputs
```

생성 파일:

```text
outputs/checklists/DAILY_CHECKLIST.md
outputs/checklists/PRE_MARKET_CHECKLIST.md
outputs/checklists/POST_TRADE_CHECKLIST.md
outputs/checklists/WEEKLY_MAINTENANCE_CHECKLIST.md
outputs/checklists/SAFETY_RULES.md
outputs/SAFETY_AUDIT.md
outputs/safety_audit_YYYYMMDD_HHMMSS.json
outputs/ARCHIVE_VIEWER.html
outputs/ARCHIVE_VIEWER.csv
outputs/ARCHIVE_VIEWER_SUMMARY.md
outputs/ARCHIVE_VIEWER_PRESETS.json
outputs/archive_viewer_YYYYMMDD_HHMMSS.json
```

주간 운영 예:

```bash
python main.py weekly-maintenance --output-dir outputs --archive-dir outputs/archive
python main.py notify-alerts --dry-run --include-maintenance --output-dir outputs
python main.py weekly-report-bundle --output-dir outputs
python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive
python main.py open-dashboard --output-dir outputs --open-index
python main.py open-dashboard --output-dir outputs --open-archive
```

한 번에 묶기:

```bash
python main.py weekly-report-bundle --output-dir outputs --zip
```

스케줄러 대신 수동 체크리스트를 먼저 갱신하려면:

```bash
python main.py generate-checklists --output-dir outputs/checklists
python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db
```

## 5. 위험 경고

- `KIS_ENV=live`는 실계좌 호스트를 의미한다.
- `--execute`는 실제 주문 가능 경로를 요청한다.
- 실주문에는 `--approved`, `--allow-live-env`, `--final-confirm I_UNDERSTAND_REAL_ORDER`, session guard, runbook guard, duplicate order guard가 필요하다.
- `risk-check`는 경고만 생성한다. SELL, 시장가, 자동 청산은 없다.
- `post-trade-runbook`의 risk threshold 옵션은 경고 기준만 조정한다. SELL, 시장가, 자동 청산으로 이어지지 않는다.
- `sell-plan`은 운영자 검토용 계획서만 생성한다. SELL 주문 실행 기능이 아니다.
- `notify-alerts`는 기본 dry-run이며, `--send` 없이는 네트워크 호출을 하지 않는다. `--send`를 사용해도 alert-only이며 주문 실행은 없다.
- `notify-alerts --include-maintenance`는 `weekly_maintenance_*.json`과 `report_health_*.json`도 알림 source로 포함한다. 기본 dry-run이며 `--send` 없이는 전송하지 않는다.
- `telegram-approval-request --send`와 `telegram-approval-listen`은 Telegram Bot API 네트워크를 사용할 수 있다. 승인 callback이 유효해도 audit과 `execute-last-approved` 안내만 생성하며, 실제 실행은 운영자가 터미널에서 단축 실행 명령을 직접 입력해야 한다.
- `daily-ops-summary`는 당일 운영 산출물을 한 파일로 묶는 조회/요약 전용 리포트다. 실주문이나 SELL 자동화가 아니다.
- `html-dashboard`는 로컬 `outputs/` JSON을 읽어 `outputs/OPS_DASHBOARD.html` 정적 파일만 생성한다. 웹서버, 네트워크 호출, 실주문, SELL 자동화가 아니다.
- `post-trade-runbook --with-summary`의 추가 리포트 체인은 파일 생성 전용이다. 일부 리포트 생성 실패는 warning으로 기록되며 자동 주문이나 SELL로 이어지지 않는다.
- `cleanup-reports`는 기본 dry-run이다. `--apply`를 붙이기 전 audit의 candidates를 확인하고, 운영 리포트 보존이 필요하면 `--archive`를 우선 사용한다.
- `report-index`는 outputs/archive 리포트 목록을 정적 HTML/Markdown/JSON으로 요약한다. 웹서버나 네트워크 호출 없이 파일 링크만 만든다.
- `report-index`, `open-dashboard`, `archive-viewer`, `safety-audit`는 `AI_DAILY_*` 산출물을 읽기 전용으로 표시한다. 누락된 daily workflow 단계는 다음 실행 명령으로 안내되며 주문 실행 트리거가 아니다.
- `ops-dry-run`은 조회/점검/리포트 생성 전용이다. `--network` 없이는 KIS 네트워크 조회를 하지 않고, `--network`를 사용해도 OAuth·잔고조회·reconcile 조회만 포함한다.
- `open-dashboard`는 로컬 파일 viewer다. 기본은 경로 출력만 하며, `--open` 계열 옵션이 있을 때만 `output_dir` 내부 HTML 파일을 연다.
- `report-health-check`는 진단 전용이다. 경고와 next actions를 제안하지만 정리/삭제/네트워크/알림/주문은 수행하지 않는다.
- `weekly-maintenance`는 dry-run 점검/리포트 생성 전용이다. cleanup 후보를 찾더라도 삭제나 archive 이동을 하지 않는다.
- `weekly-report-bundle`은 리포트 생성/복사/번들링 전용이다. ZIP은 `--zip`일 때만 만들며, 민감 파일은 포함하지 않는다.
- `generate-checklists`는 Markdown 체크리스트 생성 전용이다. cron, launchd, plist, 자동 실행, 네트워크 호출, 실주문, SELL 자동화, KIS POST를 수행하지 않는다.
- `safety-audit`는 로컬 읽기 전용 감사다. 네트워크 호출, KIS POST, `live-approve`, `--execute`, SELL 자동화, 시장가 주문 기능, cleanup apply, archive 이동, 파일 삭제를 수행하지 않는다.
- `report-index`, `html-dashboard`, `open-dashboard`의 Safety Audit 연동은 로컬 파일 링크 표시 전용이다. `SAFETY_AUDIT_OK`도 실주문 허가가 아니며 WARNING/BLOCKED는 수동 점검 필요 신호다.
- `archive-viewer`는 read-only 로컬 리포트 탐색기다. 실주문 허가, 자동 복구, 파일 정리, archive 이동 기능이 아니다.
- Archive Viewer의 `Needs Attention`은 수동 점검 우선순위다. 자동 주문 중단/복구/cleanup 실행 트리거가 아니다.
- Archive Viewer 한국어 label은 표시 전용이다. 내부 JSON/status raw 값 변경, 실주문 허가, 자동 복구, 파일 정리 기능이 아니다.
- Archive Viewer CSV/Markdown summary는 metadata export 전용이다. 리포트 원문 전체, DB 내용, token/app secret/account 원문을 포함하지 않는다.
- Archive Viewer preset은 로컬 정적 JSON과 inline JS UI 기능이다. 외부 네트워크/서버/localStorage/DB 조회 없이 현재 HTML 테이블 필터만 조정한다.
- Archive Trend Analytics는 metadata/status/count 통계다. 경고가 줄고 있는지, 반복 차단 유형이 있는지 보는 참고 자료이며 자동 복구나 실주문 판단 엔진이 아니다.
- AI Live Trade Recommendation은 자동 판단 보조와 주문안 생성 기능이다. `live-approve` 자동 호출, `--execute` 자동 호출, KIS `order-cash` POST, 시장가, SELL 자동주문, final-confirm 자동 주입은 수행하지 않는다. SELL/REDUCE 후보는 기본 주문안에서 제외된다.
- AI Recommendation Validation은 검증 전용이다. 결과는 수익 보장이 아니며 실거래 적용 전 기간/종목/옵션별로 충분히 비교해야 한다.
- Sharpe, profit factor, benchmark 초과수익이 좋아도 미래 수익을 보장하지 않는다. drawdown과 연속 손실이 운영자가 감당 가능한 범위인지 별도로 확인한다.
- partial fill, reconcile mismatch, stale snapshot, duplicate order 경고가 있으면 실주문을 멈추고 증권사 앱과 대조한다.

## 6. 절대 하지 말 것

- shell alias로 자동 실주문 실행
- cron 또는 launchd로 `live-approve --execute` 자동 실행
- `run_live_precheck_macos.sh`에 `live-approve` 또는 `--execute` 추가
- API key, app secret, 계좌번호를 코드나 문서에 하드코딩
- `.env` 커밋
- guard 차단을 우회하는 옵션이나 별도 주문 스크립트 추가
- `notify-alerts`를 자동 주문 트리거로 연결
- `notify-alerts --include-maintenance`를 cleanup apply, archive 이동, 실주문, SELL 자동화 트리거로 연결
- `html-dashboard`를 웹서버/실시간 네트워크 조회/주문 트리거로 연결
- `cleanup-reports`로 `.env`, DB, 소스 코드, AI_CONTEXT, scripts를 정리 대상으로 확장
- `report-index`를 웹서버나 자동 주문/알림 트리거로 연결
- `ops-dry-run`에 `live-approve`, 실주문, 자동 SELL, KIS 주문 POST를 연결
- `open-dashboard`에서 웹서버 실행, 외부 URL 열기, `output_dir` 밖 파일 열기, 주문 관련 명령 호출
- `report-health-check`에서 cleanup 적용, AppleDouble 삭제, KIS 네트워크 조회, 알림 전송, 실주문 명령 호출
- `weekly-maintenance`에 `--apply`, `--archive`, `--network`, `--send`, 실주문, SELL 자동화, KIS POST 연결
- `weekly-report-bundle`에 삭제, archive 이동, 네트워크 호출, 알림 실제 전송, 실주문, SELL 자동화, KIS POST 연결
- `generate-checklists`를 cron/launchd 등록 도구로 확장하거나 plist/crontab 파일 생성에 연결
- `safety-audit`에서 KIS 조회, KIS POST, `live-approve`, cleanup apply, archive 이동, 파일 삭제, scheduler 파일 생성 수행
- `archive-viewer`에서 네트워크 호출, DB 내용 읽기, cleanup apply, archive 이동, 파일 삭제, source code 분석, 주문 관련 명령 호출
- Archive Viewer CSV/Markdown summary에 리포트 원문 전체, `.env`, token, app secret, account 원문 포함
- Archive Viewer preset 적용을 위해 외부/로컬 파일을 `fetch`로 다시 호출하거나 서버 기능 추가
- Trend Analytics를 위해 DB 내용, KIS raw 원문, 리포트 원문 전체를 읽거나 외부 차트 라이브러리/CDN 사용
- `ai-live-recommend` 결과를 cron/launchd/plist/alias/shell script로 자동 승인하거나 `live-approve --execute`와 연결해 무인 실행
- `validate-ai-recommendation`에서 KIS 호출, live-approve 호출, --execute 호출, 실계좌 주문, `paper_*` 운영 테이블 수정 추가
- `live-approve --execute` 또는 `--final-confirm I_UNDERSTAND_REAL_ORDER`를 cron, launchd, alias, shell script로 자동 주입
- Telegram/Discord 토큰이나 webhook URL을 코드·문서·커밋에 노출
