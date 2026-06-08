# DeepSignal — 프로젝트 맥락

## 프로젝트명

**DeepSignal**

## 목적

뉴스, 경제지표, 차트 데이터를 **수집·분석**하여 매수·매도 후보를 **점수화**하는 AI 투자 **분석** 시스템을 만든다.  
초기에는 “자동으로 돈을 굴리는 봇”이 아니라, **판단 근거와 점수를 제공하는 보조 도구**에 가깝게 설계한다.

## 최종 목표 (장기)

1. 전략·데이터 파이프라인이 **재현 가능**하고 **백테스트·모의투자**로 검증된 뒤  
2. **모의투자**로 운영 검증  
3. **소액 실전 자동매매**로 연결  
4. **리스크 관리**를 전제로 규모·기능을 단계적으로 확대  

실전 자동매매는 기술만이 아니라 규제·계좌·심리적 리스크가 크므로, 문서와 코드 모두에서 **마지막 단계**로 둔다.

## 초기 MVP

- **실전 자동매매가 아님.**  
- **투자 판단 보조 시스템**: 데이터 수집 → 기본 분석 → 점수·후보 출력 → (이후) 백테스트로 검증 가능한 형태까지를 1차 완성 목표로 한다.  
- **점수화 1차**: `TechnicalAnalyzer` 결과를 `SignalScorer`로 요약해 `signals`에 **후보 분류·점수**를 기록한다 (`technical_v1`). **`news_items` 기반 `news_score`**, **`economic_indicators` 기반 `macro_score`** 가 있으면 `final_score`에 **가중(기본 0.6 / 0.2 / 0.2, 누락 시 자동 정규화)** 반영한다([7순위-1]). 이는 주문·체결과 무관하다.  
- **포트폴리오 배분 1차**: 심볼별 최신 **`signals`** 를 **`PortfolioEngine`** 으로 비교해 **목표 비중·현금 버퍼**를 산출한다([8순위-1], CLI **`analyze-portfolio`**). **실주문·브로커 없음**; `PortfolioSnapshot.raw.allocations_for_paper` 는 **`paper-rebalance`**([8순위-2])에서 읽어 **`paper_*`만** 가상 체결로 반영한다(단독 `analyze-portfolio`는 여전히 DB 체결 없음).  
- **백테스트 1차**: 동일 규칙을 과거 OHLCV에 리플레이해 **가상 체결·요약 지표**를 `backtest_results`에 남긴다. 선택적으로 **`--include-news`** 로 거래일까지의 뉴스 감성을 반영한다([6순위-4]). 실주문·실계좌와 무관하다.  
- **모의투자 1차**: DB에 적재된 최신 일봉과 시그널로 **종목별 `paper-step`** 한 스텝씩 가상 계좌를 갱신하거나([4순위-2]), **`paper-rebalance` / `run-daily --paper-rebalance`** 로 **포트폴리오 단위** 정렬만 수행한다([8순위-2]). 리밸런스 경로는 **`PaperRebalanceConfig`** 로 **수수료·슬리피지·최소 거래·리밸런스 임계값**을 반영할 수 있다([8순위-3]). 모두 `paper_*` 테이블에만 기록하며 브로커·실주문과 무관하다.  
- **리포트 CLI 1차**: 동일 DB의 시그널·백테스트·모의 기록을 **콘솔 표로 조회**한다. `show-signals`는 **`news_score`·`technical_score`** 등을 표시한다([6순위-3]). 편집·주문·보내기는 포함하지 않는다.  
- **대시보드 GUI 1차**: 동일 데이터를 **tkinter 로컬 창**으로 **읽기만** 확인한다. Signals 탭에 **뉴스·기술 점수 컬럼**을 둔다([6순위-3]). 브로커·실주문·백그라운드 수집과 연결하지 않는다.  
- **일일 파이프라인 1차**: `run-daily`로 뉴스·시장·**거시 지표** 수집 후 심볼별 점수·백테스트·**기본은 종목별 모의 1스텝(`paper-step`)** 을 **한 CLI 명령**에서 순차 실행한다. **`--paper-rebalance`** 가 있으면 루프 내 `paper-step`은 생략하고 **마지막에 포트폴리오 모의 리밸런싱 1회**한다(실주문·브로커 없음).  
- **일일 파이프라인 운영 옵션 1차**: `run-daily`에 **`--skip-news` / `--skip-market` / `--skip-macro` / `--symbols` / `--no-backtest` / `--no-paper` / `--paper-rebalance` / `--commission-rate` / `--slippage-rate` / `--min-trade-value` / `--rebalance-threshold` / `--log-json`** 및 **`DailyPipelineResult`** 요약·`logs/daily_pipeline_*.json` 기록(실주문 없음).  
- **일일 파이프라인 종료 코드·배치 1차**: **`run-daily`** 는 **`success` 기준 프로세스 종료 0/1**, **`live-approve`** 는 **`DRY_RUN_COMPLETED`/`KIS_SAFE_MODE_COMPLETED` 기준 0/1**, **`kis-check`** 는 검증·토큰 테스트 기준 **0/1**, `scripts/run_daily.bat` / `scripts/run_dashboard.bat`(실주문 없음).  
- **실패 알림 1차**: `NOTIFY_ON_FAILURE`·`WEBHOOK_URL`·`WebhookNotifier`로 **선택적 웹훅**(기본 비활성, **실주문 없음**, 알림 실패가 종료 코드를 덮어쓰지 않음).
- **macOS 실행·운영 환경 포팅**: Windows 백업 완료 후 macOS 전용 `requirements-macos.txt`와 `scripts/setup_macos.sh`, `scripts/test_macos.sh`, 조회/점검 전용 `scripts/run_live_precheck_macos.sh`를 추가했다. macOS 운영은 `.venv` 사용을 전제로 하며, 실주문 실행 스크립트는 만들지 않는다. 실전 주문은 기존 `live-approve` guard 기반 CLI에서만 수동 수행한다. 운영 문서는 `docs/MACOS_OPERATION_GUIDE.md`, `docs/MACOS_TROUBLESHOOTING.md`, `docs/REAL_TRADING_CHECKLIST.md`를 기준으로 한다.
- **뉴스 감성 CLI 1차**: `python main.py analyze-news SYMBOL` — `news_items` **제목·요약**만 영어 **키워드 규칙**으로 요약(OpenAI·FinBERT·전문 수집 없음). **`score-symbol`과 별개의 조회·요약용**이며, 점수 저장은 `score-symbol` 경로를 쓴다.  
- **Live Order Plan([실전-1])**: `python main.py live-plan` — `PortfolioEngine` 기반 **BUY 주문안만** JSON/Markdown으로 `outputs/`에 저장. **`PENDING_APPROVAL`**, **브로커·실주문·API 키 없음**; 승인·체결은 후속 단계.
- **Live Approve ([실전-2]~[실전-4])**: `python main.py live-approve` — [실전-1] JSON 검증·감사 로그. **`--broker dry-run`(기본)**: **`DryRunBroker`**·`DRY_RUN_COMPLETED`. **`--broker kis`**: 기본 **`KIS_SAFE_MODE_BLOCKED`**(`order-cash` 없음). **`--execute`** 및 **`LiveExecutionPolicy`/`validate_live_execution`** 통과·**`KIS_ENV=live`**·**`--allow-live-env`**·**`--final-confirm I_UNDERSTAND_REAL_ORDER`** 시에만 **`KISBroker(..., safe_mode=False).place_order(..., execute=True)`** 로 **`order-cash` POST**(단발·LIMIT BUY·자동 재시도 없음). **`--approved` 없으면 거부**, **`--no-dry-run` 거부**. **`KIS_ENV=paper`** 는 실주문 가드에서 차단.
- **실주문 후 조회 ([실전-5])**: **`live-order-status`** — 감사 JSON에서 ODNO 추출·**`--network`** 시 일별체결 조회; **`live-sync-account --broker kis --network`** — 잔고조회로 포지션·현금 스냅샷을 **`outputs/`** 파일로 저장(**`paper_*` 미사용**). **주문 성공 ≠ 체결**; 증권사 앱과 대조 권장.
- **실계좌 스냅샷 DB·Reconcile ([실전-6])**: … **`reconcile-live-account`** … **`paper_*`와 혼합하지 않음**. KIS 포지션은 `pdno` 6자리 + `hldg_qty > 0` 기준으로 파싱하고, 최신 DB 포지션은 최신 `real_account_snapshots.snapshot_time` 기준으로만 읽는다. 최신 계좌 스냅샷이 포지션 0개이면 빈 목록이 정상이며, `--debug-raw`로 KIS `output1`/`output2` 행 수와 키 목록만 안전하게 점검할 수 있다.
- **Duplicate order protection ([실전-7])**: **`real_order_history`** 적재·**`order_guard`** 로 최근 주문·pending·reconcile mismatch·stale snapshot 차단. **`live-approve --execute`** 는 execution guard **이후** order guard 통과 시에만 KIS POST. CLI **`live-order-guard-check`**.
- **Fill tracking ([실전-8])**: **Order(주문)** vs **Fill(체결)** 분리 — **`real_fill_history`**·**`fill_tracker`**·**`live-order-status --network`** 체결 저장·**`live-fill-summary`**. Partial fill(`remaining_quantity > 0`) 시 guard **`partial_fill_open`** 차단.
- **Trading session ([실전-9])**: 평일 **09:00~15:30 KST**·주말·`DEEPSIGNAL_MARKET_HOLIDAYS` 수동 휴일 — 장외 **`live-approve --execute`** 차단(`LIVE_EXECUTION_BLOCKED_BY_SESSION`). CLI **`trading-session-check`**. 공휴일 자동 API 없음.
- **Trading runbook ([실전-10]~[실전-13])**: **`pre-trade-runbook --network`** — session → sync → reconcile → guard → plan → `PRE_TRADE_READY`/`BLOCKED`. **`post-trade-runbook --network`** — status → fill → sync → reconcile → **risk_check** → `POST_TRADE_OK`/`WARNING`/**`RISK_ALERT`**/`BLOCKED`. **`outputs/*_trade_runbook_*.json`**, **`PRE/POST_TRADE_RUNBOOK.md`**, Risk Summary·`risk_alert_*.json`. 주문 기능 확대 없음·오케스트레이션만.
- **Pre-trade runbook guard ([실전-11])**: **`live-approve --require-pre-trade-runbook`** — 최근 `PRE_TRADE_READY` JSON·TTL(기본 10분)·plan/symbol/qty/limit 일치. 실패 시 **`LIVE_EXECUTION_BLOCKED_BY_RUNBOOK`**. 가드 순서: session → execution → **runbook** → duplicate → POST.
- **Risk guard ([실전-12]~[실전-13], [실전-24])**: CLI **`risk-check`** + post-trade **`risk_check`** — `run_portfolio_risk_check` 공유. DB **`real_positions`** 손절/익절 **경고만**. **`RISK_ALERT.md`**. post-trade도 `--stop-loss-pct` / `--take-profit-pct` / `--warn-loss-pct` / `--warn-profit-pct` 옵션으로 동일 policy를 전달할 수 있고 JSON/Markdown에 기록한다. **SELL·자동매도·시장가 없음**.
- **Ops dashboard ([실전-14])**: CLI **`ops-dashboard`** — 최신 account snapshot, positions, reconcile, risk, fill summary, recent orders를 단일 JSON/Markdown으로 요약한다. 입력은 로컬 DB와 `outputs/` 최신 리포트뿐이며 네트워크 조회·주문·SELL·시장가·자동 취소 없음.
- **Manual sell plan ([실전-15])**: CLI **`sell-plan`** — 최신 `real_positions` 기준으로 `HOLD` / `REVIEW` / `REDUCE` / `EXIT` 운영자 검토용 SELL 계획서 JSON/Markdown만 생성한다. `live-approve` SELL 실행은 미구현이며, SELL API·시장가·자동매도·KIS POST를 추가하지 않는다.
- **Alert-only notification center ([실전-16])**: CLI **`notify-alerts`** — 최신 risk/ops/sell/reconcile 리포트에서 위험 메시지를 만들고 Telegram/Discord로 alert-only 전송한다. 기본은 dry-run이며 `--send` 없이는 네트워크 호출이 없다. 전송 결과는 `notification_audit_*.json`에 남기며 주문 실행·SELL 자동화·KIS POST 없음.
- **Daily operations summary ([실전-17])**: CLI **`daily-ops-summary`** — 오늘자 account/reconcile/risk/ops/sell/notification 리포트를 단일 JSON/Markdown으로 묶고 next actions를 생성한다. 최신 fallback은 warning으로 남기며, 조회/요약 전용이라 실주문·SELL 자동화·KIS POST 없음.
- **Static HTML risk dashboard ([실전-18])**: CLI **`html-dashboard`** — `outputs/` 최신 운영 JSON을 읽어 브라우저에서 열 수 있는 `OPS_DASHBOARD.html` 정적 파일을 생성한다. inline CSS와 HTML escaping을 사용하며, 웹서버·네트워크 호출·실주문·SELL 자동화·KIS POST 없음.
- **Post-trade runbook report chain ([실전-19])**: CLI **`post-trade-runbook --with-summary`** — 기존 post-trade order status/fill/sync/reconcile/risk-check 이후 `ops-dashboard`, `sell-plan`, `daily-ops-summary`, `html-dashboard`를 선택 연동한다. 생성 경로는 post-trade runbook `summary.generated_reports`에 남긴다. 기본 post-trade 동작은 유지하며, 추가 리포트 체인은 조회/요약/파일 생성 전용이라 웹서버·실주문·SELL 자동화·KIS POST 없음.
- **Report archive cleanup ([실전-20])**: CLI **`cleanup-reports`** — `outputs/` 내부 운영 리포트에 dry-run 기본 보존 정책(`keep-days`, `keep-latest`)을 적용하고, 필요 시 archive/delete 및 AppleDouble 정리를 수행한다. audit은 `report_cleanup_audit_*.json`에 남기며, 보호 파일과 토큰 캐시는 보존한다. 파일 정리 전용이라 네트워크·실주문·SELL 자동화·KIS POST 없음.
- **Dashboard archive index ([실전-21])**: CLI **`report-index`** — `outputs/`와 `outputs/archive`의 운영 리포트를 날짜/종류별로 묶어 `REPORT_INDEX.html`, `REPORT_INDEX.md`, `report_index_*.json` 정적 인덱스를 생성한다. status와 count 중심 summary 및 상대 링크만 포함하며, 웹서버·네트워크·실주문·SELL 자동화·KIS POST 없음.
- **One-command dry-run operations ([실전-22])**: CLI **`ops-dry-run`** — 세션 확인, KIS offline 설정 검증, risk-check, ops-dashboard, sell-plan, daily-ops-summary, html-dashboard, report-index를 순차 실행해 `OPS_DRY_RUN.md`와 `ops_dry_run_*.json`을 생성한다. `--network`가 있을 때만 KIS OAuth·계좌 동기화·reconcile 조회를 포함한다. 장외/주말 세션 종료는 warning으로만 기록한다. `live-approve` 호출, 실주문, SELL 자동화, KIS 주문 POST 없음.
- **Lightweight local web viewer ([실전-23])**: CLI **`open-dashboard`** — `outputs/OPS_DASHBOARD.html`, `REPORT_INDEX.html`, `DAILY_OPS_SUMMARY.md`, `OPS_DRY_RUN.md`, `RISK_ALERT.md`, `SELL_PLAN.md` 경로를 안내하고, 옵션 사용 시 로컬 HTML만 기본 브라우저로 연다. 기본은 목록 출력만 수행한다. 웹서버·외부 URL·네트워크 호출·실주문·SELL 자동화·KIS POST 없음.
- **Post-trade risk policy pass-through ([실전-24])**: CLI **`post-trade-runbook`** — standalone `risk-check`와 같은 threshold 옵션을 받아 post-trade risk_check 단계에 전달한다. 기본값은 기존과 동일하며, 사용된 policy는 post-trade runbook summary와 Markdown Risk Policy에 기록된다. threshold 전달만 수행하며 주문 기능 확대 없음.
- **Report health check ([실전-25])**: CLI **`report-health-check`** — `outputs/` 주요 리포트 존재, 최신 JSON 나이, DB 최신 account snapshot/positions, AppleDouble, `.kis_token_cache.json` 만료, stale dashboard, output 파일 수를 진단해 `REPORT_HEALTH.md`와 `report_health_*.json`을 생성한다. next actions를 제안하지만 수정·삭제·cleanup 실행·네트워크 호출·알림 전송·실주문·SELL 자동화·KIS POST 없음.
- **Weekly maintenance dry-run ([실전-26])**: CLI **`weekly-maintenance`** — `report-health-check`, `cleanup-reports` dry-run, `daily-ops-summary`, `html-dashboard`, `report-index`를 순차 실행해 `WEEKLY_MAINTENANCE.md`와 `weekly_maintenance_*.json`을 생성한다. `--apply`/`--archive`/`--network`/`--send` 없이 점검과 리포트 생성만 수행하며 삭제·archive 이동·네트워크 호출·알림 전송·실주문·SELL 자동화·KIS POST 없음.
- **Maintenance report notification dry-run ([실전-27])**: CLI **`notify-alerts --include-maintenance`** — 기존 risk/ops/sell/reconcile 알림 source에 더해 `weekly_maintenance_*.json`과 `report_health_*.json`을 opt-in으로 읽고 WARNING/CRITICAL/INFO 메시지로 변환한다. 기본은 dry-run audit만 생성하며 `--send` 없이는 네트워크 호출 없음. 실주문·SELL 자동화·KIS POST·cleanup apply·archive 이동 없음.
- **One-command weekly report bundle ([실전-28])**: CLI **`weekly-report-bundle`** — `weekly-maintenance`, `notify-alerts --dry-run --include-maintenance`, `report-index`, `html-dashboard`를 실행한 뒤 핵심 Markdown/HTML/JSON을 `outputs/weekly_bundles/weekly_bundle_YYYYMMDD_HHMMSS/`로 복사하고 `BUNDLE_INDEX.md` / `BUNDLE_INDEX.html`을 생성한다. `--zip`일 때만 zip을 만들며 `.env`, token cache, DB, 소스 코드는 제외한다. 삭제·archive 이동·네트워크 호출·알림 실제 전송·실주문·SELL 자동화·KIS POST 없음.
- **Scheduled reminder / checklist only ([실전-29])**: CLI **`generate-checklists`** — `outputs/checklists/`에 daily, pre-market, post-trade, weekly maintenance, safety rules Markdown을 생성한다. 운영자 참고용 템플릿만 만들며 cron/launchd/plist, 자동 실행, 네트워크 호출, 실주문, SELL 자동화, KIS POST 없음.
- **Safety audit command ([실전-30])**: CLI **`safety-audit`** — `outputs/`와 선택 DB를 읽어 체크리스트/SAFETY_RULES/운영 리포트/최신 위험 파일/pre-trade readiness/partial fill/stale snapshot/reconcile mismatch/final confirmation 자동화 의심 흔적을 감사하고 `SAFETY_AUDIT.md`와 `safety_audit_*.json`을 생성한다. OK/WARNING은 종료 코드 0, BLOCKED는 1. 네트워크·주문·cleanup·scheduler 생성 없음.
- **Safety Audit dashboard link ([실전-31])**: `report-index`, `html-dashboard`, `open-dashboard`가 Safety Audit 결과를 optional local source로 읽어 status와 `SAFETY_AUDIT.md` / 최신 `safety_audit_*.json` 링크를 표시한다. 파일이 없어도 dashboard/index 생성은 계속되며 `NOT_AVAILABLE`로 표시한다. 로컬 링크 표시 전용이라 네트워크·주문·cleanup 없음.
- **Dashboard archive viewer ([실전-32])**: CLI **`archive-viewer`** — `outputs/`와 `outputs/archive/`의 과거 운영 리포트를 read-only로 스캔해 `ARCHIVE_VIEWER.html`과 `archive_viewer_*.json`을 생성한다. report-index와 open-dashboard에 링크가 표시되며, `open-dashboard --open-archive`로 로컬 HTML만 열 수 있다. DB/source/secret 파일은 제외하고 네트워크·주문·cleanup·archive 이동 없음.
- **Archive Viewer filter/UX ([실전-33])**: `ARCHIVE_VIEWER.html`은 report type/status/severity/text/date range 필터, Only warnings/errors, Latest only, 컬럼 정렬, `Needs Attention` 빠른 링크를 제공한다. JSON export는 `summary`, `filters_available`, `entries`, `needs_attention`, `latest_by_type` 구조를 포함한다. inline JS만 사용하며 외부 CDN·네트워크·주문·자동복구 없음.
- **Archive Viewer Korean operator UI ([실전-34])**: `operator_labels.py`가 report type/status/severity/summary key를 한국어 운영자 label로 변환한다. HTML/Markdown/dashboard 표시만 바꾸며 JSON export raw status와 HTML data attributes는 변경하지 않는다. 알 수 없는 값은 원문을 보존해 `(미분류)`로 표시한다.
- **Archive Viewer Print / Export Mode ([실전-35])**: `ARCHIVE_VIEWER.html`은 print CSS를 포함하고, `archive-viewer`는 기본으로 `ARCHIVE_VIEWER.csv`, `ARCHIVE_VIEWER_SUMMARY.md`, `archive_viewer_*.json`의 `export_files`를 생성한다. CSV/Markdown은 metadata 기반이며 `--no-csv`, `--no-summary-md`로 생략 가능하다. 리포트 원문 전체·DB·secret export 없음.
- **Archive Viewer Saved Filter Presets ([실전-36])**: `archive-viewer`가 `ARCHIVE_VIEWER_PRESETS.json` 정적 preset 파일을 만들고, HTML은 inline JS로 preset을 기존 필터에 적용한다. 기본 preset은 주의 필요 항목, 최신 리포트만, 안전 점검만, 리스크/정합성, 실거래 감사다. localStorage·fetch·서버·DB 조회 없음.
- **Archive Trend Analytics ([실전-37])**: `archive-viewer`가 entries metadata로 `trend_analytics`를 계산하고, `ARCHIVE_VIEWER.html`/`ARCHIVE_VIEWER_SUMMARY.md`에 운영 추세를 표시한다. `--trend-days` 기본값은 7이며 반복 문제 유형과 trend window 계산에 사용한다. 과거 로컬 리포트 통계 전용이며 실계좌 재조회·실주문·자동 복구 없음.
- **AI Live Trade Recommendation ([실전-37])**: CLI **`ai-live-recommend`** — 최신 signals/market/macro/실계좌 snapshot/운영 리포트 metadata로 BUY/SELL/HOLD/REDUCE/INCREASE/SKIP 추천을 만들고, `AI_LIVE_TRADE_RECOMMENDATION.md`, `ai_live_trade_recommendation_*.json`, `live_order_plan_ai_*.json`을 생성한다. AI는 자동 판단과 승인 대기 주문안 생성까지만 수행하며, 최종 실주문은 기존 `live-approve --execute` 수동 승인 절차에서만 가능하다. live-approve 자동 호출, KIS POST, 시장가, final-confirm 자동 주입 없음.
- **AI Recommendation Validation ([실전-38])**: CLI **`validate-ai-recommendation`** — 로컬 DB의 `market_prices`/`signals`/macro metadata를 날짜별로 재생해 AI 추천 정책을 in-memory portfolio로 검증한다. 산출물은 `AI_RECOMMENDATION_VALIDATION.md`, `ai_recommendation_validation_*.json`, `AI_RECOMMENDATION_VALIDATION_TRADES.csv`이다. KIS/live-approve/--execute/실계좌 주문/paper_* writes 없음.
- **AI Recommendation Advanced Metrics ([실전-39])**: `validate-ai-recommendation` 결과에 annualized return, volatility, Sharpe, profit factor, expectancy, max drawdown 구간, 연속 손익, exposure, turnover, average holding days, 동일비중 buy-and-hold benchmark 비교를 추가했다. `--benchmark` 기본 ON, `--risk-free-rate` 기본 0.0이다.
- **AI Recommendation Cost / Slippage Validation ([실전-40])**: `validate-ai-recommendation`에 `CostModel` 기반 수수료/세금/슬리피지/최소·최대 주문금액 검증을 추가했다. 기본 비용은 commission 0.1%, tax 0%, slippage 5bps, min order 10,000 KRW이며 `--no-costs`로 비활성화할 수 있다. JSON/Markdown/CSV는 gross/net return, cost drag, 총 비용, 스킵 주문 metadata와 거래별 비용 컬럼을 포함한다.
- **AI Recommendation Portfolio Risk Validation ([실전-41])**: `validate-ai-recommendation`에 `PortfolioRiskConfig` 기반 단일 종목/섹터 비중, 고상관 종목쌍, concentration/diversification score를 추가했다. 섹터는 `--sector-map` 로컬 JSON만 사용하며 없으면 `UNKNOWN`이다. 산출물은 기존 validation JSON/Markdown에 `portfolio_risk`/`포트폴리오 리스크 검증`을 포함하고, 별도 `AI_RECOMMENDATION_PORTFOLIO_RISK.csv`를 생성한다.
- **AI Recommendation Liquidity Constraint Validation ([실전-42])**: `validate-ai-recommendation`에 `LiquidityConfig` 기반 평균 거래량/거래대금 제한을 추가했다. `--liquidity-limit-pct`, `--min-daily-volume`, `--min-daily-value`, `--volume-lookback-days`로 주문 수량 축소/스킵을 검증한다. 기본은 제한 없음이며 산출물은 `liquidity_model`, Markdown `유동성 제한 검증`, trades CSV liquidity 컬럼을 포함한다.
- **AI Recommendation FX / Currency-Aware Validation ([실전-43])**: `validate-ai-recommendation`에 `FXConfig` 기반 기준통화 환산을 추가했다. `--base-currency`, `--default-symbol-currency`, `--fx-rates`, `--symbol-currency-map`, `--fallback-fx`로 로컬 환율/종목 통화 정보를 사용한다. 산출물은 `fx_model`, Markdown `통화 / 환율 검증`, equity curve FX fields, trades CSV FX 컬럼을 포함한다.
- **Telegram Approval Trading Workflow ([실전-44])**: `telegram-approval-request/listen/status`로 AI live order plan에 대한 Telegram 승인 요청과 승인/중단 audit을 생성한다. request는 local state/Markdown/JSON과 optional `sendMessage`만 수행하고, listen은 chat_id/token/expiry/plan hash/order limits/today halt 검증 뒤 audit과 수동 `live-approve` 명령 안내만 남긴다. Telegram 승인은 final-confirm을 대체하지 않으며 `execute_live_order_plan()`, `live-approve`, `--execute`, `--allow-live-env`, KIS POST를 자동 호출하지 않는다.
- **Simplified Approved Execution Command ([실전-45])**: `execute-last-approved` / `execute-approved --request-id`가 Telegram approval audit/state를 조회해 승인 상태, token consumed, expiry, today halt, plan hash, plan 파일, 주문 한도, 중복 실행 여부를 검증한 뒤 기존 `execute_live_order_plan()` 경로를 재사용한다. 실행은 운영자가 터미널에서 직접 명령을 입력해야 하며 Telegram listener는 주문 실행을 호출하지 않는다. 산출물은 `EXECUTE_APPROVED_AUDIT.md`, `execute_approved_audit_*.json`, 연결 `live_approval_audit_*.json`이다.
- **Daily AI Trading Workflow ([실전-46])**: `daily-ai-trade-plan`이 AI 추천과 `live_order_plan_ai_latest.json`, `AI_DAILY_TRADE_PLAN.md`, `ai_daily_trade_plan_*.json`을 생성한다. `daily-ai-trade-report`는 최신 추천/승인/실행/체결/계좌/리스크 산출물을 요약하고, `daily-ai-status`는 현재 단계와 다음 명령을 안내한다. plan/report/status는 실주문을 실행하지 않으며, 실주문은 Telegram 승인 후 운영자가 `execute-last-approved`를 직접 실행할 때만 기존 live execution path를 사용한다.
- **Daily AI Workflow Dashboard Integration ([실전-47])**: `daily_ai_status_reader.py`가 outputs의 `AI_DAILY_*`, Telegram approval, execute-approved audit을 읽어 workflow 상태와 다음 명령을 계산한다. `report-index`, `open-dashboard`, `archive-viewer`, `safety-audit`는 이 상태를 로컬 링크/요약/warning으로 표시하며 주문 실행·네트워크 호출·Telegram API 호출은 하지 않는다.
- **Daily AI Workflow Timestamp Normalization ([실전-49])**: `time_utils.py`와 `stamp_daily_ai_payload()`로 Daily AI/Telegram/execute audit JSON·Markdown에 Asia/Seoul timezone-aware `generated_at`을 기록한다. freshness는 `generated_at` 우선·mtime fallback 보조이며 source를 표시한다.
- **Daily AI Workflow Freshness Validation ([실전-48])**: `daily_ai_freshness.py`가 plan/latest order plan/approval/execution/report/status의 오늘 생성 여부를 검증한다. `daily-ai-status`/`safety-audit`는 `--freshness-date`를 지원하고, `execute-last-approved`는 stale plan을 차단한다. 로컬 파일 read-only이며 KIS/Telegram/execute 호출은 없다.
- **AI_CONTEXT bootstrap manager**: CLI **`init-context`** — 프로젝트 루트에 표준 `AI_CONTEXT/` 구조 7개 Markdown을 생성한다. `project_context` 패키지가 로컬 README/dependency/source/test/docs 정보를 스캔해 초기 metadata를 템플릿에 반영한다. 기존 파일은 overwrite하지 않고, `--project`, `--all-projects`로 다른 프로젝트 또는 immediate child 프로젝트를 초기화할 수 있다.
- **KIS 준비 ([실전-3]~[실전-13])**: OAuth access token은 `outputs/.kis_token_cache.json`에 파일 캐시할 수 있다. 캐시는 access token·만료시각·env·app key hash만 저장하며 app secret·계좌번호·app key 원문은 저장하지 않는다. 조회 CLI의 `tokenP` 반복 호출을 줄이기 위한 것이며, 실주문 guard·KIS `order-cash` POST 경로는 변경하지 않는다.
## 핵심 원칙 (개발 순서)

아래 순서를 **의도적으로** 지킨다. 단계를 건너뛰지 않는다.

1. **데이터 수집** (뉴스, 거시, 시장 OHLCV 등)  
2. **분석** (감성·영향도, 기술적 지표 등)  
3. **점수화** (통합 시그널·후보 순위)  
4. **백테스트** (과거 데이터로 전략 검증)  
5. **모의투자** (실시간에 가까운 조건에서 가상 체결)  
6. **실전** (소액·안전장치·리스크 엔진과 함께)  

## 관련 문서

| 문서 | 용도 |
|------|------|
| `CURRENT_STATUS.md` | 지금 어디까지 왔는지 |
| `TODO.md` | 할 일 우선순위 |
| `ROADMAP.md` | 단계별 목표·금지·완료 기준 |
| `RULES.md` | 코딩·운영 규칙 |
| `KNOWN_ISSUES.md` | 알려진 제약·미결정 사항 |

## 용어 정리

- **점수화**: 여러 입력을 규칙 또는 모델로 통합해 매수·매도·리스크 등 **숫자 점수**로 만드는 과정.  
- **보조 시스템**: 최종 투자 결정과 책임은 사용자(또는 별도 정책)에 두고, 시스템은 **정보와 점수**를 제공한다.
