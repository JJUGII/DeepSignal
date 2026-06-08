# DeepSignal — 알려진 이슈·미결정 사항

## 인프라·문서

- **AI_CONTEXT 폴더가 과거에 없었음**  
  - 본 세트 생성으로 기준 문서는 마련되었으나, 코드와의 동기화는 수동 갱신이 필요하다.  
- **macOS 포팅 범위**  
  - macOS 전용 requirements와 shell 스크립트는 추가되었지만, Windows 배치 파일은 유지·수정 대상이 아니다.
  - `scripts/run_live_precheck_macos.sh`는 조회/점검만 수행한다. 실주문은 자동 스크립트가 아니라 기존 `live-approve` guard 기반 CLI에서만 수동 수행한다.
  - `kis-check --network`, `live-sync-account --network`, `reconcile-live-account --network`는 `.env`의 KIS 설정과 네트워크가 없으면 실패할 수 있다.
  - Homebrew Python 빌드에 `_tkinter`가 없으면 `dashboard` 명령은 실행할 수 없다. 일반 CLI 초기화는 Tk 없이도 통과하도록 처리했다.
  - macOS 시스템 `python3`는 버전·의존성이 부족할 수 있으므로 운영은 `.venv` 활성화 또는 `./.venv/bin/python` 사용을 전제로 한다.
  - `.env`의 `KIS_ENV=live`는 실계좌 호스트를 의미한다. 조회/실주문 전 운영자가 직접 의도한 live 상태인지 확인해야 한다.
  - `docs/MACOS_OPERATION_GUIDE.md`, `docs/MACOS_TROUBLESHOOTING.md`, `docs/REAL_TRADING_CHECKLIST.md`를 기준 운영 문서로 사용한다.

## 데이터 소스·API

- **yfinance**  
  - 비공식·무료 공개 데이터에 가깝고, **속도 제한·필드 변경·안정성 리스크**가 있을 수 있다. 운영 전략은 쿼터·백오프·캐시를 검토한다.  
  - **수정주가·결측·재수집**에 따라 동일 기간이라도 OHLCV가 달라질 수 있고, 이에 따라 RSI/EMA도 달라질 수 있다.  
- **기술지표·데이터 기간**  
  - 수집 기간이 짧으면(예: 기본 `1mo`) **EMA26·trend_score** 등이 비거나 불안정할 수 있다. 지표 해석 전 `MARKET_PERIOD` 확대·봉 수 확인을 권장한다.  
- **한국 주식·국내 브로커 데이터**  
  - 티커 규칙·세션·통화·거래소 캘린더가 달라 **별도 어댑터·인증 API**가 필요하다. 현재 `collect-market`은 미국 티커 위주 yfinance 경로다.  
- **RSS 소스별 품질·제공 필드 차이**  
  - 동일 파이프라인이라도 `title`/`summary`/`published_at` 유무가 달라질 수 있다. 파싱·표시 로직에서 None 처리 필요.  
- **뉴스 원문 전문 수집**  
  - 저작권·이용약관 이슈가 있어 현 단계에서는 **제목·요약(피드 제공)·링크** 중심으로 제한한다. 전문 스크래핑은 별도 검토.  
- **실제 데이터 소스 / 공식 API는 아직 미확정**  
  - 뉴스(RSS vs 스크래핑), 거시지표(FRED·한은 등), 시세(야후·한투 등)별로 허용 범위·쿼터를 확정해야 한다.  
- **Reuters**  
  - 라이선스·계약·비용 이슈 가능성이 높아 **추후 검토**로 두는 것이 안전하다.  
- **X(Twitter)**  
  - API 정책·비용 변동이 크므로 **추후 검토**로 둔다.  

## Live Order Plan ([실전-1])

- **`live-plan`은 주문 계획 파일만 생성**하며, 브로커 HTTP·주문 API·증권사 인증을 **호출하지 않는다.**  
- **매도·손절 자동화 없음** (BUY 후보만). 승인·실행은 사용자/후속 단계.  
- **시세**는 DB `fetch_latest_market_price`(기본 yfinance 일봉 최신 종가)에 의존하며 **지연·결측** 가능.  
- **통화·단위**: 기본 표시 통화는 **USD**(미국 티커 중심); 실제 계좌 통화와 다를 수 있다.  
- **정수 주만** 지원(소수 주·원화 단위 규칙 미반영).  
- **`outputs/`** 는 로컬 산출물이므로 버전 관리에서 제외하는 것을 권장한다.

## Live Approve ([실전-2]~[실전-5])

- **`live-approve --broker dry-run`(기본)**: **`DryRunBroker`** 만 사용하며 증권사 **주문** HTTP를 호출하지 않는다. **`--no-dry-run` 거부(종료 1)**.
- **`live-approve --broker kis` (비 `--execute`)**: **`KISBroker`(safe_mode)** 로 **`KIS_SAFE_MODE_BLOCKED`** 만 사용한다. **`order-cash` POST 없음**.
- **`live-approve --execute` ([실전-4])**: **`KIS_ENV=live`**, **`--allow-live-env`**, **`--final-confirm I_UNDERSTAND_REAL_ORDER`**, **`--approved`**, **`--broker kis`**, 계획 **`PENDING_APPROVAL`**, **`approval_required`**, 기본 **BUY·LIMIT·건수·금액 한도**, 선택 **`--allow-symbol`** 화이트리스트 등을 만족할 때만 **`order-cash` POST**(단발·재시도 없음). **`KIS_ENV=paper`** 는 실주문 차단. **`KIS_ORDER_REJECTED`** 도 감사 로그에 **`results[].raw`** 로 남는다.
- **`live-order-status` ([실전-5])**: **`live_approval_audit_*.json`** 에서 주문번호 후보 추출·**`--network`** 시 **`inquire-daily-ccld`** 로 체결/미체결 조회. **주문 접수(rt_cd=0)** 와 **체결 완료**는 다를 수 있다. 리포트는 **`outputs/live_order_status_*.json`**, **`outputs/LIVE_ORDER_STATUS.md`**.
- 계획 JSON은 **`PENDING_APPROVAL`**, **`approval_required=true`**, **BUY·양수 수량·가격·주문가치**를 요구한다. **KIS 국내 현금 LIMIT** 경로는 **6자리 숫자 종목코드·CANO 8자리·상품코드 2자리**가 필요하며, **`live-plan` 기본(미국 티커)과 불일치할 수 있다.**
- 감사 로그는 **`outputs/live_approval_audit_*.json`** 로컬 파일이며 버전 관리 제외를 권장한다.

