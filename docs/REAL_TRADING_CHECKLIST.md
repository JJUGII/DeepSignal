# REAL TRADING CHECKLIST

이 체크리스트는 수동 운영 절차다. `live-approve --execute`를 alias, cron, launchd, shell script로 자동화하지 않는다.

## Generated checklist templates

수동 운영 템플릿은 아래 명령으로 갱신할 수 있다. 이 명령은 Markdown 파일만 생성하며 cron, launchd, plist, 자동 실행, 네트워크 호출, 실주문, SELL 자동화, KIS POST를 수행하지 않는다.

```bash
python main.py generate-checklists --output-dir outputs/checklists
```

생성되는 파일:

- `outputs/checklists/DAILY_CHECKLIST.md`
- `outputs/checklists/PRE_MARKET_CHECKLIST.md`
- `outputs/checklists/POST_TRADE_CHECKLIST.md`
- `outputs/checklists/WEEKLY_MAINTENANCE_CHECKLIST.md`
- `outputs/checklists/SAFETY_RULES.md`

`SAFETY_RULES.md`에는 `live-approve --execute` 자동화 금지, `--final-confirm` 자동 주입 금지, `.env` 커밋 금지, SELL 자동화 금지, 시장가 금지, KIS POST 직접 호출 금지가 포함되어야 한다. Telegram approval workflow도 final confirmation을 대체하지 않으며 승인 기록과 수동 실행 안내만 제공한다.

## Safety audit

실주문 전 또는 주간 점검 전 아래 안전 감사를 먼저 확인한다. 이 명령은 로컬 JSON/Markdown만 생성하며 네트워크 호출, KIS POST, `live-approve`, `--execute`, SELL 자동화, cleanup apply, archive 이동, 파일 삭제를 수행하지 않는다.

```bash
python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db
python main.py report-index --output-dir outputs --archive-dir outputs/archive
python main.py html-dashboard --output-dir outputs
python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive
python main.py open-dashboard --output-dir outputs
```

- `SAFETY_AUDIT_OK`: 로컬 감사 경고가 없다는 뜻이며 실주문 허가가 아님
- `SAFETY_AUDIT_WARNING`: 경고를 운영자가 확인한 뒤 진행 여부 판단
- `SAFETY_AUDIT_BLOCKED`: 실주문 중단 후 원인 해소

`--strict`를 붙이면 checklist 누락 같은 WARNING도 BLOCKED로 승격한다.

`report-index`, `html-dashboard`, `open-dashboard`에서 `SAFETY_AUDIT.md`와 최신 `safety_audit_*.json` 링크를 확인할 수 있다. 링크 표시는 로컬 파일 전용이며 네트워크나 주문을 수행하지 않는다.

`archive-viewer`는 과거 운영 리포트 탐색용 로컬 HTML이다. report type/status/severity/text/date range 필터와 Only warnings/errors, Latest only 토글로 위험 리포트를 좁혀 볼 수 있다. `Needs Attention`은 warning 이상, blocked/error/failed 계열, reconcile mismatch, partial fill open, stale snapshot, safety audit blocked를 수동 점검 우선순위로 모은다. 실주문 허가, 자동 복구, cleanup apply, archive 이동, 파일 삭제 기능이 아니다. 필요하면 `python main.py open-dashboard --output-dir outputs --open-archive`로 `ARCHIVE_VIEWER.html`만 연다.

Archive Viewer의 화면 label은 한국어로 표시되지만 내부 JSON/status raw 값은 변경하지 않는다. 예: `SAFETY_AUDIT_WARNING`은 화면에서 `안전 점검 경고`로 보일 수 있으나, 원본 status는 그대로 보존된다.

Archive Viewer는 `ARCHIVE_VIEWER.csv`와 `ARCHIVE_VIEWER_SUMMARY.md`를 함께 생성해 운영 점검/회의/백업에 사용할 수 있다. 두 파일은 metadata 기반 요약이며 리포트 원문 전체, DB 내용, token, app secret, account 원문을 포함하지 않는다. 브라우저 인쇄/저장은 `ARCHIVE_VIEWER.html`의 print mode를 사용한다.

