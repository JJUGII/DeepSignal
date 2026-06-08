# DeepSignal — 할 일 (우선순위)

우선순위는 **의존 관계**와 **리스크**를 기준으로 한다. 위에서부터 처리한다.

---

## [1순위] 저장소·실행 기반

- [x] 프로젝트 기본 폴더 구조 생성  
- [x] Python 패키지 구조 정리  
- [x] SQLite 초기 DB 구조 생성  
- [x] `main.py` 진입점 생성  
- [x] `README.md` 작성  
- [x] `requirements.txt` 작성  
- [x] **macOS 실행 환경 정리** (`requirements-macos.txt`, `scripts/setup_macos.sh`, `scripts/test_macos.sh`, 조회 전용 `run_live_precheck_macos.sh`)  
- [x] **macOS 운영 문서 강화** (`docs/MACOS_OPERATION_GUIDE.md`, `docs/MACOS_TROUBLESHOOTING.md`, `docs/REAL_TRADING_CHECKLIST.md`)  
- [x] import 테스트 작성 (최소 스모크: 패키지 로드·진입점 실행)  

---

## [2순위] 뉴스 수집 최소 루프

- [x] `NewsItem` 모델(또는 dataclass) 생성 (`deepsignal/collector/news/news_item.py`)  
- [x] 뉴스 RSS 수집기 최소 구현 (공개 RSS 2종, `RSS_FEEDS_JSON`으로 덮어쓰기 가능)  
- [x] 중복 뉴스 해시 처리 (`create_source_hash`, DB `INSERT OR IGNORE`)  
- [x] 뉴스 SQLite 저장 (`insert_news_items`)  
- [x] 수집 실행 로그 저장 (`insert_collection_run` → `collection_runs`)  

---

## [3순위] 시장 데이터·기술지표·점수화

- [x] Binance WebSocket 실시간 파이프라인 v1 (`binance-stream`, tick·depth·1m/3m/15m OHLCV)
- [x] 실행층 Execution Engine v1 (호가·Kelly·동적 청산)
- [x] LightGBM P(win) 1단계 (`crypto-train-lgbm` / `crypto-predict-lgbm`)
- [x] 추천 BUY에 LightGBM P(win)>0.55 게이트 연동 (`crypto_ml_gate`, `CRYPTO_ML_BUY_GATE`)
- [x] outcomes DB `model_probability` + `features_snapshot_json` + `entry_time`
- [x] crypto_auto_runner `binance_live_state` 신선도 체크
- [x] `install-binance-stream-launchd` / `crypto-retrain-lgbm` (AUC 배포·롤백)
- [x] LSTM/Transformer 시퀀스 모델 (`crypto-train-seq --model lstm|transformer`)
- [x] LightGBM + LSTM + Rule 앙상블 (`crypto_ml_ensemble`, `CRYPTO_ML_ENSEMBLE`)
- [x] live_state BUY 스캔 (`CRYPTO_USE_LIVE_STATE_SCAN`, REST fallback)
- [x] `install-crypto-retrain-launchd` (매일 03:10 기본)
- [x] 재학습 Sharpe 게이트 (`crypto_sharpe`, `--min-sharpe-improvement`)
- [x] **Phase 0 페이퍼** — `CRYPTO_PAPER_MODE`, `CRYPTO_PAPER_STATE.json`, `crypto-paper-status`, outcomes `paper`, stream stale Telegram
- [x] **Phase 1 데이터** — `_ob.jsonl` 10초, 피처 50, `replay_at`, `fetch-fear-greed`, `test_no_lookahead`
- [x] **Phase 2 ML 검증** — `crypto-validate-ml`, 과적합 리포트, P×N 스윕, `test_crypto_no_lookahead`
- [x] **Phase 3 AI 게이트** — `CRYPTO_GATE_MODE`, `CRYPTO_ENSEMBLE_MODE`, 동적 청산 fallback, `crypto-ml-suggest-config`
- [x] **Phase 4 피드백·재학습** — `crypto_trades`, warm-start, `--also-seq`, `retrain_history.jsonl`, `crypto-retrain-history`
- [x] OHLCV 시장 데이터 수집 (일봉, yfinance·`collect-market`)  
- [x] RSI / EMA 기술지표 계산 (`TechnicalAnalyzer`, `analyze-technical`)  
- [x] 기본 점수화 엔진 생성 (`SignalScorer`/`SignalResult`, `technical_v1`, `signals` 저장·CLI `score-symbol`; API 키 하드코딩 없음)  