## Telegram Approval Trading Workflow ([실전-44])

- `telegram-approval-request --send`와 `telegram-approval-listen`은 Telegram Bot API 네트워크를 사용한다. bot token/chat id는 `.env` 환경변수로만 관리하고 커밋하지 않는다.
- Telegram approval workflow는 승인 요청/승인 기록/수동 실행 명령 안내만 수행한다. `execute_live_order_plan()`, `live-approve`, `--execute`, `--allow-live-env`, KIS POST를 자동 호출하지 않는다.
- Telegram 승인은 final confirmation을 대체하지 않는다. 실주문은 운영자가 터미널에서 `execute-last-approved` / `execute-approved` 또는 일반 `live-approve`를 직접 실행해야 한다.

## Simplified Approved Execution ([실전-45])

- `execute-last-approved` / `execute-approved --request-id`는 운영자가 터미널에서 직접 실행해야 한다. Telegram 버튼이나 listener에서 자동 호출하지 않는다.
- 승인 없음, 거절, 만료, today halt, plan hash mismatch, plan 파일 누락, 이미 완료된 request, 주문 한도 초과는 차단한다.
- 내부 wrapper는 기존 live execution path를 재사용하므로 KIS live env, session, pre-trade runbook, duplicate order guard 차단이 그대로 적용된다.

## Daily AI Trading Workflow ([실전-46])

- `daily-ai-trade-plan`, `daily-ai-trade-report`, `daily-ai-status`는 실주문을 실행하지 않는다. Telegram 승인 후 실주문은 운영자가 `execute-last-approved`를 직접 실행해야 한다.
- `live_order_plan_ai_latest.json`는 최신 AI 주문안 복사본이므로 Telegram 승인 전 `AI_DAILY_TRADE_PLAN.md`와 함께 plan hash/주문 수량/금액을 확인한다.
- 일일 리포트는 최신 로컬 산출물 요약이며, 누락된 live status/fill/account/risk 파일은 `NOT_AVAILABLE`로 표시될 수 있다.

## Daily AI Workflow Dashboard Integration ([실전-47])

- `report-index`, `open-dashboard`, `archive-viewer`, `safety-audit`의 AI daily workflow 표시는 outputs 파일 기반 조회 전용이다.
- `safety-audit`의 daily workflow warning은 누락 단계 안내이며 자동 실행 트리거가 아니다.
- latest 파일이 없거나 오래된 경우 `NOT_AVAILABLE` 또는 warning으로 표시될 수 있으므로 운영자는 `daily-ai-status`로 현재 단계를 재확인한다.
- **[실전-49] Daily AI timestamp**는 신규 생성 산출물부터 `generated_at`/`generated_date`/`timezone`을 기록한다. [실전-48] 이전에 만든 파일은 mtime fallback이 사용될 수 있다.
- **[실전-48] Daily AI freshness**는 로컬 파일 `generated_at`/mtime과 Asia/Seoul 기준 날짜로 추정한다. 외부 시간 API를 호출하지 않으며, JSON에 timezone 없는 timestamp는 policy timezone(기본 Seoul)으로 해석한다. Markdown-only 파일은 mtime에 의존한다.
- stale plan 차단은 `execute-last-approved`/`execute-approved` 경로에만 적용된다. `live-approve` 직접 실행 경로는 별도로 운영자가 plan 날짜를 확인해야 한다.

## KIS Open API ([실전-3]~[실전-5])