Saved Filter Presets는 `ARCHIVE_VIEWER_PRESETS.json` 정적 파일과 HTML inline JS로 동작한다. 기본 프리셋(주의 필요 항목, 최신 리포트만, 안전 점검만, 리스크/정합성, 실거래 감사)은 자주 보는 리포트 조합을 빠르게 확인하기 위한 보기 기능이며 네트워크, 서버, DB 조회, 자동 복구를 수행하지 않는다.

Archive Trend Analytics는 `archive-viewer` entries metadata만으로 최근 경고/차단 흐름, 반복 문제 유형, 유형별 주의 항목을 보여준다. `--trend-days` 기본값은 7이며 운영 검토 기간에 따라 14 등으로 조정할 수 있다. 이 기능은 과거 로컬 리포트 통계이며 실계좌 상태 재조회, 실주문, 자동 복구 기능이 아니다.

Archive Viewer Freshness Source([실전-50])는 리포트별 **생성 시각**·**기준 소스**를 표시한다. `JSON generated_at`이 가장 신뢰도가 높고, `파일 수정시간 fallback`은 구버전/복사 파일일 수 있다. Daily AI workflow 운영 시 plan/report JSON의 `generated_at`을 archive viewer에서 확인한 뒤 `execute-last-approved`를 검토한다.

AI Live Trade Recommendation은 `python main.py ai-live-recommend --broker kis --output-dir outputs`로 AI 추천 리포트와 `live_order_plan_ai_*.json` 승인 대기 주문안을 생성한다. `--network`는 KIS 잔고/포지션 조회에만 사용하며 주문 POST가 아니다. SELL/REDUCE 후보는 리포트에서 확인하되 기본 주문안에는 포함하지 않는다. 주문안 실행은 운영자가 직접 `live-approve --execute`와 `--final-confirm I_UNDERSTAND_REAL_ORDER`를 입력하는 수동 절차에서만 가능하다.

Telegram Approval Trading Workflow는 `telegram-approval-request`로 승인 요청 JSON/Markdown을 만들고, `--send` 사용 시 Telegram 버튼을 보낸다. `telegram-approval-listen`은 유효한 승인 버튼, 허용 chat id, one-time token, 만료, plan hash, 주문 한도, today halt를 검증해 승인/중단 audit을 남기고 `execute-last-approved`를 안내한다. listener는 `live-approve`, `--execute`, `--allow-live-env`, KIS POST를 자동 호출하지 않는다.

Simplified Approved Execution은 Telegram 승인 후 운영자가 터미널에서 `python main.py execute-last-approved --output-dir outputs` 또는 `python main.py execute-approved --request-id REQUEST_ID --output-dir outputs`를 직접 실행하는 단계다. 이 명령은 승인 없음, 승인 거절, 만료, today halt, plan hash mismatch, plan 누락, 중복 실행, 주문 한도 초과를 차단하고 기존 live execution guard를 재사용한다.

Daily AI Trading Workflow는 장 시작 전 `daily-ai-trade-plan`, Telegram 승인 요청, 승인 후 `execute-last-approved`, 장 종료 후 `daily-ai-trade-report`, 중간 상태 확인 `daily-ai-status` 순서로 운영한다. `daily-ai-trade-plan`, `telegram-approval-request`, `telegram-approval-listen`, `daily-ai-trade-report`, `daily-ai-status`는 실주문을 실행하지 않는다.

Daily AI Dashboard Integration은 `report-index`, `open-dashboard`, `archive-viewer`, `safety-audit`에서 `AI_DAILY_*` 산출물과 workflow 누락 단계를 표시한다. 이 통합은 outputs 파일 읽기와 로컬 리포트 생성 전용이며 KIS/Telegram/live-approve/execute 호출이 아니다.

Daily AI Workflow Timestamp Normalization([실전-49])은 Daily AI JSON에 `generated_at`/`generated_date`/`timezone`(Asia/Seoul)을 일관 기록한다. freshness는 `generated_at` 우선이며 없을 때만 mtime fallback을 사용한다.

Daily AI Workflow Freshness Validation([실전-48])은 `daily-ai-status`와 `safety-audit`에서 plan/latest order plan/approval/execution/report가 **오늘 생성**되었는지 확인한다. 전일 plan으로 `execute-last-approved`를 실행하지 않는다. stale plan은 실행 차단되며 audit에 한국어 사유가 기록된다. 테스트용으로 `--freshness-date YYYY-MM-DD`를 지정할 수 있으며 기본은 Asia/Seoul 오늘이다.