---

## [4순위] 검증·표시

- [x] 백테스트 엔진 v1 (`BacktestEngine`, `backtest-symbol`, `backtest_results`)  
- [x] 모의투자 엔진 v1 (`PaperTradingEngine`, `paper-step`, `paper_*` 테이블)  
- [x] 최소 GUI 대시보드 v1 (`tkinter`, `python main.py dashboard`, `deepsignal/dashboard`, 조회 전용)  
- [ ] 웹·차트·고급 대시보드 (선택)  
- [ ] dashboard optional dependency 분리 또는 Tk 미포함 macOS Python 안내 고도화  
- [ ] Tk 포함 macOS 패키징/실행 환경 검토  

---

## [5순위] 운영·자동화 (실주문 제외)

- [x] 일일 파이프라인 단일 명령 `run-daily` (`collect-news` → `collect-market` → **`collect-macro`** → `MARKET_SYMBOLS`별 `score-symbol` / `backtest-symbol` / `paper-step`)  
- [x] `run-daily` 운영 옵션 v1: `--skip-news`, `--skip-market`, **`--skip-macro`**, `--symbols`, `--no-backtest`, `--no-paper`, `--log-json`, `DailyPipelineResult` / `PipelineStepResult`, `logs/daily_pipeline_*.json`  
- [x] `run-daily` 종료 코드(성공 0 / 실패 1)·Windows 배치 `scripts/run_daily.bat`, `scripts/run_dashboard.bat`, `logs/run_daily_console.log`  
- [x] `run-daily` 실패 알림 v1: `NOTIFY_ON_FAILURE` / `WEBHOOK_URL` / `WebhookNotifier`·`notify_pipeline_failure` (기본 비활성, 종료 코드와 분리)  
- [ ] `run-daily` 고도화: Discord/Slack 전용 페이로드·알림 재시도·로그 로테이션·`--log-json PATH` 등  

---

## [6순위] 뉴스 감성·점수 연동 (실주문 제외)

- [x] 키워드 규칙 기반 `SentimentAnalyzer`·`NewsSentimentResult` (`deepsignal/analyzer/sentiment/sentiment_analyzer.py`)
- [x] `fetch_recent_news_items` (`database.py`), CLI `analyze-news SYMBOL`
- [x] `SignalScorer.score_final`에 `news_score`/`macro_score` 선택 가중 (기존 동작 유지: 둘 다 `None`이면 technical만)
- [x] `score-symbol`·`run-daily` 점수 단계에서 **`signals.news_score`·`final_score`** 반영 (`score_symbol_to_db`)
- [x] `show-signals`·대시보드 Signals에서 **`technical_score` / `news_score` / `final_score`** 등 확인 (`fetch_recent_signals`, `console_formatter`, `dashboard_app`)
- [x] `backtest-symbol --include-news`·`fetch_news_items_until`·`BacktestEngine` 일자별 뉴스 감성(기본 미사용, `run-daily` 미연동)

---

## [7순위] 거시·통합 점수 (실주문 제외)

- [x] yfinance 기반 거시 스냅샷 수집(VIX·DXY·TNX)·`economic_indicators` 저장·`collect-macro` / `analyze-macro`
- [x] `MacroScorer` v1 규칙·`SignalScorer` 가중(0.6/0.2/0.2)·`score-symbol` / `run-daily` 연동
- [ ] FRED·CPI·실업률·FOMC 일정 등 **추가 소스·규칙** (유료 API·OpenAI 없이 가능한 범위부터)

---

## [8순위] 포트폴리오·배분 (실주문 제외)