- **`kis-check`**: 기본은 **`KIS_*` 환경 변수 검증만(HTTP 없음)**. **`--network`** 일 때만 **OAuth 토큰** HTTPS. **`kis-check`는 주문 API를 호출하지 않는다.**
- **KIS OAuth token cache**: `kis-check --network` 후 이어지는 조회 CLI의 `tokenP` 반복 호출을 줄이기 위해 access token을 `outputs/.kis_token_cache.json`에 캐시한다. 저장 필드는 access token·만료시각·env·app key hash이며 app secret·계좌번호·app key 원문은 저장하지 않는다. 캐시 파일은 버전 관리 제외 대상이다.
- **`KISBroker`**: **`post_kis_order_cash_request`** 로 **`order-cash`** 를 보낼 수 있으나, **`safe_mode=True`** 또는 **`place_order(..., execute=False)`** 이면 POST 하지 않는다. **실주문은 `live-approve --execute` + `LiveExecutionGuard` 통과 후** `safe_mode=False`·`execute=True` 경로만.
- **조회 API ([실전-5])**: **`get_order_status`** → **`inquire-daily-ccld`**, **`get_positions`/`get_cash_balance`** → **`inquire-balance`** (실전/모의 TR 분기). CLI **`live-order-status --network`**, **`live-sync-account --network`** 에서만 기본 **HTTPS 조회**가 나간다(테스트는 mock).
- **`live-sync-account` ([실전-5]~[실전-6])**: **`paper_*`에는 쓰지 않는다.** **`outputs/live_account_snapshot_*.json`** / **`LIVE_ACCOUNT_SNAPSHOT.md`** 에 기록하고, 기본으로 **`real_positions`** / **`real_account_snapshots`** 에도 동일 `snapshot_time`으로 저장한다(**`--no-save-db`** 로 DB 생략).
- **KIS position 파싱 정책**: `inquire-balance.output1`에서 `pdno`가 6자리 숫자인 행만 보유종목 후보로 보고, `hldg_qty > 0`을 실보유 기준으로 사용한다. `hldg_qty`가 없을 때만 `ord_psbl_qty` fallback을 사용하며 `quantity <= 0`은 제외한다.
- **`live-sync-account --debug-raw` / `reconcile-live-account --debug-raw`**: KIS raw 전체가 아니라 `output1`/`output2` 행 수와 키 목록만 출력·저장한다. 계좌번호·토큰·키·원문 값은 저장하지 않는다.
- **`reconcile-live-account` ([실전-6])**: … 불일치 시 **종료 코드 1**·경고 출력; **감사 로그 자동 append는 하지 않음**(JSON/Markdown 리포트만). **`LATEST_RECONCILE_STATE.json`** 이 order guard 입력으로 사용됨([실전-7]). DB 포지션은 최신 `real_account_snapshots.snapshot_time` 기준으로 읽으며, 최신 스냅샷이 빈 포지션이면 과거 `real_positions`를 최신처럼 보지 않는다.
- **`live-order-guard-check` / order guard ([실전-7]~[실전-8])**: **`real_order_history`**·**`real_fill_history`**(open partial)·reconcile state·스냅샷 시각. **`partial_fill_open`**: DB 집계 `remaining_quantity > 0` 시 차단. **`live-approve --execute`** 는 execution guard **이후** order guard 통과 시에만 KIS POST.
- **`real_fill_history` ([실전-8])**: **`live-order-status --network`** 조회 시 저장(dedupe). KIS `output2` 없으면 `output1` 집계 1건으로 synthetic fill 가능 — **다건 체결 상세는 API 응답 필드에 의존**. 미체결·취소 상태는 별도 API 미연동.
- **`live-fill-summary`**: HTTP 없음 — DB에 이미 적재된 체결만 집계. **`live-order-status --network` 선행** 권장.
- **`ops-dashboard` ([실전-14])**: HTTP 없음 — DB 최신 실계좌 스냅샷/포지션/최근 주문과 `outputs/` 최신 reconcile/risk/fill JSON을 읽어 요약만 생성한다. 파일이 없거나 오래되면 최신 운영 상태와 다를 수 있으므로 `live-sync-account` → `reconcile-live-account` → `risk-check` 후 실행을 권장한다. SELL·시장가·자동 반복·자동 취소·KIS 주문 POST는 없다.
- **`sell-plan` ([실전-15])**: HTTP 없음 — 최신 `real_positions`를 바탕으로 운영자 검토용 SELL 계획서만 생성한다. `EXIT`/`REDUCE`는 제안 문구일 뿐 자동 주문이 아니다. SELL API, `order-cash` SELL, `live-approve` SELL 실행, 시장가, 자동매도, 자동 반복, 자동 취소, KIS 주문 POST는 없다.
- **`notify-alerts` ([실전-16])**: 기본 dry-run이며 `--send` 없이는 네트워크 호출을 하지 않는다. `--send` 시에도 Telegram/Discord alert-only 전송만 수행하고 주문 실행·SELL 자동화·KIS POST는 없다. `.env`의 Telegram/Discord 토큰·webhook URL은 실제 값 커밋 금지.
- **`daily-ops-summary` ([실전-17])**: HTTP 없음 — `outputs/`의 오늘자 운영 리포트를 통합한다. 오늘 파일이 없으면 기본 latest fallback을 쓰며 warnings에 남긴다. 오래된 fallback은 실제 운영 상태와 다를 수 있으므로 당일 `live-sync-account`→`reconcile`→`risk-check`→`ops-dashboard`→`sell-plan` 후 실행 권장. 실주문·SELL 자동화·KIS POST 없음.
- **`html-dashboard` ([실전-18])**: HTTP 없음 — `outputs/` 최신 JSON을 읽어 `OPS_DASHBOARD.html` 정적 파일만 생성한다. 파일이 없으면 `No data`로 표시한다. 브라우저에서 보는 HTML은 생성 시점 스냅샷이므로 최신 상태 확인 전 `live-sync-account`→`reconcile`→`risk-check`→`ops-dashboard`→`sell-plan`→`daily-ops-summary` 후 재생성 권장. 웹서버·네트워크 호출·실주문·SELL 자동화·KIS POST 없음.
- **`post-trade-runbook --with-summary` ([실전-19])**: 기존 post-trade 조회 흐름 이후 로컬 리포트 체인을 생성한다. `ops-dashboard`, `sell-plan`, `daily-ops-summary`, `html-dashboard` 생성 실패는 warning으로 기록되며, reconcile mismatch나 risk alert의 기존 판정은 유지된다. 추가 네트워크 호출, 웹서버, 실주문, SELL 자동화, KIS POST 없음.
- **`cleanup-reports` ([실전-20])**: 기본 dry-run이며 `--apply` 없이는 삭제/이동하지 않는다. `output_dir` 내부 리포트만 대상으로 하고 `.kis_token_cache.json`, `.gitkeep`, 주요 최신 Markdown/HTML 파일은 보호한다. 오래된 리포트를 archive/delete하면 daily summary latest fallback 대상이 줄어들 수 있으므로 audit 확인 후 적용한다. AppleDouble `._*` 실제 삭제는 `--remove-appledouble`가 필요하다. 네트워크 호출·실주문·SELL 자동화·KIS POST 없음.
- **`report-index` ([실전-21])**: `outputs/`와 지정 archive 디렉터리의 리포트 목록을 정적 인덱스로 만든다. JSON 원문 전체를 복사하지 않고 status/count 중심 summary만 포함한다. 깨진 JSON은 warning으로 남기고 status 없이 표시한다. 웹서버·네트워크 호출·실주문·SELL 자동화·KIS POST 없음.
- **`ops-dry-run` ([실전-22])**: 기본 실행은 네트워크 없이 세션/KIS 설정/risk/ops/sell/daily/html/index 점검만 수행한다. `--network`가 있을 때만 KIS OAuth·잔고조회·reconcile 조회를 포함한다. 장외/주말 session closed는 warning이며 리포트 생성은 계속한다. 최신 account/reconcile/notification 등 입력 리포트가 부족하면 daily/html 단계가 warning 또는 no-data 성격을 띨 수 있다. 실주문·SELL 자동화·KIS 주문 POST 없음.
- **`open-dashboard` ([실전-23])**: 기본은 주요 운영 리포트 경로 출력만 수행한다. `--open`/`--open-index`/`--open-all` 사용 시에도 `output_dir` 내부의 존재하는 HTML 파일만 `file://`로 연다. Markdown은 경로 안내만 하며 자동으로 열지 않는다. 웹서버·외부 URL·네트워크 호출·실주문·SELL 자동화·KIS POST 없음.
- **`post-trade-runbook` risk policy ([실전-24])**: `--stop-loss-pct`, `--take-profit-pct`, `--warn-loss-pct`, `--warn-profit-pct`는 post-trade risk_check 단계의 경고 임계값만 조정한다. 값은 JSON summary와 Markdown Risk Policy에 기록된다. `--with-summary`의 dashboard/daily summary는 생성된 risk report를 통해 반영된다. SELL·시장가·자동청산·KIS 주문 POST 없음.
- **`report-health-check` ([실전-25])**: outputs/DB/token cache 상태를 진단하고 `REPORT_HEALTH.md`와 `report_health_*.json`만 생성한다. 오래된 리포트, 누락된 dashboard/index, AppleDouble, token cache 만료 임박, output 파일 수 초과를 warning으로 남기지만 직접 cleanup/delete/archive/network/notify/order를 실행하지 않는다. `HEALTH_WARNING`은 다음 조치 필요 신호이며 자동 복구가 아니다.
- **`weekly-maintenance` ([실전-26])**: report-health, cleanup dry-run, daily summary, HTML dashboard, report-index를 묶어 실행한다. cleanup 후보가 있어도 `--apply`/archive 이동을 하지 않으며 CLI에 `--apply`, `--archive`, `--network`, `--send` 옵션이 없다. 생성 리포트의 next actions는 수동 조치 제안일 뿐 자동 복구가 아니다.
- **`notify-alerts --include-maintenance` ([실전-27])**: 기본 notify source는 유지하고, 옵션을 명시한 경우에만 `weekly_maintenance_*.json`과 `report_health_*.json`을 읽는다. OK는 기본 제외, `--include-ok`일 때만 INFO로 포함한다. `--send` 없이는 네트워크 호출이 없으며, 알림 메시지는 cleanup apply/archive 이동/주문 트리거가 아니다.
- **`weekly-report-bundle` ([실전-28])**: 주간 리포트 파일을 복사해 번들 폴더와 index를 만들 뿐 원본을 삭제/이동하지 않는다. 기본 ZIP 없음, `--zip`일 때만 zip 생성. `.env`, `.kis_token_cache.json`, DB, 소스 코드는 whitelist 대상이 아니며 번들에 포함하지 않는다. 번들에 포함된 리포트는 생성 시점 스냅샷이다.
- **`generate-checklists` ([실전-29])**: Markdown 체크리스트 템플릿만 생성한다. 실행 시점의 계좌/세션/리포트 상태를 검증하지 않으며, 체크리스트 안의 명령도 자동 실행하지 않는다. cron/launchd/plist 생성, 네트워크 호출, 실주문, SELL 자동화, KIS POST는 의도적으로 제외한다.
- **`safety-audit` ([실전-30])**: 로컬 파일과 선택 DB의 읽기 전용 감사다. 최신성은 파일 mtime/JSON timestamp 기준의 추정이며, 실제 증권사 계좌 상태를 네트워크로 재조회하지 않는다. partial fill DB 점검은 `real_order_history`/`real_fill_history`의 order_id·수량 집계에 의존한다. SAFETY_AUDIT_OK도 실주문 허가가 아니라 운영자 수동 점검 보조 신호다.
- **Safety Audit dashboard link ([실전-31])**: dashboard/index/open-dashboard 연동은 `outputs/` 로컬 파일 링크 표시만 수행한다. 표시된 `SAFETY_AUDIT_OK`는 실주문 허가가 아니며, 최신 JSON이 없으면 `NOT_AVAILABLE`로 표시될 수 있다. 링크 대상 파일의 실제 내용 최신성은 `safety-audit` 재실행 여부에 의존한다.
- **`archive-viewer` ([실전-32])**: `outputs/`와 `outputs/archive/` 안의 알려진 리포트 패턴만 metadata 중심으로 스캔한다. JSON은 status/time/count 등 제한된 키만 읽으며, DB·source code·token cache·`.env`는 제외한다. HTML table filter는 inline minimal JS이며 외부 CDN은 없다. Archive Viewer는 실주문 허가, 자동 복구, cleanup apply, archive 이동 기능이 아니다.
- **Archive Viewer filter/UX ([실전-33])**: `Needs Attention`은 metadata 기반의 수동 점검 우선순위다. 실제 계좌를 재조회하지 않고, DB 원문이나 KIS raw 원문을 읽지 않으므로 stale/partial/mismatch 판단은 리포트 JSON의 제한 키에 의존한다. HTML 필터/정렬은 inline JS 편의 기능이며 JS가 꺼져도 기본 표만 표시된다.
- **Archive Viewer Korean UI ([실전-34])**: 한국어 label은 HTML/Markdown/CLI 표시 전용이다. JSON export의 raw `status`, `report_type`, `severity` 값과 HTML data attribute는 기존 machine-readable 값을 유지한다. 알 수 없는 status는 원문에 `(미분류)`를 붙여 표시하므로 새 status 추가 시 label map 보강이 필요할 수 있다.
- **Archive Viewer Print/Export ([실전-35])**: CSV/Markdown summary는 metadata 기반 export만 수행한다. 리포트 원문 전체, DB 내용, token/app secret/account 원문은 포함하지 않는다. `--no-csv`/`--no-summary-md`는 해당 실행에서 생성을 생략하지만 기존 파일이 이미 있으면 별도 삭제하지 않는다.
- **Archive Viewer Saved Filter Presets ([실전-36])**: preset은 `ARCHIVE_VIEWER_PRESETS.json` 정적 파일과 HTML inline JS로만 동작한다. localStorage나 외부/로컬 fetch를 사용하지 않으며, 현재 HTML table의 필터 input만 조정한다. CSV/Markdown export는 프리셋 적용 상태가 아니라 전체 scan metadata 기준이다.
- **Archive Trend Analytics ([실전-37])**: trend는 Archive Viewer entries metadata와 파일명/mtime 기반 날짜를 사용한 로컬 통계다. 실계좌 상태를 재조회하지 않고 DB 내용, KIS raw 원문, 리포트 원문 전체를 읽지 않으므로 누락/오래된 리포트가 있으면 추세 해석도 그 한계를 따른다. `warning_trend_7d`/`blocked_trend_7d` 필드는 `--trend-days` 값에 따라 실제 길이가 달라질 수 있다.
- **AI Live Trade Recommendation ([실전-37])**: `ai-live-recommend`는 규칙 기반 추천/주문안 생성기이며 전략 수익성을 보장하지 않는다. `live_order_plan_ai_*`는 BUY/LIMIT 중심으로 기존 `live-approve` 검증에 맞춘다. SELL/REDUCE 후보는 리포트에 표시되지만 현재 `live-approve`가 BUY만 검증하므로 주문안에는 넣지 않는다. 안전 감사, reconcile mismatch, stale snapshot, partial fill, duplicate order risk가 있으면 `allowed_for_plan=false`로 차단될 수 있다.
- **AI Recommendation Validation ([실전-38])**: `validate-ai-recommendation`은 로컬 DB 기반 in-memory 검증이다. 여전히 종가 근사 체결이며 거래정지, 호가 공백, 장중 유동성, 실제 브로커 체결 품질은 반영하지 않는다. 결과는 수익 보장이 아니며, `paper_*` 운영 테이블이나 실계좌 테이블에는 쓰지 않는다.
- **AI Recommendation Advanced Metrics ([실전-39])**: Sharpe, profit factor, expectancy, benchmark 비교는 deterministic 참고 지표다. 거래 수가 적거나 닫힌 거래가 없으면 일부 값은 `None`/0으로 표시된다. Benchmark는 동일비중 buy-and-hold 단순 비교이며 리밸런싱, 배당, 환율은 반영하지 않는다.
- **AI Recommendation Cost / Slippage Validation ([실전-40])**: 수수료, 세금, 슬리피지, 최소/최대 주문금액은 단순 비용 모델이다. 비용 적용 결과도 검증용이며 KIS/live-approve/실주문과 연결되지 않는다.
- **AI Recommendation Portfolio Risk Validation ([실전-41])**: 섹터는 로컬 `sector_map.json`에 의존하며 파일이 없거나 누락된 심볼은 `UNKNOWN`으로 처리된다. 상관관계는 `market_prices` 일별 수익률 기반 단순 Pearson 계산이며 lookback/min points가 부족하면 unavailable warning이 남는다. `blocked` severity는 검증 경고일 뿐 주문 실행 차단이 아니다.
- **AI Recommendation Liquidity Constraint Validation ([실전-42])**: 유동성 제한은 `market_prices.volume` 품질에 의존한다. volume이 없거나 0이면 주문을 임의로 막지 않고 unavailable warning을 남기며 기존 방식으로 진행한다. 평균 거래량/거래대금은 종가와 volume 기반 단순 계산이며 실제 호가 잔량, 장중 체결 가능성, 시장 충격은 보장하지 않는다.
- **AI Recommendation FX / Currency-Aware Validation ([실전-43])**: 환율은 로컬 `fx_rates.json` 또는 `--fallback-fx`에 의존한다. 파일/값이 없으면 missing rate warning 후 1.0을 사용해 deterministic하게 진행한다. 외부 환율 조회, 환전 수수료, 실제 체결 환율, 배당/세금 통화 처리는 아직 반영하지 않는다.
- **`init-context` (AI_CONTEXT bootstrap)**: 프로젝트 분석은 로컬 파일명·확장자·README 일부 텍스트 기반의 단순 추론이다. 하위 프로젝트 일괄 초기화는 기준 폴더의 immediate child만 대상으로 하며, nested monorepo package 전체를 재귀 탐색하지 않는다. 기존 파일은 overwrite하지 않으므로 이미 오래된 문서는 별도 수동 갱신이 필요하다.
- **Trading session ([실전-9])**: **국내 정규장 단순 시각 가드**만(09:00~15:30 KST, 주말, env 휴일). **점심 단일가·단축장·임시 휴장·서머타임** 미반영. **`DEEPSIGNAL_ALLOW_AFTER_HOURS=true`** 시 시간 외 허용(주말·휴일은 여전히 CLOSED). **`--ignore-session-guard` CLI 없음**(테스트는 `session_now` 주입).
- **키·시크릿·계좌**: `.env`/OS 환경 변수만. **저장소·문서·테스트에 실제 값 금지.**