AI Recommendation Validation은 `python main.py validate-ai-recommendation --output-dir outputs`로 AI 추천 정책을 실계좌와 무관하게 검증한다. 이 명령은 로컬 DB를 읽고 in-memory portfolio로만 성과를 계산하며 `paper_*`, 실계좌 테이블, KIS, live-approve를 수정/호출하지 않는다. 검증 결과는 수익 보장이 아니며 실거래 적용 전 참고 자료다.

[실전-39]부터 검증 리포트는 Sharpe, 변동성, profit factor, expectancy, 최대 낙폭 구간, 연속 손실, 동일비중 buy-and-hold benchmark 대비 초과수익도 표시한다. 지표가 좋아도 미래 수익 보장은 아니며, drawdown과 연속 손실이 운영 가능한 범위인지 확인해야 한다.

[실전-40]부터 검증 리포트는 기본 비용 가정(수수료율 0.1%, 세금 0%, 슬리피지 5bps, 최소 주문금액 10,000 KRW)을 반영한 비용 차감 전/후 성과도 표시한다. 실거래 후보 검토 전 `비용 반영 성과`와 `비용으로 스킵된 거래` 섹션을 확인하고, 소액 반복매매가 비용으로 사라지는지 확인한다. `--no-costs` 결과와 비용 적용 결과를 나란히 비교하되, 둘 다 검증용이며 주문 실행 기능이 아니다.

[실전-41]부터 검증 리포트는 `포트폴리오 리스크 검증` 섹션과 `AI_RECOMMENDATION_PORTFOLIO_RISK.csv`를 생성한다. `--sector-map config/sector_map.json` 로컬 파일을 사용하면 섹터 집중도를 확인할 수 있고, 파일이 없으면 섹터는 `UNKNOWN`으로 표시된다. 집중도 점수가 높거나 고상관 종목쌍이 많으면 실거래 전 종목군과 비중을 보수적으로 재검토한다.

[실전-42]부터 검증 리포트는 `--liquidity-limit-pct`, `--min-daily-volume`, `--min-daily-value`, `--volume-lookback-days` 옵션으로 거래량 기반 유동성 제한을 반영할 수 있다. 기본은 제한 없음이며, 옵션을 지정하면 평균 거래량/거래대금 기준으로 주문 수량이 축소되거나 스킵된다. `유동성 제한 검증` 섹션에서 축소/스킵/volume unavailable 경고를 확인한다.

[실전-43]부터 검증 리포트는 `--base-currency`, `--default-symbol-currency`, `--fx-rates`, `--symbol-currency-map`, `--fallback-fx` 옵션으로 통화/환율 영향을 반영할 수 있다. 미국 주식과 국내 주식이 섞이면 `통화 / 환율 검증` 섹션에서 통화별 현금, 포지션 평가액, 외화 노출 비중, FX warning을 확인한다. 외부 환율 조회는 하지 않으며 로컬 JSON 또는 fallback 값만 사용한다.

## Before market open

- [ ] `source .venv/bin/activate`
- [ ] `python --version`이 `.venv` Python 3.11 이상인지 확인
- [ ] `.env`의 `KIS_ENV`가 의도한 값인지 확인
- [ ] AI 추천을 사용할 경우 `AI_LIVE_TRADE_RECOMMENDATION.md`의 차단 사유와 `live_order_plan_ai_*.json` 주문 포함 여부를 직접 확인
- [ ] 일일 AI 운영을 사용할 경우 `python main.py daily-ai-trade-plan --broker kis --network --output-dir outputs`
- [ ] `outputs/live_order_plan_ai_latest.json`와 `AI_DAILY_TRADE_PLAN.md` 확인
- [ ] AI 추천을 실거래 후보로 쓰기 전 `AI_RECOMMENDATION_VALIDATION.md`의 수익률, 최대 낙폭, Sharpe, profit factor, benchmark 비교, 비용 차감 전/후 성과, 포트폴리오 리스크, 유동성 제한, 통화/환율 노출, action별 성과를 확인
- [ ] `python main.py trading-session-check`
- [ ] `python main.py kis-check`
- [ ] `python main.py live-sync-account --broker kis --network`
- [ ] `python main.py reconcile-live-account --broker kis --network`
- [ ] `python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db`
- [ ] `python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive`
- [ ] `python main.py report-index --output-dir outputs --archive-dir outputs/archive`
- [ ] `python main.py open-dashboard --output-dir outputs`
- [ ] 증권사 앱의 계좌/보유/현금과 DeepSignal 리포트를 대조