- [x] `PortfolioEngine` v1·`PortfolioAllocation` / `PortfolioSnapshot`·`fetch_latest_signals`·CLI **`analyze-portfolio`** (점수 기반 배분·거시 투자 상한·`allocations_for_paper` raw)
- [x] **포트폴리오 모의 리밸런싱 v1** (`PaperTradingEngine.rebalance_portfolio`, `fetch_latest_market_price`, CLI **`paper-rebalance`**, **`run-daily --paper-rebalance`**; `paper_*`만, 실주문·브로커 없음)
- [x] **모의 리밸런스 거래비용 v1** (`PaperRebalanceConfig`, 수수료·슬리피지·최소 거래금액·임계값, CLI/`run-daily` 옵션)
- [ ] **모의 리밸런싱 추가 고도화**: partial fill·리밸런싱 주기·섹터·상관·변동성 리스크
- [ ] **섹터 익스포저** 한도·라벨링
- [ ] **종목 간 상관·중복** 제어
- [ ] **변동성 가중**·리스크 패리티
- [ ] **스톱·손절 규칙** (모의·백테스트 정책으로만)

---

## [실전] 승인형 매수 (단계적, 브로커 연동 전)

- [x] **[실전-1] Live Order Plan** (`live_order_plan.py`, CLI **`live-plan`**, JSON/Markdown 산출, **주문 전송 없음**)
- [x] **[실전-3] KIS Broker Adapter 준비** (`kis_config`, `KISBroker`, **`kis-check`**, **`live-approve --broker kis`**; **실매수 주문 HTTP 없음**; OAuth는 **`kis-check --network`** 선택)
- [x] **[실전-3 보강] KIS access token 파일 캐시** (`outputs/.kis_token_cache.json`, secret/account 저장 없음)
- [x] **[실전-5] KIS 주문·체결 조회·계좌 스냅샷** (`kis_order_status`, `live_account_sync`, CLI **`live-order-status`** / **`live-sync-account`**, `KISBroker` inquire; `paper_*` 미사용)
- [x] **[실전-6]** `real_positions` / `real_account_snapshots` DB 저장, **`reconcile-live-account`**; 최신 빈 계좌 스냅샷 기준 포지션 조회 및 KIS `--debug-raw` 점검 옵션
- [x] **[실전-7]** duplicate order protection — `real_order_history`, **`order_guard`**, **`live-order-guard-check`**, `live-approve` preflight
- [x] **[실전-8]** `real_fill_history`, **`fill_tracker`**, partial fill 추적, **`live-fill-summary`**, guard `partial_fill_open`
- [x] **[실전-9]** trading session manager — 정규장·주말·휴일, **`trading-session-check`**, `live-approve` 세션 가드
- [x] **[실전-10]** trading runbook — **`pre-trade-runbook`** / **`post-trade-runbook`**, `runbook.py`, JSON·MD 리포트
- [x] **[실전-11]** **`live-approve --require-pre-trade-runbook`** — `runbook_guard.py`, TTL·plan/symbol/qty/limit 검증
- [x] **[실전-12]** stop-loss / take-profit guard — **`risk_guard`**, CLI **`risk-check`**, 경고 리포트만 (SELL 없음)
- [x] **[실전-13]** post-trade-runbook + **`risk-check` 통합** — `risk_check` 단계, `POST_TRADE_RISK_ALERT`, runbook Risk Summary
- [x] **[실전-14]** risk dashboard / 운영 상태 요약 — **`ops-dashboard`**, 최신 account/reconcile/risk/fill/order 요약 JSON·Markdown, 조회 전용
- [x] **[실전-15]** manual sell plan generator — **`sell-plan`**, 운영자 검토용 SELL 계획서 JSON·Markdown, 자동매도·SELL 주문 없음
- [x] **[실전-16]** alert-only notification center — **`notify-alerts`**, Telegram/Discord 알림, 기본 dry-run, 주문 실행 없음
- [x] **[실전-17]** daily operations summary — **`daily-ops-summary`**, 당일 account/reconcile/risk/ops/sell/notification 통합 JSON·Markdown, 조회 전용
- [x] **[실전-18]** static HTML risk dashboard — **`html-dashboard`**, `outputs/OPS_DASHBOARD.html`, 로컬 JSON 기반 브라우저용 정적 대시보드, 웹서버·네트워크·주문 없음
- [x] **[실전-19]** post-trade runbook report chain — **`post-trade-runbook --with-summary`**, risk/ops/sell/daily/html 리포트 선택 연동, 기본 동작 유지
- [x] **[실전-20]** report archive cleanup / outputs retention manager — **`cleanup-reports`**, dry-run 기본, keep-days/keep-latest/archive/AppleDouble 정리, audit 생성
- [x] **[실전-21]** dashboard archive index — **`report-index`**, outputs/archive 리포트 날짜·종류별 HTML/Markdown/JSON 정적 인덱스
- [x] **[실전-22]** one-command dry-run operations — **`ops-dry-run`**, 세션/KIS/risk/ops/sell/daily/html/index 순차 점검, `--network` 선택 조회, JSON/Markdown 리포트 생성
- [x] **[실전-23]** lightweight local web viewer — **`open-dashboard`**, 주요 운영 HTML/Markdown 리포트 경로 안내, 선택적 로컬 HTML 열기, 웹서버·네트워크 없음
- [x] **[실전-24]** post-trade risk policy option pass-through — **`post-trade-runbook`** 에 `risk-check` 동일 threshold 옵션 전달, JSON/Markdown policy 기록
- [x] **[실전-25]** report health check — **`report-health-check`**, outputs/DB/token/report freshness 진단 JSON/Markdown, next actions 제안, 수정·삭제·네트워크 없음
- [x] **[실전-26]** weekly maintenance dry-run — **`weekly-maintenance`**, report-health/cleanup dry-run/daily/html/index 통합, JSON/Markdown summary, 삭제·이동·네트워크 없음
- [x] **[실전-27]** maintenance report notification dry-run only — **`notify-alerts --include-maintenance`**, weekly/report-health 알림 메시지 변환, 기본 dry-run audit, `--send` 명시 시에만 전송
- [x] **[실전-28]** one-command weekly report bundle — **`weekly-report-bundle`**, weekly/health/notification/index/dashboard 핵심 리포트 번들 폴더 복사, 선택 ZIP, 민감 파일 제외
- [x] **[실전-29]** scheduled reminder/checklist only — **`generate-checklists`**, daily/pre-market/post-trade/weekly maintenance/safety Markdown 체크리스트 생성, 스케줄러·자동 실행·네트워크·실주문 없음
- [x] **[실전-30]** safety audit command — **`safety-audit`**, 체크리스트/SAFETY_RULES/운영 리포트/최신 위험 파일/partial fill/stale snapshot/reconcile mismatch 로컬 감사 JSON·Markdown 생성, 읽기 전용
- [x] **AI_CONTEXT bootstrap manager** — **`init-context`**, 표준 `AI_CONTEXT` 7개 Markdown 생성, metadata 추론, overwrite 방지, multi-project 초기화
- [x] **[실전-31]** safety audit dashboard link — `REPORT_INDEX.html` / `OPS_DASHBOARD.html` / `open-dashboard`에 `SAFETY_AUDIT.md`와 최신 `safety_audit_*.json` 링크·status 표시
- [x] **[실전-32]** dashboard archive viewer — **`archive-viewer`**, outputs/archive 과거 운영 리포트 read-only HTML/JSON 탐색기, `open-dashboard --open-archive` 연결
- [x] **[실전-33]** archive viewer filters/UX polish — `ARCHIVE_VIEWER.html` 필터/정렬/Needs Attention/latest_by_type JSON export 고도화
- [x] **[실전-34]** archive viewer Korean operator UI/status labeling — 내부 raw status 유지, HTML/report 표시 label만 한국어화
- [x] **[실전-35]** Archive Viewer Print / Export Mode — print CSS, `ARCHIVE_VIEWER.csv`, `ARCHIVE_VIEWER_SUMMARY.md`, `export_files` JSON, `--no-csv`/`--no-summary-md`
- [x] **[실전-36]** Saved Filter Presets — `ARCHIVE_VIEWER_PRESETS.json`, HTML inline preset 적용, summary/report-index/open-dashboard 연결
- [x] **[실전-37]** Archive Trend Analytics — metadata 기반 `trend_analytics`, HTML/Markdown 운영 추세, `--trend-days`
- [x] **[실전-37]** AI Live Trade Recommendation Engine — `ai-live-recommend`, AI 추천 리포트, `live_order_plan_ai_*` PENDING_APPROVAL 주문안, live-approve 수동 승인 연결
- [x] **[실전-38]** AI Recommendation Backtest / Paper Validation — `validate-ai-recommendation`, in-memory portfolio, validation JSON/Markdown/CSV, `--include-sell-reduce`
- [x] **[실전-39]** AI Recommendation Advanced Validation Metrics — Sharpe/volatility/profit factor/drawdown 구간/benchmark/equity curve 확장
- [x] **[실전-40]** AI Recommendation Cost / Slippage Validation — 수수료/세금/슬리피지/최소·최대 주문금액, 비용 전/후 성과, 비용 스킵 metadata
- [x] **[실전-41]** AI Recommendation Portfolio Risk Validation — 단일 종목/섹터 비중, 고상관 종목쌍, 집중도/분산 점수, portfolio risk CSV
- [x] **[실전-42]** AI Recommendation Liquidity Constraint Validation — 거래량 기반 주문 수량 축소/스킵, 유동성 JSON/Markdown/CSV metadata
- [x] **[실전-43]** AI Recommendation FX / Currency-Aware Validation — 로컬 FX JSON/fallback 기반 기준통화 환산, 통화별 현금/포지션/CSV FX 컬럼
- [x] **[실전-44]** Telegram Approval Trading Workflow — Telegram 버튼 승인, one-time token/hash/chat_id/expiry/limit 검증, 승인/중단 audit, 터미널 실행 명령 안내
- [x] **[실전-45]** Simplified Approved Execution Command — `execute-last-approved` / `execute-approved --request-id`로 승인 audit 검증 후 기존 live execution path 연결
- [x] **[실전-46]** Daily AI Trading Workflow — `daily-ai-trade-plan/report/status`, latest AI plan pointer, 일일 운영 리포트/상태 안내
- [x] **[실전-47]** Daily AI Workflow Dashboard Integration — REPORT_INDEX/open-dashboard/archive-viewer/safety-audit에서 AI_DAILY_* 상태와 누락 단계 표시
- [x] **[실전-48]** Daily AI Workflow Freshness Validation — 오늘 생성 plan 검증, stale execute 차단, daily-ai-status/safety-audit freshness 표시
- [x] **[실전-49]** Daily AI Workflow Timestamp Normalization — Asia/Seoul generated_at 정규화, mtime fallback 최소화, freshness source 표시
- [x] **[실전-50]** Archive Viewer Freshness Source Column — 생성 시각/기준 소스 컬럼, JSON·Markdown·mtime fallback 구분, HTML/CSV/JSON/summary/report-index/dashboard 연동
- [x] **[코인-P0~P4]** 리스크 제어 강화 — concentration gate, 비보유 종목 우선, 동일종목 재매수 쿨다운, 일일 신규종목/매수금액 cap, auto-runner state 확장
- [ ] **AI_CONTEXT 고도화** — Overmind용 machine-readable metadata/export, prompt/result append helper 검토
- [ ] **Archive Viewer anomaly summary** — trend 기반 이상 징후 요약/운영자 주의 문구 고도화
- [ ] **AI recommendation 검증 고도화** — 리밸런싱 정책 반영, SELL/REDUCE 수동 절차 분리

## [나중] 고위험·고비용·규제 민감

- [ ] 실전 자동매매  
- [ ] 브로커 API 연동  
- [ ] AI 강화학습  
- [ ] 자동 주문 확대  

---

## 메모

- 항목을 완료할 때마다 체크박스를 갱신하고, 필요 시 `KNOWN_ISSUES.md`에 새 제약을 추가한다.  
- `RULES.md`의 금지 사항(실전 조기 구현, API 키 하드코딩 등)을 위반하지 않는다.