## 브로커·실전

- **브로커 API**  
  - 인증 방식, 세션 만료, **모의/실전 엔드포인트 분리**가 필수다. 동일 인터페이스로 감싸는 설계가 필요하다.  
- **실전 자동매매**  
  - 법적·금융·운영 리스크가 있으므로 **마지막 단계**에서만 검토한다.  
  - 본 저장소는 교육·연구·개인 판단 보조 목적을 전제로 하며, 특정 증권사·상품에 대한 권유가 아니다.  

## 점수화·시그널

- **현재 점수화는 기술지표 기반 단순 규칙(`technical_v1`)**이며, 가중치·임계값은 코드 상수로 정의되어 있다.  
- **`score-symbol`·`run-daily`의 `score_symbol_to_db`**: 최신 봉 기준으로 **`news_items`에서 조회한 키워드 감성 `news_score`** 가 있으면 `SignalScorer.score_final`로 **`final_score`** 에 반영하고 **`signals`에 저장**한다. 뉴스가 없거나 분석이 스킵되면 **`news_score`는 NULL** 이고 기술 점수만 사용한다.  
- **`macro_score` v1 ([7순위-1])**: `economic_indicators` 최신 스냅샷(VIX·DXY·TNX, yfinance)을 **`MacroScorer` 규칙**으로 요약한다. **OpenAI·유료 거시 API 없음**. 지표가 없으면 `macro_score`는 NULL이고 `final_score`는 뉴스·기술만으로 정규화된다.  
- **yfinance 거시 시리즈**: **지연·결측·티커 변경** 가능성이 있으며, 장중 이벤트(FOMC 등)는 일봉 스냅샷에 즉시 반영되지 않을 수 있다.  
- **`backtest-symbol` (`BacktestEngine`)**: 기본은 **`include_news=False`**(기술만). **`--include-news`** 및 **`db_path`** 가 있으면 각 **`trade_date`** 에 대해 **`fetch_news_items_until`** 로 **`published_at`≤해당 거래일** 인 뉴스만 사용한다(`published_at` NULL 제외). **장전·장중·타임존은 `DATE(published_at)` 기준으로만 단순화**되어 있다. **`run-daily`의 백테스트 호출은 여전히 뉴스 미포함**이다.  
- **점수·`action`은 투자 판단 보조·후보 분류용**이며, **자동 주문 근거나 매매 지시가 아니다.**  