## Before execute

- [ ] 주문안 JSON 경로 확인
- [ ] `pre-trade-runbook --network` 실행
- [ ] `PRE_TRADE_READY` 확인
- [ ] symbol 확인
- [ ] qty 확인
- [ ] limit price 확인
- [ ] `--allow-symbol` 확인
- [ ] `--approved` 포함 여부 확인
- [ ] `--require-pre-trade-runbook` 포함 여부 확인
- [ ] `--allow-live-env`가 의도된 실계좌 실행인지 확인
- [ ] 일반 CLI는 `--final-confirm I_UNDERSTAND_REAL_ORDER`를 직접 입력할지 최종 확인
- [ ] Telegram 승인 경로는 `TELEGRAM_APPROVAL_REQUEST.md`, chat id, token expiry, 주문 한도, `today halt` 상태, 승인 audit의 `execute-last-approved` 안내 명령을 확인
- [ ] 승인 요청은 `python main.py telegram-approval-request --plan outputs/live_order_plan_ai_latest.json --send --output-dir outputs`
- [ ] duplicate order, partial fill, reconcile mismatch, stale snapshot 경고가 없는지 확인

## Execute

- [ ] `live-approve --execute`는 한 번만 수동 실행
- [ ] Telegram 승인 경로를 쓰는 경우 `telegram-approval-listen`은 승인/중단 audit 생성만 수행했는지 확인
- [ ] 단축 실행 경로를 쓰는 경우 `execute-last-approved` 또는 `execute-approved --request-id ...`는 터미널에서 운영자가 직접 한 번만 실행
- [ ] 실행 전후 `python main.py daily-ai-status --output-dir outputs`로 다음 단계와 freshness(오늘 계획/최신 주문안) 확인
- [ ] `python main.py safety-audit --output-dir outputs`의 `AI 일일 매매 운영 Freshness`에서 stale plan이 없는지 확인
- [ ] `execute-last-approved` 전 `live_order_plan_ai_latest.json`이 오늘 생성된 plan인지 확인 (stale이면 차단됨)
- [ ] 감사 로그 경로 확인
- [ ] 콘솔 출력의 `actual_order_attempted`, status, broker order id 확인
- [ ] 실패 또는 차단 시 자동 재시도하지 않음

## After execute

- [ ] `python main.py post-trade-runbook --broker kis --network --audit outputs/live_approval_audit_YYYYMMDD_HHMMSS.json --output-dir outputs`
- [ ] `python main.py live-order-status --audit outputs/live_approval_audit_YYYYMMDD_HHMMSS.json --network`
- [ ] `python main.py live-fill-summary --audit outputs/live_approval_audit_YYYYMMDD_HHMMSS.json`
- [ ] `python main.py risk-check --broker kis --output-dir outputs`
- [ ] 증권사 앱에서 접수/체결/잔량 확인
- [ ] `python main.py daily-ai-trade-report --broker kis --network --output-dir outputs`
- [ ] `AI_DAILY_TRADE_REPORT.md`와 `AI_DAILY_STATUS.md` 확인
- [ ] audit log, runbook JSON/Markdown, risk report를 로컬 백업

## Stop conditions

- [ ] Trading session `CLOSED`
- [ ] `PRE_TRADE_READY` 아님
- [ ] plan/symbol/qty/limit 불일치
- [ ] duplicate order blocked
- [ ] partial fill open
- [ ] reconcile mismatch
- [ ] stale account snapshot
- [ ] `KIS_ENV`가 의도와 다름
- [ ] final confirmation 문구를 확신하지 못함

위 조건 중 하나라도 해당하면 실주문을 중단하고 수동 점검한다.