## 포트폴리오 배분 v1 ([8순위-1])

- **`analyze-portfolio` / `PortfolioEngine`**: **단순 `final_score` 비율**과 고정 임계값(BUY·confidence·상위 5종·종목당 최대 40% 등)에 의존한다. **섹터·상관관계·변동성·유동성은 반영하지 않는다.**  
- **동일 섹터·테마에 종목이 몰릴 수 있음** (산업 라벨·지수 멤버십 미사용).  
- **`fetch_latest_signals`**: 심볼당 **가장 최근 `signal_date`** 1건만 사용한다. **같은 날 여러 전략**은 아직 구분하지 않으며 `technical_v1`만 대상이다.  
- **기준 자본**: 모의 스냅샷이 없으면 **10,000 가정**이며 실계좌와 무관하다. **`analyze-portfolio`만 단독 실행할 때** `allocations_for_paper`는 **DB 저장 없이 콘솔·raw** 수준이다. **`paper-rebalance` / `run-daily --paper-rebalance`** 가 있으면 동일 엔진 산출을 읽어 **`paper_*`에 가상 체결**한다([8순위-2]).

## 포트폴리오 모의 리밸런싱 v1 ([8순위-2])·거래비용 v1 ([8순위-3])

- **최신 일봉 종가**만 시장 기준가로 쓰며, 체결가는 **슬리피지**로 BUY는 불리·SELL은 불리하게 조정된다.  
- **수수료·세금·환율 미반영**은 완화되었으나, 여전히 **단순 비율 모델**이며 실제 증권사와 다르다.  
- **목표 수량**: `int(target_amount // market_close)` 로 **내림**; 최소 거래·임계값으로 스킵 시 **목표와 실제 괴리**가 남을 수 있다.  
- **가격 없음·0 이하** 종목은 스킵할 수 있어 목표와 실제 비중이 어긋날 수 있다.  
- **`liquidate_missing=True`(기본)**: 목표 배분에 없는 기존 포지션은 전량 매도 시도(가격 없으면 스킵). 최소 거래·임계값에 걸리면 **잔류 포지션**이 남을 수 있다.  
- **`paper_trades.price`**: DB 컬럼은 **체결가(executed)**; 세부는 `raw_json`의 `market_price`·`executed_price`·`commission`·`commission_rate`·`slippage_rate` 등을 참고한다.

## 뉴스 감성 v1 (키워드 규칙)

- **정확도 한계**: 제목·요약의 **부분 문자열 매칭**이며 문맥·아이러니·부정문을 구분하지 않는다.  
- **영어 키워드 중심**: RSS 제목·요약이 한국어·다국어일 때 **감지 누락**이 잦을 수 있다.  
- **`news_items.symbol`이 비어 있으면** 조회 시 **`title`/`summary`에 티커 문자열이 포함되는지 `LIKE`로 보조 필터**한다. 부분 일치(예: `AA` vs `AAPL`)·노이즈 가능성이 있으므로 **심볼 컬럼 채우기**가 장기적으로 유리하다.  
- **외부 AI·FinBERT·뉴스 전문 수집 없음** — 고도화 시 별도 설계가 필요하다.

## 스키마·운영

- **SQLite 스키마 마이그레이션 전략 미정**  
  - 현재는 `schema.sql` 전체 `executescript`로 초기화한다. 컬럼 변경 시 Alembic 등 도입 여부는 추후 결정.  
- **`signals` 구형 테이블**  
  - `signal_date`/`strategy_name` 등이 없는 구버전 `signals`는 `database._migrate_signals_schema`에서 **테이블 교체**로 이행한다. 운영 중 데이터가 있으면 **구형 행은 보존되지 않으므로** 백업 후 마이그레이션할 것.  
- **`backtest_results` 구형 테이블 (`run_name`, `metrics_json` 등)**  
  - v1 스키마(`symbol`, `start_date`, `end_date`, 성과 컬럼)로 `database._migrate_backtest_results_schema`가 **테이블 교체**한다. 구형 행은 이관되지 않는다.  

## 백테스트 v2 뉴스(선택) 제약

- **`published_at` 품질 의존**: NULL·형식 불량 뉴스는 **백테스트 뉴스 집계에서 제외**된다. RSS·피드에 따라 **시차·누락**이 있으면 해당 일 **뉴스 점수가 비어** 기술만 반영된다.  
- **날짜 단순화**: `DATE(TRIM(published_at)) <= until_date`(거래일)만 본다. **장 시작 전·장중** 구분, **거래소 캘린더·장 마감 후 뉴스**는 아직 모델링하지 않는다.  
- **종목 매칭**: `symbol` 컬럼 또는 `title`/`summary` **LIKE**로 단순 매칭한다. **전문·정확한 티커 매핑은 미흡**할 수 있다.  
- **최대 100건**: 동일 `until_date` 구간에서 **최신 `published_at` 순 100건**만 사용한다(그 이전 뉴스는 점수에 반영되지 않을 수 있음).  

## 백테스트 v1 제약

- **수수료·슬리피지·유동성·공매도 제한 등 미반영** (`BacktestEngine.COMMISSION_RATE` / `SLIPPAGE_BPS`는 0, 추후 확장용).  
- **체결 단순화**: 시그널 발생 **다음 거래일 종가**에 전량 체결한다.  
- **단일 종목·단일 포지션**만 시뮬레이션한다.  
- **과거 성과·백테스트 결과는 미래 수익을 보장하지 않는다.**  

## 모의투자 v1 제약

- **최신 일봉 종가를 체결가로 사용**하는 단순 규칙이며, 호가·스프레드·부분 체결 등은 반영하지 않는다.  
- **수수료·슬리피지 미반영** (실계좌와 금액이 일치하지 않는다).  
- **`paper-step`**: **한 종목**씩 시그널 기반 한 스텝. **`paper-rebalance`**: **포트폴리오** 단위로 `allocations_for_paper` 정렬(종목당 1행 `paper_positions`는 동일 스키마).  
- **모의 체결은 실제 주문이 아니며**, 증권사 API를 호출하지 않는다.  

## 리포트 CLI v1 제약

- **조회 전용**: `show-*` 명령은 SQLite에서 **읽기만** 하며, 설정·시그널·체결을 변경하지 않는다.  
- **건수 고정**: 시그널·백테스트·모의 체결 표는 기본 **최신 20건**(모의 스냅샷은 **1건**)으로 자른 요약이다.  
- **ASCII 표**: 터미널 폭에 따라 일부 컬럼(특히 `reason`)은 잘릴 수 있다. `show-signals`는 **`technical_score`·`news_score`·`final_score`** 등 컬럼이 늘어 **가로 폭이 더 넓어질 수 있다.** CSV·HTML 등 **보내기는 미포함**이다.  

## 대시보드 GUI v1 제약

- **tkinter 로컬 전용**: 별도 웹 서버·브라우저 임베드·원격 접속을 제공하지 않는다.  
- **조회 전용**: 창에서 **SQLite 쓰기·수집·점수화·백테스트·paper-step 실행**을 하지 않는다 (Refresh는 읽기 재조회만).  
- **표/요약 수준**: 차트·실시간 틱·다중 창 레이아웃 등은 포함하지 않는다. Signals 탭은 **`news_score` 등 점수 컬럼**을 표시하며, 창이 좁으면 가로 스크롤은 없고 **컬럼 일부가 잘릴 수 있다.**

## 일일 파이프라인 `run-daily` v1·v2 제약

- **단일 프로세스 순차 실행**: 내부적으로 기존 CLI와 동일한 수집·점수·백테스트·모의 로직을 호출할 뿐, **병렬 수집·분산 잠금·부분 롤백**은 없다.  
- **네트워크 의존**: `collect-news` / `collect-market` 단계에서 **RSS·yfinance 실패** 시에도 이후 심볼 루프는 시도한다(단계별 예외는 `steps`·`errors`에 남김). **`--skip-*`로 수집을 끌 수 있다.**  
- **`Success` 의미**: 터미널·JSON의 `success`는 **`failed` 상태인 단계가 없고**(`partial_failed`는 허용), **예외 스택이 `errors`에 쌓이지 않은 경우**에 `true`다. 데이터 부족으로 점수만 `partial_failed`인 경우는 `success`가 `true`일 수 있다.  
- **JSON 로그**: `--log-json` 시 `logs/daily_pipeline_YYYYMMDD_HHMMSS.json` 에 기록(UTF-8). **저장 경로·파일명 고정 옵션은 없음**(추후 확장). **버전 관리 대상 아님** (`.gitignore`: `logs/*.json`).  
- **프로세스 종료 코드**: `python main.py run-daily`는 **`DailyPipelineResult.success`가 `true`이면 종료 코드 0**, 그렇지 않으면 **1** (`sys.exit`). **`live-approve`** 는 **`DRY_RUN_COMPLETED`/`KIS_SAFE_MODE_COMPLETED`/`KIS_LIVE_ORDER_COMPLETED`** 일 때 **0**, 그 외 **1**. **`kis-check`** 도 성공 **0** / 실패 **1**. 작업 스케줄러·배치에서 실패 감지에 사용한다. **`collect-news` 등 그 외 서브커맨드는 현재 0만 반환**(예외 시 비정상 종료).  
- **콘솔 로그 배치**: `scripts/run_daily.bat`은 표준 출력·에러를 **`logs/run_daily_console.log`에 누적**하며(`.gitignore`: `logs/*.log`), 실패 시 **`ERRORLEVEL`을 콘솔에 출력**한다.  
- **실패 알림 v1**: `NOTIFY_ON_FAILURE=true`이고 **`WEBHOOK_URL`** 이 있을 때만 `run-daily` **실패 후** POST. 페이로드는 **`title`/`message`/`detail`** JSON이며 **Discord·Slack 기본 웹훅 형식과 다를 수 있다.** 알림 HTTP 실패·타임아웃은 **`run-daily` 종료 코드(0/1)를 변경하지 않는다.**  
- **스케줄러 미내장**: OS 작업 스케줄러·cron 등에 **사용자가 명령을 등록**해야 한다.  
- **실주문·브로커 없음** (모의 `paper-step` 또는 **`--paper-rebalance` 시 `paper-rebalance` 한 번**).  

## Risk guard ([실전-12]~[실전-13])

- **경고 전용**: `risk-check`·post-trade **`risk_check`** 단계는 리포트만 생성하며 **SELL·시장가·자동 청산을 하지 않는다.**
- **데이터 소스**: 최신 **`real_positions`** 스냅샷(`avg_price`·`current_price`). 가격이 없으면 해당 종목 **WARNING**.
- **`--sync-first` 미구현**: 검사 전 **`live-sync-account --network`** 로 DB를 갱신하는 것을 권장.
- **post-trade 통합 ([실전-13]/[실전-24])**: `post-trade-runbook` 5단계에서 **`run_portfolio_risk_check`**(standalone `risk-check`와 동일). **`POST_TRADE_RISK_ALERT`** 시 종료 코드 **1**. runbook 내 risk는 기본 `RiskGuardPolicy`를 쓰되, [실전-24]부터 standalone `risk-check`와 같은 threshold CLI 옵션으로 조정할 수 있다.
- **임계값**: 기본 손절 -7%·익절 +15%는 보수적 예시이며 standalone CLI·정책으로 조정 가능. 투자 판단·법적 책임은 사용자에게 있음.

## Trading runbook ([실전-10]~[실전-13])

- **오케스트레이션만**: `pre-trade-runbook` / `post-trade-runbook`은 기존 CLI 로직을 **순서대로 호출**할 뿐, KIS 주문 API·SELL·시장가·자동 취소를 추가하지 않는다. 실매수는 여전히 **`live-approve --execute`** 한 경로뿐이다.
- **post-trade 최종 상태**: `POST_TRADE_OK` / `POST_TRADE_WARNING` / **`POST_TRADE_RISK_ALERT`**(손절·익절·혼합 알림) / `POST_TRADE_BLOCKED`.
- **`--network` 필수**: pre/post runbook 모두 KIS HTTP(sync·reconcile·status)가 필요하다. 오프라인에서는 STEP 1(세션)만 의미가 제한적이다.
- **pre-trade reconcile**: `live-sync-account` 직후 DB를 갱신한 뒤 reconcile하므로, **정상 시에는 broker·DB가 일치**해 통과한다. 불일치는 **sync 저장 실패·DB 손상·이전 스냅샷 잔존** 등 이상 징후로 본다.
- **`--require-pre-trade-runbook`은 opt-in**: 기본 `live-approve` 동작은 변경 없음. 운영에서 강제하려면 스크립트·문서에 플래그를 명시해야 한다.
- **TTL 기본 10분**: `pre-trade-runbook` 실행 후 10분 내 `live-approve --execute` 권장. `--pre-trade-runbook-max-age-minutes`로 조정.
- **검증 필드**: report `summary`의 `plan_path`·`symbol`·`quantity`·`limit_price`와 execute 요청이 일치해야 한다. [실전-10] 이전 리포트는 필드가 부족할 수 있음 — runbook 재실행 권장.
- **리포트 경로**: `outputs/pre_trade_runbook_*.json`, `PRE_TRADE_RUNBOOK.md` 등은 로컬 산출물이며 버전 관리 제외 권장.

## Telegram 코인 스캔 메시지 반복 (해결)

- **증상**: `⏳ 코인 전체 스캔 중…` 이 5~10초마다 반복 전송.
- **원인**: 스캔이 길어지는 동안 Telegram `update_id`가 늦게 저장되어 동일 메뉴 요청이 매 폴링마다 재처리됨.
- **조치**: `acknowledge_update` 즉시 저장, 메뉴 스캔 lock, `TELEGRAM_PROGRESS_NOTIFY_MIN_SECONDS`(기본 300초) 쓰로틀.
- **진행 알림 끄기**: `.env`에 `TELEGRAM_PROGRESS_NOTIFY=false`

## 기타

- 새 이슈가 발견되면 본 파일에 **날짜 없이도** bullet로 추가하고, 해결되면 삭제하거나 “해결됨”으로 옮긴다.
