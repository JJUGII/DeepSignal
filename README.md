# DeepSignal

뉴스·경제지표·차트 데이터를 수집·분석해 매수·매도 후보를 **점수화**하는 **투자 판단 보조**용 Python 프로젝트입니다.  
법적·운영 리스크를 줄이기 위해 **실전 자동매매는 검증·모의 이후 단계**로 두며, 본 저장소는 특정 상품을 권유하지 않습니다.

## 현재 MVP 범위

- 로컬에서 동작하는 **실행 뼈대** (`main.py`, `deepsignal` 패키지, SQLite 스키마)
- **공개 RSS** 기반 뉴스 수집·`news_items` / `collection_runs` 저장 (`python main.py collect-news`)
- **`news_items` 제목·요약 키워드 기반 뉴스 감성 요약** (`python main.py analyze-news SYMBOL`) — **외부 AI·전문 수집 없음**, **투자 판단 보조·자동매매 아님**
- **yfinance** 기반 **일봉 OHLCV** 수집·`market_prices` / `collection_runs` 저장 (`python main.py collect-market`)
- **`market_prices` 조회 → RSI·EMA·trend_score 계산** (`python main.py analyze-technical SYMBOL`) — **콘솔 출력**
- **기술지표 기반 기본 점수화** (`python main.py score-symbol SYMBOL`) — `SignalScorer`로 `technical_v1` 점수와 **`news_items` 키워드 감성 `news_score`**, **`economic_indicators` 기반 `macro_score`**(수집 후)를 **`final_score`에 가중(기본 0.6 / 0.2 / 0.2, 누락 시 정규화)**·후보 분류를 산출하고 **`signals` 테이블에 기록** (뉴스·거시 없으면 해당 항목 제외; 중복은 `symbol`+`signal_date`+`strategy_name` 기준 스킵)
- **포트폴리오 배분 분석 v1** (`python main.py analyze-portfolio`) — 최신 **`signals`·거시 국면**으로 종목 간 **목표 비중·현금 버퍼**를 콘솔에만 표시 (**자동 리밸런싱·실주문 없음**, 모의 계좌 `equity`/`cash` 또는 기본 1만 기준)
- **단일 종목 백테스트 v1·v2** (`python main.py backtest-symbol SYMBOL`, 옵션 **`--include-news`**) — 과거 OHLCV 리플레이·가상 체결 요약, **`backtest_results` 저장** (실주문 없음; v2는 `published_at`≤거래일 뉴스만; **거시는 백테스트 미반영**)
- **모의투자 v1** (`python main.py paper-step SYMBOL`) — DB 최신 일봉·시그널로 **가상 체결·스냅샷** (`paper_positions` / `paper_trades` / `paper_account_snapshots`, **실주문·브로커 없음**)
- **포트폴리오 모의 리밸런싱 v1** (`python main.py paper-rebalance`, `run-daily --paper-rebalance`) — **`analyze-portfolio`와 동일한 `PortfolioEngine` 경로**로 `allocations_for_paper`를 읽고, **최신 일봉 종가**를 기준으로 목표 수량에 맞춰 `paper_*`만 갱신. **기본 수수료 0.1%·슬리피지 0.05%·최소 거래 $10·리밸런스 임계값 총자산 1%** (`PaperRebalanceConfig`, CLI로 조정 가능). **`paper-step`은 종목별 단일 스텝**, `paper-rebalance`는 **한 번에 포트폴리오 정렬** (**실주문·브로커 없음**)
- **리포트 CLI v1** (`show-signals` / `show-backtests` / `show-paper`) — DB 적재 데이터 **콘솔 표 조회**
- **최소 GUI 대시보드 v1** (`python main.py dashboard`) — **tkinter** 로컬 창, `show-*`와 동일 데이터 **조회 전용** (실주문·수집·점수화 버튼 없음)
- **일일 파이프라인 v1** (`python main.py run-daily`) — `collect-news` → `collect-market` → **`collect-macro`** → 심볼별 `score-symbol` → `backtest-symbol` → **`paper-step`(기본)** 또는 **`--paper-rebalance` 시 종목 루프의 `paper-step` 생략 후 포트폴리오 `paper-rebalance` 1회** (**`--no-paper`면 둘 다 생략**; **`--skip-*` / `--symbols` / `--no-*` / `--log-json`**, **`--log-json` 시 루트 `macro` 스냅샷**, **실패 시 선택적 웹훅 알림(`NOTIFY_ON_FAILURE`·`WEBHOOK_URL`)**, **`success` 시 종료 코드 0·실패 1**, **`scripts/*.bat`**, **실주문·브로커 없음**)
- **웹/차트 대시보드 등**은 **미구현**
- **승인형 실전 매수 1단계 — Live Order Plan** (`python main.py live-plan`) — `PortfolioEngine`·`allocations_for_paper`와 동일 신호 경로로 **BUY 후보 주문안만** `outputs/live_order_plan_YYYYMMDD.json`·`outputs/TODAY_LIVE_ORDER_PLAN.md`에 기록. **`PENDING_APPROVAL`**, **`dry-run` 기본 on**, **브로커·실주문·API 키 없음**
- **승인형 실전 매수 2단계 — Live Approve** (`python main.py live-approve`) — 저장된 계획 JSON 검증·감사 로그. **`--broker dry-run`(기본)**: **`DryRunBroker`**. **`--broker kis`**: **`KISBroker`**·기본은 **`KIS_SAFE_MODE_BLOCKED`**(실주문 없음). **[실전-4]** 에서 **`--execute`** 및 가드·`KIS_ENV=live`·`--final-confirm I_UNDERSTAND_REAL_ORDER`**·**`--allow-live-env`** 등을 모두 만족할 때만 **`order-cash` POST 1회(또는 `--max-orders` 한도)**. **`--approved` 필수**, **`--no-dry-run` 거부(종료 1)**. **`kis-check`**([실전-3])는 **`KIS_*`** 검증·선택 **`--network`** OAuth만
- **Telegram 승인 기록 워크플로우** (`telegram-approval-request/listen/status`) — `live_order_plan_ai_*.json`에 대한 Telegram 승인 요청과 승인/중단 audit만 생성한다. 기본 request는 dry-run이며 `--send` 때만 Telegram Bot API를 호출한다. listen은 token/chat_id/expiry/plan hash/주문 한도/today halt를 검증해 audit을 남기고, 운영자가 직접 실행할 `live-approve` 명령을 안내한다. Telegram 승인은 `final-confirm`을 대체하지 않으며 KIS POST를 실행하지 않는다.
- **승인 후 단축 실행 명령** (`execute-last-approved`, `execute-approved`) — Telegram 승인 audit을 운영자가 터미널에서 직접 실행하는 짧은 명령으로 기존 `live-approve` 실행 경로에 연결한다. 승인 없음/만료/hash mismatch/today halt/중복 실행은 차단하며, Telegram listener가 자동 실행하지 않는다.
- **일일 AI 투자 운영 흐름** (`daily-ai-trade-plan/report/status`) — 장 시작 전 AI 추천과 `live_order_plan_ai_latest.json`을 만들고, Telegram 승인과 `execute-last-approved` 이후 장 종료 리포트를 생성한다. plan/status/report 명령은 실주문을 실행하지 않는다.

## 현재 구현된 것

- `deepsignal.config.settings`: `DB_PATH`, `RSS_FEEDS_JSON`, `MARKET_SYMBOLS` / `MARKET_PERIOD` / `MARKET_INTERVAL`
- `deepsignal.storage`: `fetch_market_prices()`(기본 `timeframe='1d'` kw-only), `init_database()`, `insert_*`, `insert_collection_run()`
- `deepsignal.collector.news` / `deepsignal.collector.market`
- `deepsignal.analyzer.technical`: `TechnicalIndicator`, `TechnicalAnalyzer` (EMA12/26, RSI14, 규칙 기반 `trend_score`)
- `deepsignal.analyzer.sentiment`: `NewsSentimentResult`, `SentimentAnalyzer` (영어 키워드 규칙, 제목+요약만; OpenAI·FinBERT 없음)
- `deepsignal.scoring`: `SignalResult`, `SignalScorer` (기술 점수·`BUY_CANDIDATE` / `SELL_CANDIDATE` / `HOLD` / `INSUFFICIENT_DATA`; **`score_final`에서 `news_score`/`macro_score` 가중(기본 0.6/0.2/0.2, 누락 시 정규화)**; **`MacroScorer`·`MacroScoreResult`** 거시 규칙 v1; **주문·포지션 지시 아님**)
- `deepsignal.portfolio`: `PortfolioEngine`, `PortfolioAllocation`, `PortfolioSnapshot` (**`analyze-portfolio`**, 점수 기반 배분 v1, **실주문·리밸런싱 없음**)
- `deepsignal.collector.economic`: `EconomicIndicator`, `EconomicCollector` (yfinance **^VIX**, **DX-Y.NYB**, **^TNX**)
- `deepsignal.backtest`: `BacktestTrade`, `BacktestResult`, `BacktestEngine` (다음 거래일 종가 체결, 수수료·슬리피지 0; 선택 **`include_news`·`fetch_news_items_until`**)
- `deepsignal.paper_trading`: `PaperPosition`, `PaperTrade`, `PaperAccountSnapshot`, `PaperTradingEngine`, **`PaperRebalanceConfig`**, `paper_rebalance_config_from_namespace` (`run_step`·**`rebalance_portfolio`**, 리밸런스 시 **슬리피지·수수료·최소 거래·임계값**; `paper-step`은 수수료 0, **실주문 없음**)
- `deepsignal.reporting`: `report_service`, `console_formatter` (ASCII 표, **읽기 전용** 조회)
- `deepsignal.dashboard`: `dashboard_data`, `dashboard_app` (tkinter **조회 전용** 대시보드)
- `deepsignal.live_trading`: … **`runbook`**, **`runbook_guard`**, **`risk_guard`**, **`ops_dashboard`**, **`sell_plan`**, **`notification_center`**, **`daily_ops_summary`**, **`html_dashboard`**, **`report_cleanup`**, **`report_index`**, **`ops_dry_run`**, **`local_viewer`**, **`report_health`**, **`weekly_maintenance`**, **`weekly_report_bundle`**, **`checklist_generator`**, **`safety_audit`**, … (기본·safe-mode는 **실주문 없음**; **[실전-4]** `live-approve --execute` 가드 통과 시에만 **`order-cash` BUY**; **[실전-10~11]** runbook·`--require-pre-trade-runbook`; **[실전-12~13]** **`risk-check`**·**`post-trade-runbook` 내 risk-check** 손절/익절 **경고만**·SELL 없음, **[실전-24]** post-trade risk policy 옵션 전달; **[실전-14]** `ops-dashboard`; **[실전-15]** `sell-plan`; **[실전-16]** `notify-alerts`; **[실전-17]** `daily-ops-summary`; **[실전-18]** `html-dashboard`; **[실전-19]** `post-trade-runbook --with-summary`; **[실전-20]** `cleanup-reports`; **[실전-21]** `report-index`; **[실전-22]** `ops-dry-run`; **[실전-23]** `open-dashboard`; **[실전-25]** `report-health-check`; **[실전-26]** `weekly-maintenance`; **[실전-27]** `notify-alerts --include-maintenance`; **[실전-28]** `weekly-report-bundle`; **[실전-29]** `generate-checklists`; **[실전-30]** `safety-audit`)
- `deepsignal.pipelines.daily_pipeline`: `DailyPipelineResult` / `PipelineStepResult`, `collect_*`, **`collect_macro_to_db`**, `score_symbol_to_db`(**뉴스·거시 감성 포함**), `backtest_symbol_to_db`, `paper_step_to_db`, **`paper_rebalance_to_db`**, `run_daily_pipeline`, `print_daily_pipeline_summary` (CLI·`run-daily` 옵션·`logs/*.json`); **`main.py`는 `run-daily`·`live-approve`·`kis-check`·`live-order-status`·`live-sync-account`·`reconcile-live-account`·`live-order-guard-check`·`ops-dry-run`·`report-health-check`·`weekly-maintenance`·`weekly-report-bundle`가 결과에 따라 종료 코드 0/1이며, `generate-checklists`는 Markdown 생성 성공 시 0, `safety-audit`은 OK/WARNING 0·BLOCKED 1**
- `deepsignal.storage.database`: `insert_*`, **`insert_economic_indicators`**, **`fetch_latest_economic_indicators`**, **`fetch_latest_signals`**, **`fetch_latest_market_price`**, `fetch_recent_news_items`, **`fetch_news_items_until`**, `fetch_recent_signals` / `fetch_recent_backtests` / `fetch_latest_paper_snapshot` / `fetch_recent_paper_trades`, `get_paper_*`, **`save_real_positions`/`save_real_account_snapshot`/`load_latest_real_*`**, **`save_real_order_history`/`load_recent_real_orders`** (`paper_*`와 별도 테이블, [실전-6]~[실전-7]) 등
- `tests/`: … **`test_kis_config` / `test_kis_broker` / `test_main_kis_check`** …

## 사전 요구

- Python 3.11+ 권장 (macOS 포팅 기준)
- macOS에서는 프로젝트 루트 기준 실행을 전제로 합니다.
- `collect-news`, `collect-market`, **실패 알림 웹훅**은 **인터넷 연결**이 필요합니다.

## 설치

```bash
pip install -r requirements.txt
```

환경 변수 예시는 `.env.example`을 참고해 프로젝트 루트에 `.env`를 만듭니다. API 키는 **코드에 넣지 않습니다.**

### macOS 설치 및 검증

macOS 전용 의존성은 `requirements-macos.txt`를 사용합니다. **macOS에서는 반드시 프로젝트 `.venv`를 활성화하거나 `./.venv/bin/python`으로 실행하세요.** 시스템 `python3`는 버전이 낮거나 `pandas`/`pytest` 등 의존성이 없어 `python3 main.py` 직접 실행이 실패할 수 있습니다.

> macOS 운영 원칙: `python3 main.py` 직접 실행 대신 `source .venv/bin/activate` 후 `python main.py ...` 또는 `./.venv/bin/python main.py ...`를 사용합니다.

```bash
chmod +x scripts/setup_macos.sh
./scripts/setup_macos.sh

source .venv/bin/activate
./scripts/test_macos.sh
```

수동 설치가 필요하면 아래 순서로 진행합니다.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-macos.txt
python -m pytest -q
```

`.venv`를 활성화하지 않고 실행해야 한다면 아래처럼 명시적으로 `.venv`의 Python을 사용합니다.

```bash
./.venv/bin/python main.py --help
./.venv/bin/python main.py trading-session-check
```

GUI 대시보드는 Python 표준 라이브러리의 `tkinter`를 사용합니다. Homebrew Python 등에서 `_tkinter`가 빠진 경우 **일반 CLI에는 영향이 없지만 `python main.py dashboard`는 실패할 수 있습니다.** 대시보드가 필요할 때만 Tk 포함 Python 설치를 검토하세요.

macOS shell에서는 Windows의 `^` 대신 `\`로 줄바꿈합니다. 아래 예시는 **승인 검증/safe-mode 흐름** 예시이며, `--execute`를 포함하지 않습니다.

```bash
python main.py live-approve \
  --plan outputs/live_order_plan_test.json \
  --broker kis \
  --approved
```

실전 주문은 자동 실행 스크립트로 만들지 않습니다. 반드시 기존 `live-approve` CLI의 session guard, live execution guard, pre-trade runbook guard, duplicate order guard를 모두 통과하는 수동 절차에서만 수행합니다.

`KIS_ENV=live`는 실계좌 호스트를 의미합니다. macOS 운영 전 `.env`의 `KIS_ENV`, 계좌번호, 상품코드가 의도한 값인지 직접 확인하고, 키 값은 문서·채팅·커밋에 노출하지 마세요.

주문 전 조회/점검만 한 번에 실행하려면 다음 스크립트를 사용할 수 있습니다. 이 스크립트는 `live-approve --execute`를 호출하지 않습니다.

```bash
chmod +x scripts/run_live_precheck_macos.sh
./scripts/run_live_precheck_macos.sh
```

운영 문서:

- `docs/MACOS_OPERATION_GUIDE.md` — macOS 설치, 검증, 실전 전 조회/점검 순서
- `docs/MACOS_TROUBLESHOOTING.md` — pytest/pandas/Tk/KIS/session/guard 문제 해결
- `docs/REAL_TRADING_CHECKLIST.md` — 실전 주문 전후 수동 체크리스트

### 최종 운영 (macOS launchd 자동 시작)

**1회 설치** (프로젝트 루트, `.venv`·`.env`·Telegram 설정 완료 후):

```bash
source .venv/bin/activate
python main.py install-launchd \
  --broker kis \
  --network \
  --plan-time 09:05 \
  --report-time 15:40 \
  --max-order-value 300000 \
  --max-single-order-value 300000 \
  --max-total-order-value 300000 \
  --max-orders 1 \
  --output-dir outputs
```

동작:

- `~/Library/LaunchAgents/com.deepsignal.auto_runner.plist` 생성
- `launchctl`로 로그인 시 `daily-ai-auto-runner` 자동 시작 (`RunAtLoad`, `KeepAlive`)
- 로그: `logs/daily_ai_auto_runner.log`, `logs/daily_ai_auto_runner.error.log`
- **install 자체는 실주문을 실행하지 않음.** 실주문은 기존처럼 **Telegram [승인]** 후에만 실행됩니다.

**이후 운영:**

- Mac이 켜져 있으면 09:05 plan · Telegram 승인 요청 · 15:40 일일 요약이 자동 동작
- 운영자는 **Telegram [승인] / [거부]만** 누르면 됨

```bash
python main.py launchd-status
python main.py uninstall-launchd
```

수동으로 다시 load/unload 할 때:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.deepsignal.auto_runner.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.deepsignal.auto_runner.plist
```

#### launchctl running=False troubleshooting

`python main.py launchd-status`에서 `launchctl loaded: True`인데 `running: False`이고 로그 파일이 비어 있으면, launchd가 runner를 띄우기 전에 설정 오류로 종료한 경우가 많습니다.

| 증상 | 흔한 원인 | 조치 |
|------|-----------|------|
| `last exit code = 78` (EX_CONFIG) | plist 경로에 **`#`** 또는 공백 문제 | `install-launchd` 재실행 (`~/.deepsignal/project_root` symlink·로그 경로 사용) |
| stdout/stderr 로그 0 bytes | 위와 동일, 또는 `logs/` 없음 | `install-launchd`가 `logs/`·`outputs/` 생성 및 log touch 수행 |
| runner-test는 성공, launchd만 실패 | OS/launchd 경로 문제 | symlink 경로로 plist 재설치 후 `launchctl bootout` → `bootstrap` |
| runner-test import 실패 | venv/의존성 | `./scripts/setup_macos.sh`, `.venv/bin/python` 확인 |

진단 명령:

```bash
python main.py launchd-status
python main.py launchd-runner-test --network
launchctl print gui/$(id -u)/com.deepsignal.auto_runner
```

- **# path issue**: 프로젝트가 `/Volumes/.../#Project/...`처럼 `#`를 포함하면 launchd가 로그/작업 디렉터리를 열지 못해 EX_CONFIG(78)가 날 수 있습니다. `install-launchd`는 `#` 없는 symlink 경로로 plist를 씁니다.
- **bootstrap retry**: `install-launchd` 실패 시 출력의 bootstrap/load stderr를 확인한 뒤, `bootout` 후 다시 `install-launchd` 또는 수동 `bootstrap`을 실행합니다.
- **python path mismatch**: plist의 `ProgramArguments[0]`가 `.venv/bin/python`인지 `launchd-status` / `logs/launchd_runner_config.json`에서 확인합니다.

## 실행

프로젝트 루트에서:

```bash
source .venv/bin/activate
python main.py
```

성공 시 `DeepSignal initialized successfully`가 출력되고, 기본 설정이면 `data/deepsignal.db`가 생성됩니다.

### 뉴스 RSS 수집

```bash
python main.py collect-news
```

- 기본 RSS(Yahoo Finance 뉴스 RSS 인덱스, MarketWatch Top Stories)를 가져와 `news_items`에 저장하고, 소스별 결과를 `collection_runs`에 남깁니다.
- 피드를 바꾸려면 `.env`에 `RSS_FEEDS_JSON`을 설정합니다. 형식은 `.env.example` 참고.
- **공개 RSS·제목·요약·링크 중심**이며, Reuters·X(Twitter) 등 **로그인·유료·API 키가 필요한 소스는 포함하지 않습니다.**
- 수집 데이터는 **투자 참고·분석 입력용**이며, **투자 추천·자동매매가 아닙니다.**

### 뉴스 감성 요약 v1 (`analyze-news`)

```bash
python main.py analyze-news AAPL
```

- **먼저** `collect-news` 등으로 `news_items`에 행이 있어야 의미 있는 결과가 나옵니다. 없으면 `News Count: 0`과 함께 점수·신뢰도는 `-`로 표시됩니다.
- DB에서 최근 뉴스(기본 최대 100건)를 읽을 때, **`news_items.symbol`이 채워져 있으면 해당 심볼로 필터**하고, 비어 있는 행이 많을 수 있어 **`title`/`summary`에 티커 문자열이 포함되는지 `LIKE`로 보조 매칭**합니다.
- **영어 키워드 규칙**만 사용합니다(OpenAI·유료 API·FinBERT·뉴스 전문 수집 없음). 행별 감성은 키워드 출현 비교로 -1~+1에 매핑되고, **`news_score`는 평균×100**, **`confidence`는 비중립 비율**입니다.
- 출력은 **참고용 요약**이며, **`signals`에 자동 저장하지 않습니다.** (`SignalScorer.score_final`은 다음 단계에서 뉴스 점수를 넘기기 쉽도록 가중치만 준비되어 있습니다.)

### 시장 데이터(일봉 OHLCV) 수집

```bash
python main.py collect-market
```

- **yfinance**로 미국 티커 위주 일봉(`interval=1d`, 기본 `period=1mo`)을 받아 `market_prices`에 저장합니다.
- 수집 대상은 환경 변수 **`MARKET_SYMBOLS`**(쉼표 구분)로 바꿀 수 있습니다. 예: `MARKET_SYMBOLS=AAPL,MSFT,SPY`
- 기간·간격: `MARKET_PERIOD`(기본 `1mo`), `MARKET_INTERVAL`(기본 `1d`). **EMA26 등은 최소 약 26거래일 이상**이 있어야 의미 있으므로, 기술지표를 보려면 `MARKET_PERIOD`를 늘리는 것을 권장합니다.
- `collection_runs`에는 `collector_type=market_yfinance`, 성공/부분 실패/실패 요약이 기록됩니다.
- 데이터는 **차트·분석 입력용**이며, **투자 추천·자동매매가 아닙니다.**

### 거시 지표 스냅샷 수집 (`collect-macro`)

```bash
python main.py collect-macro
```

- **yfinance**로 미국 중심 거시 시리즈 **최근 일봉**에서 최신 값을 읽어 `economic_indicators`에 저장합니다(기본: **VIX `^VIX`**, **달러 인덱스 `DX-Y.NYB`**, **미국채 10년 `^TNX`**).
- **API 키·OpenAI·유료 거시 API 없음**. 시리즈별 실패 시 로그만 남기고 나머지는 계속합니다.
- `collection_runs`에 `collector_type=macro_yfinance` 기록이 남습니다.
- **지연·결측·티커 변경** 가능성은 `KNOWN_ISSUES.md`를 참고하세요.

### 거시 점수 요약 (`analyze-macro`)

```bash
python main.py analyze-macro
```

- DB **`economic_indicators` 최신 스냅샷**을 읽어 **`macro_score`(-100~100)**·**`market_regime`**(`risk_on` / `neutral` / `risk_off`)·**`confidence`**(채워진 지표 수 기반)와 한국어 **Reason**을 콘솔에 출력합니다.
- **단순 임계값 규칙 v1**이며 장중 이벤트·뉴스 속보는 반영하지 않습니다. 고도화는 `TODO.md` **[7순위]** 후속 항목을 참고하세요.

### 포트폴리오 배분 분석 (`analyze-portfolio`)

```bash
python main.py analyze-portfolio
```

- DB에서 **심볼당 최신 `signals`(strategy=`technical_v1`)** 를 읽고, **`analyze-macro`와 동일한 거시 스냅샷**으로 **`PortfolioEngine` v1** 목표 비중을 계산합니다.
- **필터**: `BUY_CANDIDATE`만, `final_score` > 0, `confidence` ≥ 0.2, `final_score` 상위 최대 **5종목**.
- **배분**: `final_score` 비율 기반(내부 비중), 종목당 **최대 40%**, 내부 **최소 5%** 미만은 제외 후 재정규화. **거시 `market_regime`에 따라 투자 가능 총액 비율**: `risk_off` ≤40%, `neutral` ≤70%, `risk_on` ≤95% — 나머지는 **현금 버퍼**로 표시됩니다.
- **기준 자본**: 최신 **`paper_account_snapshots`의 `equity`**(없으면 `cash`, 둘 다 없으면 **10,000** 가정). **실주문·브로커·자동 리밸런싱 없음**; `PortfolioSnapshot.raw["allocations_for_paper"]`만 이후 모의 엔진 연동용으로 남깁니다.

### 기술지표(RSI / EMA) 분석

```bash
python main.py analyze-technical AAPL
```

- **먼저** `collect-market`으로 해당 심볼 데이터가 `market_prices`에 있어야 합니다. 없으면 안내 메시지 후 종료합니다.
- SQLite에서 최근 최대 120봉(`timeframe=1d`, `source=yfinance`)을 읽어 **RSI(14), EMA(12/26), trend_score**를 계산하고 **최근 5거래일**을 표로 출력합니다.
- **`signals` 테이블에는 저장하지 않습니다.** (점수·후보 분류는 아래 `score-symbol` 사용)
- 출력은 **지표 계산 결과**일 뿐이며, **매수·매도 추천이나 투자 조언이 아닙니다.**

### 시그널 점수화 및 `signals` 저장

```bash
python main.py score-symbol AAPL
```

- **먼저** `collect-market`으로 해당 심볼이 `market_prices`에 있어야 합니다. 데이터가 없으면 `Insufficient technical data for ... Run collect-market with longer period.` 후 종료합니다.
- `news_items`에서 **`fetch_recent_news_items`(최대 100건)** 로 제목·요약을 읽어 **키워드 기반 뉴스 감성 점수**를 붙입니다. 관련 뉴스가 없거나 감성 산출이 불가하면 **`news_score`는 `NULL`** 이고 **기술 점수만으로 `final_score`** 를 계산합니다(기존과 동일). 뉴스 조회·분석 중 오류가 나도 **전체 점수화는 계속**하고 해당 건만 뉴스 없음으로 처리합니다.
- **`final_score`** 는 `SignalScorer.score_final(technical, news, macro)` 규칙을 따릅니다(기본 가중 **0.6 / 0.2 / 0.2**; `news_score`·`macro_score`가 없으면 해당 가중치를 제외하고 **정규화**). **`raw_json`** 에 `news_sentiment`·`macro` 요약이 포함됩니다.
- 콘솔에 **Technical Score**, **News Score**, **Macro Score**, **Final Score** 등을 출력합니다.
- 최근 봉 기준 **`action`** 과 근거를 **`signals`** 행으로 `INSERT OR IGNORE` 합니다. 같은 일자·전략명이 이미 있으면 **skipped**로 집계됩니다.
- 뉴스 감성은 **영어 키워드 규칙**이라 **정확도·언어 편향 제한**이 있습니다.
- **`action`은 주문 지시가 아니라 후보 분류 라벨**입니다 (`BUY_CANDIDATE` / `SELL_CANDIDATE` / `HOLD` / `INSUFFICIENT_DATA`). **자동매매·실제 체결과 연결되어 있지 않습니다.**

### 백테스트 v1·v2 (`backtest_results`)

```bash
python main.py backtest-symbol AAPL
python main.py backtest-symbol AAPL --include-news
```

- **먼저** `collect-market`으로 해당 심볼 일봉이 `market_prices`에 있어야 합니다. 없거나 유효 종가가 부족하면 `Insufficient market data for ... Run collect-market with longer period.` 후 종료합니다.
- `TechnicalAnalyzer` + `SignalScorer`를 날짜별로 재생하고, **시그널은 익일 종가에 체결**하는 단순 규칙으로 **가상 매수·매도·평가액**을 계산합니다. **수수료·슬리피지는 v1에서 0**입니다.
- **`--include-news` (v2)**: `news_items` 중 **`published_at`이 NULL이 아니고**, **`DATE(published_at) ≤ 해당 거래일`** 인 뉴스만(룩어헤드 없음) 모아 키워드 감성 `news_score`를 붙입니다. **`db_path`가 없으면 플래그만 켜도 기술만** 사용합니다. **`run-daily`의 백테스트 단계는 기본적으로 뉴스 미포함**입니다.
- **한계**: `published_at` 품질·**날짜만 비교**(장전/장중 미세 분리 없음), **심볼 LIKE 매칭**, 구간당 **최대 100건** 뉴스만 반영합니다.
- 결과는 **`backtest_results`**에 `INSERT OR IGNORE` (동일 `symbol`+`strategy_name`+`start_date`+`end_date`면 skipped).
- 출력에 **`Include News: True/False`** 가 표시됩니다. `raw_json`의 `parameters`에 `include_news`·`db_path_used`, `equity_curve` 각 행에(뉴스 모드일 때) `news_score`가 들어갈 수 있습니다.
- 백테스트는 **과거 데이터로 전략을 검토하는 도구**일 뿐이며, **미래 수익·실제 성과를 보장하지 않습니다.** **실전 자동매매가 아닙니다.**

### 모의투자 v1 (`paper_*` 테이블)

```bash
python main.py paper-step AAPL
```

- **먼저** `collect-market`으로 `market_prices`에 해당 심볼 일봉이 있어야 합니다. 없으면 `Insufficient market data for ...` 후 종료합니다.
- `TechnicalAnalyzer` + `SignalScorer`로 **최신 봉** 시그널을 만들고, **그 종가**를 체결가로 삼아 가상 **매수·매도·보유**를 처리합니다. **브로커 API·실제 주문은 없습니다.**
- **현금**은 `paper_account_snapshots`의 **가장 최근 행 `cash`**를 사용하고, 없으면 엔진 기본값(예: 10,000)입니다. **포지션**은 `paper_positions`(종목당 1행)입니다. **체결·스텝 요약**은 `paper_trades`, `paper_account_snapshots`에 쌓입니다.
- **백테스트와의 차이**: 백테스트는 **과거 구간 전체**를 리플레이해 성과표를 `backtest_results`에 남깁니다. 모의투자는 **지금 DB에 있는 최신 데이터 한 스텝**씩 가상 계좌를 갱신합니다.

### 포트폴리오 모의 리밸런싱 v1 (`paper-rebalance` / `run-daily --paper-rebalance`)

```bash
python main.py paper-rebalance
```

- **`analyze-portfolio`와 동일한 입력 경로**: `fetch_latest_signals` → 최신 거시(`MacroScorer`) → 최신 모의 스냅샷의 **`equity`(없으면 `cash`, 둘 다 없으면 10,000)** 를 기준 자본으로 **`PortfolioEngine.build_portfolio`** → 결과 **`raw["allocations_for_paper"]`** 를 읽어 목표 금액을 정합니다.
- **체결 모델**: 시장가는 DB **최신 종가**. **BUY** 체결가 = 시장가×(1+슬리피지), **SELL** 체결가 = 시장가×(1−슬리피지). 수수료는 거래대금×`commission_rate`. `paper_trades.price`에는 **체결가**를 저장하고, `raw_json`에 `market_price`·`executed_price`·`commission` 등을 넣습니다. **브로커·실주문 없음**.
- **스킵 규칙**: `abs(목표$−현재$)`가 **`min_trade_value` 미만**이거나 **`equity×rebalance_threshold` 미만**이면 해당 종목 리밸런스 거래를 하지 않습니다(리밸런스 시작 시점의 **총자산**으로 임계값 계산). 매수는 **현금 부족 시 수수료 포함** 가능 수량만.
- **기본값** (`PaperRebalanceConfig`): 수수료 **0.001**(0.1%), 슬리피지 **0.0005**(0.05%), 최소 거래 **10** USD, 임계값 **0.01**(자산의 1%). CLI: `--commission-rate`, `--slippage-rate`, `--min-trade-value`, `--rebalance-threshold`.

```bash
python main.py paper-rebalance --commission-rate 0.001 --slippage-rate 0.0005
```

- **목표에 없는 기존 포지션**: 기본 **`liquidate_missing=True`** 로 전량 매도 시도(가격 없으면 스킵). 위 스킵 규칙이 **매도에도** 적용될 수 있습니다.
- **`paper-step`과의 차이**: `paper-step`은 **한 종목**의 시그널로 한 스텝만 갱신합니다. `paper-rebalance`는 **포트폴리오 단위**로 한 번에 정렬합니다. 둘 다 **`paper_*`만** 쓰며 실계좌와 무관합니다.

### Live Order Plan ([실전-1], `live-plan`)

```bash
python main.py live-plan --capital 300000
```

- **`analyze-portfolio`와 동일한 신호·배분 경로**: DB `fetch_latest_signals`·거시(`MacroScorer`)·**`PortfolioEngine.build_portfolio`** 로 `raw["allocations_for_paper"]`를 얻고, **`--capital`에서 `cash_buffer_pct`를 뺀 금액**을 `total_cash`로 넘겨 목표 금액을 맞춥니다. **BUY 후보만** 주문안에 넣습니다 (**매도 자동화 없음**).
- **제약**: 종목당 `target_value ≤ capital × max_position_pct`, **`max_symbols`**, **`min_order_value`**, 수량은 **`floor(target_value / price)` 정수 주**만. 시세는 **`fetch_latest_market_price`**(지연 가능).
- **산출물**: `outputs/live_order_plan_YYYYMMDD.json`, `outputs/TODAY_LIVE_ORDER_PLAN.md`. **`status=PENDING_APPROVAL`**, **`approval_required=true`**, **`--dry-run` 기본 true**(이번 명령은 **항상** 브로커·실주문 없음).
- **옵션**: `--capital`(기본 300000), `--max-symbols`(기본 3), `--max-position-pct`(기본 0.25), `--min-order-value`(기본 10000), `--cash-buffer-pct`(기본 0.10), `--currency`(기본 **USD**, 미국 주식 중심), `--dry-run` / `--no-dry-run`.

### AI Live Trade Recommendation ([실전-37], `ai-live-recommend`)

```bash
python main.py ai-live-recommend --broker kis --output-dir outputs
python main.py ai-live-recommend --broker kis --network --output-dir outputs --capital-limit 100000 --max-recommendations 5
python main.py ai-live-recommend --broker kis --network --symbols 005930,000660
```

`ai-live-recommend`는 최신 signals, market price, macro score, 실계좌 snapshot, reconcile/risk/fill/safety audit/archive trend metadata를 참고해 `BUY` / `SELL` / `HOLD` / `REDUCE` / `INCREASE` / `SKIP` 추천을 생성한다. AI 자동 판단은 리포트와 승인 대기 주문안 생성까지만 수행하며, 실주문 자동 실행이 아니다.

산출물:

```text
outputs/AI_LIVE_TRADE_RECOMMENDATION.md
outputs/ai_live_trade_recommendation_YYYYMMDD_HHMMSS.json
outputs/live_order_plan_ai_YYYYMMDD_HHMMSS.json
```

`live_order_plan_ai_*.json`은 기존 `live-approve`가 읽을 수 있도록 `status=PENDING_APPROVAL`, `approval_required=true`, `dry_run=true`, BUY/LIMIT 중심 필드를 유지한다. 기본 주문안에는 `allowed_for_plan=true`인 `BUY`/`INCREASE`만 포함한다. `SELL`/`REDUCE` 후보는 Markdown/JSON 추천 리포트에 표시되지만, 현재 `live-approve` 승인 경로가 BUY/LIMIT만 검증하므로 주문안에서는 제외한다.

수동 승인 예시:

```bash
python main.py live-approve --broker kis --plan outputs/live_order_plan_ai_YYYYMMDD_HHMMSS.json --approved
python main.py live-approve --broker kis --plan outputs/live_order_plan_ai_YYYYMMDD_HHMMSS.json --approved --execute --allow-live-env --final-confirm I_UNDERSTAND_REAL_ORDER
```

**중요:** `ai-live-recommend`는 `live-approve`를 호출하지 않고, `--execute`를 호출하지 않으며, KIS `order-cash` POST를 수행하지 않는다. `--network`는 KIS 잔고/포지션 조회용 safe-mode 경로에만 사용한다. 시장가 주문, final-confirm 자동 주입, SELL 자동주문, cron/launchd/plist/alias/shell script 생성은 금지한다.

### Telegram MVP 검증 순서 ([긴급-MVP])

`.env`에 `DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN`, `DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID`를 설정한 뒤 아래 순서로 연결·승인 버튼을 검증한다.

```bash
python main.py telegram-test --send --message "DeepSignal 연결 테스트"

# 소액 실주문 검증용 테스트 plan (주문 1건, BUY/LIMIT)
python main.py generate-test-order-plan \
  --symbol 005930 \
  --quantity 1 \
  --limit-price 70000 \
  --output-dir outputs

python main.py telegram-approval-request \
  --plan outputs/test_live_order_plan.json \
  --send \
  --output-dir outputs

# Telegram에서 승인 버튼 클릭

# 승인 callback 확인 + 주문 실행 (소액 plan으로만, 실주문 경로):
python main.py execute-last-approved --output-dir outputs --wait-seconds 60
```

`telegram-test`는 기본 dry-run이며 `--send`일 때만 `sendMessage`를 호출한다. `execute-last-approved`는 Telegram `getUpdates`로 승인/중단 callback을 확인한 뒤 audit/state를 갱신하고 실행합니다. `telegram-approval-listen`은 debug/optional입니다.

### Telegram Approval Trading Workflow ([실전-44], `telegram-approval-*`)

```bash
python main.py telegram-test --message "DeepSignal 연결 테스트"
python main.py telegram-approval-request --plan outputs/live_order_plan_ai_YYYYMMDD_HHMMSS.json --output-dir outputs
python main.py telegram-approval-status --output-dir outputs
python main.py telegram-approval-request --plan outputs/live_order_plan_ai_YYYYMMDD_HHMMSS.json --send --output-dir outputs
python main.py telegram-approval-listen --output-dir outputs
```

`telegram-approval-request`는 plan exact bytes의 SHA-256, one-time token, 만료시각, 주문 건수/금액 한도를 `outputs/telegram_approval_request_*.json`과 `outputs/TELEGRAM_APPROVAL_REQUEST.md`에 기록한다. `--send`가 있을 때만 `DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN` / `DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID`로 Telegram `sendMessage`를 호출하며, 버튼은 `승인`, `중단`이다.

`telegram-approval-listen`은 Telegram callback/chat id, token, expiry, plan hash, max orders/value, today halt state를 다시 검증한다. `승인`이 유효하면 `approval_channel=telegram`, `plan_hash_verified=true`, `one_time_token_consumed=true`, `final_confirm_supplied=false`, `manual_live_approve_required=true`, `kis_post_called=false`를 `telegram_approval_audit_*.json`에 남기고 `execute-last-approved` 명령을 안내한다. Telegram listener는 KIS POST를 실행하지 않는다.

### Simplified Approved Execution ([실전-45], `execute-last-approved`)

```bash
python main.py execute-last-approved --output-dir outputs
python main.py execute-approved --request-id REQUEST_ID --output-dir outputs
```

`execute-last-approved`는 최신 Telegram approval audit을, `execute-approved`는 request-id(token) 기반 approval을 찾는다. 승인 상태, token consumed, expiry, today halt, plan hash, plan file 존재, 주문 건수/금액 한도, 이미 실행 완료 여부를 모두 확인한 뒤 기존 `execute_live_order_plan()` 경로를 재사용한다. 운영자는 짧은 명령만 입력하지만, 내부에서는 기존 live guard, KIS live env, session, pre-trade runbook, duplicate order guard를 그대로 통과해야 한다.

산출물은 `outputs/EXECUTE_APPROVED_AUDIT.md`, `outputs/execute_approved_audit_YYYYMMDD_HHMMSS.json`이며 Telegram approval audit/state, plan hash, validation 결과, execution result, 연결된 `live_approval_audit_*.json` 경로를 포함한다. `--send`를 붙인 경우에만 실행 결과 Telegram 전송을 시도한다.

### Daily AI Trading Workflow ([실전-46])

```bash
python main.py daily-ai-trade-plan --broker kis --network --output-dir outputs
python main.py telegram-approval-request --plan outputs/live_order_plan_ai_latest.json --send --output-dir outputs
python main.py execute-last-approved --output-dir outputs
python main.py daily-ai-trade-report --broker kis --network --output-dir outputs
python main.py daily-ai-status --output-dir outputs
```

`daily-ai-trade-plan`은 trading/session/KIS/account/reconcile/safety context를 기록하고 `ai-live-recommend`를 실행해 `AI_DAILY_TRADE_PLAN.md`, `ai_daily_trade_plan_*.json`, `live_order_plan_ai_latest.json`을 만든다. 이 명령은 추천/조회/리포트 생성 전용이며 `live-approve`, `execute-last-approved`, KIS `order-cash` POST를 호출하지 않는다.

`daily-ai-trade-report`는 최신 AI 추천, Telegram 승인, approved execution, live approval, fill/account/reconcile/risk/safety/archive 파일을 요약해 `AI_DAILY_TRADE_REPORT.md`와 `ai_daily_trade_report_*.json`을 만든다. `daily-ai-status`는 plan 생성, Telegram 승인, 실행, 체결 확인, 리포트 생성 여부와 다음에 실행할 명령을 `AI_DAILY_STATUS.md` / `ai_daily_status_*.json`에 기록한다.

### Daily AI Workflow Timestamp Normalization ([실전-49])

Daily AI workflow JSON/Markdown 산출물은 `Asia/Seoul` 기준 timezone-aware `generated_at`을 기록한다.

```json
{
  "generated_at": "2026-05-19T10:30:00+09:00",
  "generated_date": "2026-05-19",
  "timezone": "Asia/Seoul"
}
```

- freshness 판단은 `generated_at` 우선, 없으면 `mtime fallback`
- `daily-ai-status` / `safety-audit` / `REPORT_INDEX`는 freshness source(`generated_at` / `mtime fallback`)를 표시할 수 있다
- Markdown 상단에 `생성 시각` / `기준 날짜` / `타임존`을 명시한다

### Daily AI Workflow Freshness Validation ([실전-48])

전일 `live_order_plan_ai_latest.json`을 오늘 실수로 실행하는 위험을 줄이기 위해, daily workflow 산출물이 **오늘(Asia/Seoul) 생성되었는지** 로컬 파일 metadata/JSON timestamp만으로 검증한다. KIS, Telegram, live-approve, `--execute` 호출은 없다.

```bash
python main.py daily-ai-status --output-dir outputs
python main.py daily-ai-status --output-dir outputs --freshness-date 2026-05-19
python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db
python main.py safety-audit --output-dir outputs --freshness-date 2026-05-19
```

- `daily-ai-status` / `safety-audit`는 plan, latest order plan, approval, execution, report freshness를 표시한다.
- 오래된 plan 또는 `live_order_plan_ai_latest.json`은 `execute-last-approved`에서 차단되며 `execute_approved_audit_*.json`에 stale reason이 기록된다.
- Telegram 승인이 fresh라도 plan 자체가 stale이면 실행되지 않는다. **오늘 생성된 plan만** 실행해야 한다.

### Daily AI Dashboard Integration ([실전-47])

`report-index`, `open-dashboard`, `archive-viewer`, `safety-audit`는 `AI_DAILY_*` 산출물을 읽기 전용으로 표시한다.

```bash
python main.py daily-ai-status --output-dir outputs
python main.py safety-audit --output-dir outputs
python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive
python main.py report-index --output-dir outputs --archive-dir outputs/archive
python main.py open-dashboard --output-dir outputs
```

`REPORT_INDEX.html` / `REPORT_INDEX.md`에는 `AI 일일 매매 운영` 섹션이 추가되어 계획/최신 주문안/리포트 freshness(최신·오래됨·없음), Telegram 승인 요청, 실행 audit, 장 종료 리포트, 다음 실행 권장 명령을 보여준다. `open-dashboard` 콘솔에는 freshness 요약과 `AI_DAILY_*` 링크가 표시된다. `archive-viewer`는 AI daily plan/report/status와 latest AI order plan을 별도 report type으로 분류한다. `safety-audit`는 freshness 섹션과 stale plan warning/blocking을 기록하되, 주문·네트워크·Telegram API·KIS 호출은 하지 않는다.

### AI Recommendation Validation ([실전-38]~[실전-43], `validate-ai-recommendation`)

```bash
python main.py validate-ai-recommendation --output-dir outputs
python main.py validate-ai-recommendation --symbols AAPL,MSFT,NVDA --initial-cash 1000000 --include-sell-reduce --benchmark --risk-free-rate 0.0 --output-dir outputs
python main.py validate-ai-recommendation --symbols AAPL,MSFT,NVDA --initial-cash 1000000 --include-sell-reduce --commission-rate 0.001 --tax-rate 0.0 --slippage-bps 5 --min-order-value 10000 --max-order-value 100000 --currency KRW --output-dir outputs
python main.py validate-ai-recommendation --symbols AAPL,MSFT,NVDA --sector-map config/sector_map.json --max-symbol-weight 0.35 --max-sector-weight 0.50 --correlation-threshold 0.80 --correlation-lookback-days 60 --output-dir outputs
python main.py validate-ai-recommendation --symbols AAPL,MSFT,NVDA --liquidity-limit-pct 0.01 --min-daily-volume 100000 --min-daily-value 100000000 --volume-lookback-days 20 --output-dir outputs
python main.py validate-ai-recommendation --symbols AAPL,MSFT,NVDA --base-currency KRW --default-symbol-currency USD --fallback-fx USD=1350,KRW=1 --output-dir outputs
python main.py validate-ai-recommendation --symbols AAPL,MSFT,NVDA --no-costs --output-dir outputs
python main.py validate-ai-recommendation --start-date 2026-01-01 --end-date 2026-05-17
```

`validate-ai-recommendation`은 `ai-live-recommend` v1 추천 정책을 로컬 DB의 `market_prices` / `signals` / macro metadata로 날짜별 재생해 검증한다. 가상 포트폴리오는 메모리 안에서만 갱신하며, 실계좌 테이블이나 `paper_*` 운영 테이블을 수정하지 않는다.

산출물:

```text
outputs/AI_RECOMMENDATION_VALIDATION.md
outputs/ai_recommendation_validation_YYYYMMDD_HHMMSS.json
outputs/AI_RECOMMENDATION_VALIDATION_TRADES.csv
```

리포트는 검증 기간, 대상 종목, 초기/최종 자금, 총 수익률, 최대 낙폭, 거래 수, 승률, 평균 수익/손실, action별/symbol별 성과, risk_off 거래 수를 표시한다. 기본은 BUY/INCREASE만 가상 체결하며, `--include-sell-reduce`를 명시한 경우에만 SELL/REDUCE 추천도 in-memory 포트폴리오에 반영한다.

[실전-39]부터 advanced metrics와 benchmark 비교를 포함한다. `advanced_metrics`에는 `annualized_return_pct`, `volatility_pct`, `sharpe_ratio`, `profit_factor`, `expectancy`, 연속 손익, 노출 비율, 회전율, 평균 보유일, best/worst trade, action/symbol별 PnL이 들어간다. Sharpe는 무위험 수익률을 기본 `0.0`으로 계산하며 `--risk-free-rate`로 조정할 수 있다.

Benchmark는 동일 기간 대상 symbols를 동일비중 buy-and-hold한 결과다. 여러 종목이면 초기 자금을 동일하게 나눠 첫 가격으로 매수하고 마지막 가격으로 평가한다. `benchmark_return_pct`, `benchmark_max_drawdown_pct`, `excess_return_pct`, `strategy_vs_benchmark`를 통해 전략이 단순 보유 대비 나았는지 확인한다. 데이터가 부족하면 benchmark는 unavailable로 표시한다.

[실전-40]부터 기본으로 비용 모델을 적용한다. 기본 가정은 수수료율 0.1%, 세금 0%, 슬리피지 5bps, 최소 주문금액 10,000 KRW이며, 최대 주문금액은 옵션을 줄 때만 적용한다. `--no-costs`를 사용하면 비용 0, 최소 주문금액 0으로 기존 방식에 가깝게 검증한다. JSON/Markdown/CSV에는 `gross_return_pct`, `net_return_pct`, 총 수수료/세금/슬리피지, `cost_drag_pct`, 비용으로 스킵된 주문 metadata가 포함된다.

[실전-41]부터 portfolio risk validation을 항상 수행한다. 최종 in-memory 포지션 기준 `symbol_weights`, `sector_weights`, 초과 비중 종목/섹터, 고상관 종목쌍, `concentration_score`, `diversification_score`를 JSON/Markdown과 `AI_RECOMMENDATION_PORTFOLIO_RISK.csv`에 기록한다. 섹터는 선택 로컬 파일 `--sector-map config/sector_map.json`만 사용하며 파일이 없으면 `UNKNOWN`으로 처리한다. 상관관계는 로컬 `market_prices` 일별 수익률과 `--correlation-lookback-days`, `--correlation-threshold` 기준으로 계산하며 yfinance info/API 조회는 하지 않는다.

[실전-42]부터 liquidity constraint validation을 선택 적용할 수 있다. `--liquidity-limit-pct`는 최근 평균 거래량의 지정 비율까지만 주문 수량을 허용하고, `--min-daily-volume` / `--min-daily-value`는 평균 거래량·거래대금 미달 종목을 스킵한다. 평균 계산 기간은 `--volume-lookback-days`로 조정한다. 기본은 제한 없음이므로 기존 동작을 유지하며, 지정 시 JSON의 `liquidity_model`, Markdown의 `유동성 제한 검증`, trades CSV의 liquidity 컬럼에 축소/스킵/경고 metadata가 기록된다.

[실전-43]부터 FX / currency-aware validation을 지원한다. `--base-currency`, `--default-symbol-currency`, `--fx-rates`, `--symbol-currency-map`, `--fallback-fx`로 종목 통화와 날짜별 환율을 로컬에서 지정한다. 예: `config/fx_rates.json`은 `{"base_currency":"KRW","rates":{"2026-05-15":{"USD":1350,"KRW":1}}}`, `config/symbol_currency_map.json`은 `{"AAPL":"USD","005930":"KRW"}` 형식이다. 외부 환율 API나 yfinance info는 호출하지 않으며, JSON `fx_model`, Markdown `통화 / 환율 검증`, trades CSV FX 컬럼에 환산 metadata가 기록된다.

**중요:** 이 명령은 수익 보장 기능이 아니다. KIS 호출, `live-approve` 호출, `--execute`, 실계좌 주문, KIS POST, 네트워크 주문, DB 실계좌 테이블 수정, `paper_*` 운영 테이블 수정을 수행하지 않는다. 실거래 적용 전에는 충분한 기간/종목군으로 검증하고, 최종 주문은 여전히 수동 승인 경로만 사용한다.

### Live Approve ([실전-2]~[실전-4], `live-approve`)

```bash
python main.py live-approve --plan outputs/live_order_plan_20260515.json --approved
```

- **`live-plan`으로 만든 JSON**과 호환: **`status=PENDING_APPROVAL`**, **`approval_required=true`**, 주문은 **BUY만**, **`estimated_qty`·`estimated_price`·`estimated_order_value` > 0**.
- **`--broker dry-run`**(기본): **`DryRunBroker`** → **`DRY_RUN_ACCEPTED`**, **`DRY_RUN_COMPLETED`** 시 종료 **0**.
- **`--broker kis`**: **`.env`/`KIS_*`** 로 **`KISBroker`** 생성. **`--execute` 없음**: `place_order(..., execute=False)` → **`KIS_SAFE_MODE_BLOCKED`** (**`order-cash` POST 없음**). 감사 로그 **`broker`=`KISBroker`**, **`KIS_SAFE_MODE_COMPLETED`** 시 종료 **0**. **국내 6자리 숫자 종목·CANO 8자리·상품코드 2자리**가 필요하며, 기본 `live-plan`(미국 티커)과 **불일치할 수 있음**.
- **`--approved`**: 없으면 거부(종료 **1**).
- **[실전-4] 단발 실매수 (`--execute`)**: 아래를 **모두** 만족할 때만 **`order-cash` LIMIT BUY** 1회(또는 **`--max-orders`**)·**자동 재시도 없음**. 그렇지 않으면 **`LIVE_EXECUTION_BLOCKED`** 등으로 **1**.
  - **`--broker kis`**, **`--approved`**, **`--execute`**, **`KIS_ENV=live`**, **`--allow-live-env`**, **`--final-confirm I_UNDERSTAND_REAL_ORDER`**
  - 기본 한도: **`--max-total-order-value` 100000**, **`--max-single-order-value` 50000**, **`--max-orders` 1** (필요 시 CLI로만 상향)
  - 선택 **`--allow-symbol 005930`** (여러 번 지정 가능) 시 **화이트리스트 외 종목 차단**
  - **`KIS_ENV=paper`** 이면 가드에서 실주문 차단(모의 전용 호스트와 실주문 분리)
- **`--dry-run` / `--no-dry-run`**: 기본 **`--dry-run`**. **`--no-dry-run` 거부(종료 1)** (`[실전-4]`까지도 실주문은 **`--execute`** 가드 경로만).
- **감사 로그**: `outputs/live_approval_audit_YYYYMMDD_HHMMSS.json`(또는 **`--output-dir`**; **`--execute`** 시 실행 전에 경로를 콘솔에 표시). 필드 예: **`execute`**, **`final_confirm_matched`**, **`live_guard_passed`**, **`kis_env`**, **`actual_order_attempted`**, **`results[].raw`** 등.
- **종료 코드**: **`DRY_RUN_COMPLETED`**, **`KIS_SAFE_MODE_COMPLETED`**, 또는 **`KIS_LIVE_ORDER_COMPLETED`**([실전-4] 실주문 성공)이면 **0**, 그 외 **1**.

```text
python main.py live-approve ^
  --plan outputs/live_order_plan_20260515.json ^
  --broker kis ^
  --approved ^
  --execute ^
  --allow-live-env ^
  --final-confirm I_UNDERSTAND_REAL_ORDER ^
  --max-total-order-value 100000 ^
  --max-single-order-value 50000 ^
  --max-orders 1 ^
  --allow-symbol 005930 ^
  --output-dir outputs
```

### Live order status ([실전-5], `live-order-status`)

`live-approve` 감사 로그(`live_approval_audit_*.json`)와 **연결해** 주문번호(ODNO 등)를 추출하고, 선택적으로 KIS **일별주문체결조회**(`inquire-daily-ccld`)를 호출합니다.

- **기본( `--network` 없음 )**: 감사 JSON 파싱·`outputs/live_order_status_*.json` 및 **`outputs/LIVE_ORDER_STATUS.md`** 만 생성. **외부 HTTP 없음**.
- **`--network`**: OAuth 후 **`inquire-daily-ccld`** GET. **주문 API(`order-cash`)는 호출하지 않음** (신규 주문 실행 기능을 늘리지 않음).
- **주문 접수 성공과 체결 완료는 다름** (장외 시간·호가 등). **증권사 앱·웹 체결과 반드시 대조**할 것.
- 옵션: `--order-id`, `--symbol`, `--start-date` / `--end-date`(YYYYMMDD), `--output-dir`.

```bash
python main.py live-order-status --audit outputs/live_approval_audit_20260515_120000.json
python main.py live-order-status --audit outputs/live_approval_audit_20260515_120000.json --network
```

### Live account snapshot ([실전-5]~[실전-6], `live-sync-account`)

KIS **잔고조회**(`inquire-balance`)로 보유 종목·현금 요약을 **`outputs/live_account_snapshot_*.json`** 및 **`outputs/LIVE_ACCOUNT_SNAPSHOT.md`** 에 저장합니다. **`paper_*` 테이블에는 쓰지 않습니다.**

- **`--network` 필수** (실호출 없이는 의미 없음).
- **`--broker kis`** 만 지원.
- **[실전-6]** 기본으로 동일 스냅샷을 SQLite **`real_positions`**·**`real_account_snapshots`** 에도 저장합니다(조회 결과만 기록, 주문 실행 없음). **`--no-save-db`** 로 DB 저장을 끌 수 있습니다.

```bash
python main.py live-sync-account --broker kis --network --output-dir outputs
python main.py live-sync-account --broker kis --network --debug-raw --output-dir outputs
python main.py live-sync-account --broker kis --network --output-dir outputs --no-save-db
```

`--debug-raw`는 KIS `inquire-balance` 원문 전체를 출력하지 않고, `output1`/`output2`의 행 수와 키 목록만 콘솔 및 `outputs/kis_debug_account_*.json`에 저장합니다. 계좌번호·토큰·키·원문 값은 저장하지 않습니다.

#### 실계좌 스냅샷 DB 테이블 (`real_*`, [실전-6])

| 테이블 | 용도 |
|--------|------|
| `real_positions` | 브로커 조회 시점별 보유 종목·수량·평단·평가액 등(`snapshot_time`, `broker`, `raw_json` 포함). |
| `real_account_snapshots` | 동일 시점의 현금·출금가능·총평가·추정자산 등 요약(`raw_json` 포함). |

**`paper_*`와 절대 혼합하지 않습니다.** 운영 전 **`live-sync-account`** 로 DB를 맞춘 뒤 아래 reconcile을 권장합니다.

### Reconcile live account ([실전-6], `reconcile-live-account`)

KIS **`get_positions()`** 와 DB **`load_latest_real_positions`**(최신 `snapshot_time`)를 비교합니다. 불일치 시 **`success=false`**·프로세스 **종료 코드 1**·경고 문구(자동 주문 중단 권고·수량 불일치 시 duplicate/stale-order 위험 안내)를 출력합니다.

- **`--network` 필수**, **`--broker kis`**.
- 산출물: **`outputs/reconcile_live_account_YYYYMMDD_HHMMSS.json`**, **`outputs/RECONCILE_LIVE_ACCOUNT.md`**.
- **실주문·SELL·시장가·자동 반복 없음** (조회·비교만).

```bash
python main.py reconcile-live-account --broker kis --network --output-dir outputs
```

**운영 권장:** 새 자동 주문·배치 전에 reconcile로 일치 여부를 확인하고, mismatch가 있으면 증권사 앱과 대조한 뒤 **`live-sync-account`** 로 DB를 갱신할 것.

`live-sync-account` 직후 포지션이 `(none)`인데 reconcile에서 과거 포지션이 보이면, 최신 빈 계좌 스냅샷과 DB 포지션 기준이 어긋난 상태일 수 있습니다. 현재 DB 조회는 최신 `real_account_snapshots.snapshot_time` 기준의 `real_positions`만 사용하며, 최신 스냅샷에 포지션이 0개면 빈 목록으로 취급합니다.

```bash
python main.py reconcile-live-account --broker kis --network --debug-raw --output-dir outputs
```

`--debug-raw`로 KIS 잔고조회 `output1`/`output2`의 행 수와 키 목록을 확인해, 브로커 응답 자체가 달라졌는지 또는 파싱/DB 저장 문제인지 구분합니다.

### Duplicate order protection ([실전-7], `live-order-guard-check` / `live-approve`)

실주문(`live-approve --execute --broker kis`) 직전에 **`order_guard`** 가 다음을 검사합니다. **차단 시 `LIVE_ORDER_BLOCKED_BY_GUARD`·KIS `order-cash` POST 없음.**

| 검사 | 동작 |
|------|------|
| 최근 동일 종목 BUY | 기본 30분 내 `real_order_history` 존재 시 차단 |
| 대기 주문 상태 | `PENDING` / `SUBMITTED` / `UNKNOWN` / `KIS_ORDER_SUBMITTED` 등 |
| reconcile mismatch | `LATEST_RECONCILE_STATE.json` 또는 최신 reconcile 리포트 `success=false` |
| stale snapshot | `real_account_snapshots` 최신 시각이 기본 10분 초과 |
| 동일 qty·limit 반복 | 최근 동일 파라미터 재주문 위험 |
| partial fill 추정 | `filled_quantity < quantity` 또는 `remaining_quantity > 0` |

**운영 권장 순서:** `live-sync-account --network` → `reconcile-live-account --network` → (필요 시) `live-order-guard-check` → `live-approve --execute`.

```bash
python main.py live-order-guard-check --symbol 005930 --broker kis --quantity 1 --limit-price 70000 --output-dir outputs
```

- **SAFE** (종료 0): 차단 사유 없음.
- **BLOCKED** (종료 1): 위험 감지 — **자동 재주문 금지**, 증권사 앱·감사 로그 확인.

실주문 시도 후(성공·실패 모두) **`real_order_history`** 에 broker·symbol·qty·limit·status·order_id·audit 경로가 저장됩니다(`paper_*`와 분리).

### Order vs fill · partial fill ([실전-8], `live-order-status` / `live-fill-summary`)

| 개념 | 설명 |
|------|------|
| **Order (주문)** | `live-approve`로 접수된 1건 — `real_order_history`·감사 로그의 `KIS_ORDER_SUBMITTED` 등 |
| **Fill (체결)** | 브로커에서 실제로 체결된 분량 — `real_fill_history`에 행 단위 저장 |

**`live-order-status --network`** 시 KIS `inquire-daily-ccld` 응답(`output1`/`output2`)에서 체결을 추출해 **`real_fill_history`** 에 dedupe 저장하고, 주문별 **partial fill 요약**(filled / remaining / avg fill price)을 `outputs/live_fill_summary_*.json`·`LIVE_FILL_SUMMARY.md`에 남깁니다.

**Partial fill 위험:** 주문 수량 대비 체결이 남아 있으면(`remaining_quantity > 0`) **[실전-7] order guard**가 **HIGH**로 차단합니다(`partial_fill_open`). 운영 시 체결 완료·잔량 확인 후 재주문하세요.

```bash
python main.py live-order-status --audit outputs/live_approval_audit_....json --network
python main.py live-fill-summary --order-id 12345
python main.py live-fill-summary --audit outputs/live_approval_audit_....json
```

#### `real_order_history` ([실전-7])

| 컬럼 | 용도 |
|------|------|
| `created_at`, `broker`, `symbol`, `side`, `quantity` | 주문 시점·종목·방향·수량 |
| `limit_price`, `estimated_order_value`, `status`, `order_id` | 지정가·금액·상태·브로커 주문번호 |
| `audit_path`, `raw_json` | `live_approval_audit_*.json` 연결·원본 payload |

#### `real_fill_history` ([실전-8])

| 컬럼 | 용도 |
|------|------|
| `order_id`, `fill_id` | 주문·체결 식별 (`fill_id` 없으면 synthetic key) |
| `fill_quantity`, `fill_price`, `fill_value` | 체결 수량·단가·금액 |
| `fill_timestamp`, `raw_json` | 체결 시각·KIS 원문 행 |

dedupe: `fill_id` 또는 `(order_id + fill_timestamp + qty + price)`.

### Trading session guard ([실전-9], `trading-session-check` / `live-approve`)

국내 주식 **정규장 시간**만 실주문(`live-approve --execute`)을 허용합니다. 기본 정책은 보수적으로 **차단**입니다.

| 조건 | 기본 동작 |
|------|-----------|
| 평일 **09:00~15:30** (Asia/Seoul) | OPEN |
| 장 시작 전 / 장 마감 후 | CLOSED |
| 토·일 | CLOSED |
| `DEEPSIGNAL_MARKET_HOLIDAYS`에 오늘 날짜 | CLOSED |

환경 변수(선택): `DEEPSIGNAL_MARKET`, `DEEPSIGNAL_MARKET_TIMEZONE`, `DEEPSIGNAL_MARKET_OPEN`, `DEEPSIGNAL_MARKET_CLOSE`, `DEEPSIGNAL_MARKET_HOLIDAYS`, `DEEPSIGNAL_ALLOW_AFTER_HOURS`. 공휴일 **자동 API 연동 없음** — 수동 리스트만.

```bash
python main.py trading-session-check
python main.py trading-session-check --now 2026-05-15T10:00:00+09:00
python main.py trading-session-check --now 2026-05-15T08:00:00+09:00
python main.py trading-session-check --holiday 2026-05-15
```

장외·휴일에 `live-approve --execute` 시 **`LIVE_EXECUTION_BLOCKED_BY_SESSION`** ·KIS `order-cash` POST 없음. 감사 로그에 `trading_session` / `trading_session_open` / `trading_session_reason` 기록.

### Trading runbook ([실전-10], `pre-trade-runbook` / `post-trade-runbook`)

실주문 전후 **운영 절차를 한 번에** 실행하는 오케스트레이션입니다. **새 주문 기능을 추가하지 않으며**, 기존 session·sync·reconcile·guard·plan 검증·status·fill 로직만 순서대로 호출합니다. **SELL·시장가·자동 반복·취소 없음.**

#### 추천 실전 운영 순서

1. `live-plan` — 주문안 JSON 생성  
2. **`pre-trade-runbook --network`** — 아래 체크리스트 자동 실행 → **`PRE_TRADE_READY`** 확인  
3. `live-approve --broker kis --approved --execute …` — (기존 가드·세션·duplicate guard 그대로)  
4. **`post-trade-runbook --network --with-summary --audit …`** — 체결·스냅샷·reconcile·risk-check 후 ops/sell/daily/html 리포트까지 생성  
5. **manual review** — 경고 시 `RISK_ALERT.md`·`SELL_PLAN.md`·`DAILY_OPS_SUMMARY.md`·`OPS_DASHBOARD.html` 검토 (자동 SELL 없음)  

#### Pre-trade (`pre-trade-runbook`)

| Step | 내용 | 실패 시 |
|------|------|---------|
| 1 | `trading-session-check` 동등 | **즉시 중단** |
| 2 | `live-sync-account --network` | 중단 |
| 3 | `reconcile-live-account --network` | mismatch 시 중단 |
| 4 | `live-order-guard-check` | blocked 시 중단 |
| 5 | plan validation (승인·BUY·화이트리스트·금액) | 중단 |
| 6 | summary | `PRE_TRADE_READY` 또는 `PRE_TRADE_BLOCKED` |

```bash
python main.py pre-trade-runbook ^
  --broker kis ^
  --network ^
  --plan outputs/live_order_plan_20260515.json ^
  --symbol 005930 ^
  --quantity 1 ^
  --limit-price 70000 ^
  --allow-symbol 005930 ^
  --output-dir outputs
```

산출물: **`outputs/pre_trade_runbook_YYYYMMDD_HHMMSS.json`**, **`outputs/PRE_TRADE_RUNBOOK.md`**

#### Post-trade (`post-trade-runbook`)

| Step | 내용 |
|------|------|
| 1 | `live-order-status --network` (audit/order-id) |
| 2 | `live-fill-summary` |
| 3 | `live-sync-account --network` |
| 4 | `reconcile-live-account --network` |
| 5 | **`risk-check` 동등** — `real_positions` 손절/익절 경고·`RISK_ALERT.md` |
| 6 | summary → `POST_TRADE_OK` / `POST_TRADE_WARNING` / **`POST_TRADE_RISK_ALERT`** / `POST_TRADE_BLOCKED` |
| 7~10 | `--with-summary`일 때 `ops-dashboard` → `sell-plan` → `daily-ops-summary` → `html-dashboard` |

```bash
python main.py post-trade-runbook ^
  --broker kis ^
  --network ^
  --audit outputs/live_approval_audit_20260515_120000.json ^
  --with-summary ^
  --output-dir outputs
```

`post-trade-runbook`의 risk 단계는 standalone `risk-check`와 같은 임계값 옵션을 사용할 수 있습니다. 지정하지 않으면 기존 기본값(손절 -7%, 익절 +15%, 손실 경고 -3%, 이익 경고 +10%)을 유지합니다.

```bash
python main.py post-trade-runbook ^
  --broker kis ^
  --network ^
  --audit outputs/live_approval_audit_20260515_120000.json ^
  --stop-loss-pct -0.05 ^
  --take-profit-pct 0.12 ^
  --warn-loss-pct -0.02 ^
  --warn-profit-pct 0.08 ^
  --output-dir outputs
```

산출물: **`outputs/post_trade_runbook_YYYYMMDD_HHMMSS.json`**, **`outputs/POST_TRADE_RUNBOOK.md`**

post-trade runbook JSON/Markdown에는 사용한 risk policy가 기록됩니다.

```json
{
  "risk_policy": {
    "stop_loss_pct": -0.05,
    "take_profit_pct": 0.12,
    "warn_loss_pct": -0.02,
    "warn_profit_pct": 0.08
  }
}
```

`--with-summary` 또는 `--full-report`를 붙이면 post-trade runbook JSON/Markdown의 `summary.generated_reports`에 아래 경로가 포함됩니다. 일부 리포트 생성 실패는 critical failure가 아니라 warning으로 기록되며, 기존 reconcile mismatch·risk alert 정책은 그대로 유지됩니다.

```json
{
  "risk_report": "outputs/risk_alert_YYYYMMDD_HHMMSS.json",
  "ops_dashboard_json": "outputs/ops_dashboard_YYYYMMDD_HHMMSS.json",
  "ops_dashboard_md": "outputs/OPS_DASHBOARD.md",
  "sell_plan_json": "outputs/sell_plan_YYYYMMDD_HHMMSS.json",
  "sell_plan_md": "outputs/SELL_PLAN.md",
  "daily_ops_summary_json": "outputs/daily_ops_summary_YYYYMMDD_HHMMSS.json",
  "daily_ops_summary_md": "outputs/DAILY_OPS_SUMMARY.md",
  "html_dashboard": "outputs/OPS_DASHBOARD.html"
}
```

**Stop policy (pre-trade):** 세션 closed · reconcile mismatch · duplicate guard blocked · partial fill open · stale snapshot · plan 없음/승인 불가 · SELL · (plan에 시장가 필드 없음 — BUY·LIMIT만 허용)

#### `live-approve --require-pre-trade-runbook` ([실전-11])

최근 **`PRE_TRADE_READY`** pre-trade runbook 리포트가 없거나 만료·plan/symbol/qty/limit 불일치 시 **`LIVE_EXECUTION_BLOCKED_BY_RUNBOOK`** ·KIS POST 없음.

| 옵션 | 설명 |
|------|------|
| `--require-pre-trade-runbook` | 검증 활성화 (`--execute`·`--broker kis` 시에만 적용) |
| `--pre-trade-runbook PATH` | 특정 JSON만 검증 (미지정 시 `output-dir` 최신 `pre_trade_runbook_*.json`) |
| `--pre-trade-runbook-max-age-minutes` | TTL(분, 기본 **10**) |

```bash
python main.py pre-trade-runbook --broker kis --network --plan outputs/live_order_plan.json --symbol 005930 ...

python main.py live-approve ^
  --broker kis ^
  --plan outputs/live_order_plan.json ^
  --approved ^
  --execute ^
  --allow-live-env ^
  --final-confirm I_UNDERSTAND_REAL_ORDER ^
  --allow-symbol 005930 ^
  --require-pre-trade-runbook ^
  --pre-trade-runbook-max-age-minutes 10
```

감사 로그: `require_pre_trade_runbook`, `pre_trade_runbook_guard`, `pre_trade_runbook_passed`, `pre_trade_runbook_path`, `pre_trade_runbook_age_seconds`

### Risk check — stop-loss / take-profit guard ([실전-12], [실전-13])

최신 DB **`real_positions`** 기준으로 미실현 손익·손절/익절 **경고 리포트**만 생성합니다.

- **자동매도·SELL 주문·시장가 없음** — 수동 검토용.
- **`paper_*` 미사용** — `real_positions`만.
- 기본 임계: 손절 **-7%**, 익절 **+15%**, 경고 손실 **-3%**, 경고 이익 **+10%** (standalone `risk-check` CLI로 변경 가능).
- 산출물: **`outputs/risk_alert_YYYYMMDD_HHMMSS.json`**, **`outputs/RISK_ALERT.md`**
- **`--sync-first`**: 미구현 — 사전에 `live-sync-account --network` 권장.

#### Integrated vs standalone

| 방식 | 명령 | 용도 |
|------|------|------|
| **통합 (권장)** | `post-trade-runbook --network --with-summary …` | 주문 직후 risk-check 및 운영 리포트 체인 생성·runbook JSON/MD에 Risk Summary와 Generated Reports 포함 |
| **단독** | `risk-check` | 주기 모니터링·임계값 커스텀(`--stop-loss-pct` 등)·post-trade 없이 점검 |

`post-trade-runbook`의 risk 단계는 **`run_portfolio_risk_check`** 를 `risk-check` CLI와 **동일 함수**로 호출합니다(subprocess 없음).

#### 권장 실전 운영 순서

1. `live-plan`
2. `pre-trade-runbook --network`
3. `live-approve --require-pre-trade-runbook --execute …`
4. `post-trade-runbook --network --with-summary --audit …` (order status → fill → sync → reconcile → risk-check → ops-dashboard → sell-plan → daily-ops-summary → html-dashboard)
5. `notify-alerts --dry-run` — 위험 알림 메시지와 audit 확인
6. 필요 시 `notify-alerts --send` — Telegram/Discord alert-only 전송
7. **manual review** — `POST_TRADE_RISK_ALERT` 시 `RISK_ALERT.md`·`SELL_PLAN.md`·`DAILY_OPS_SUMMARY.md`·`OPS_DASHBOARD.html` 확인 (자동 SELL 없음)

```bash
python main.py risk-check --broker kis --output-dir outputs
python main.py risk-check --broker kis --stop-loss-pct -0.07 --take-profit-pct 0.15 --output-dir outputs
```

종료 코드: **`OK` → 0**, 그 외(`WARNING`·`STOP_LOSS_ALERT` 등) → **1**. **`post-trade-runbook`** 은 `POST_TRADE_RISK_ALERT` 시 **1** (통합 경고).

### Ops dashboard ([실전-14], `ops-dashboard`)

최신 실계좌 운영 상태를 단일 JSON/Markdown으로 요약합니다. 새 KIS HTTP 조회를 하지 않고, 로컬 DB의 최신 `real_account_snapshots`·`real_positions`·최근 `real_order_history`와 `outputs/`의 최신 reconcile/risk/fill 리포트를 읽습니다.

- 산출물: **`outputs/ops_dashboard_YYYYMMDD_HHMMSS.json`**, **`outputs/OPS_DASHBOARD.md`**
- 상태: `OK`, `WARNING`, `RISK_ALERT`, `RECONCILE_MISMATCH`, `NO_DATA`
- **조회/요약 전용**: SELL, 시장가, 자동 반복, 자동 취소, KIS `order-cash` POST 없음.

```bash
python main.py live-sync-account --broker kis --network --output-dir outputs
python main.py reconcile-live-account --broker kis --network --output-dir outputs
python main.py risk-check --broker kis --output-dir outputs
python main.py ops-dashboard --output-dir outputs --recent-orders 10
```

콘솔 예:

```text
DeepSignal ops dashboard
Status: WARNING
Positions: 1
Risk: WARNING
Reconcile: success=True
JSON: outputs/ops_dashboard_20260516_210000.json
Markdown: outputs/OPS_DASHBOARD.md
```

Markdown에는 Account, Positions, Reconcile, Risk, Fills, Recent Orders, Warnings 섹션이 포함됩니다.

### Manual sell plan generator ([실전-15], `sell-plan`)

최신 `real_positions`와 risk/reconcile/ops/fill 리포트를 참고해 운영자 검토용 SELL 계획서만 생성합니다. 이 기능은 **주문 실행 기능이 아니며**, `live-approve`의 SELL 실행도 구현하지 않습니다.

- 산출물: **`outputs/sell_plan_YYYYMMDD_HHMMSS.json`**, **`outputs/SELL_PLAN.md`**
- 상태: `HOLD`, `REVIEW`, `REDUCE`, `EXIT`, `NO_DATA`
- 기본 정책:
  - `pnl_pct <= stop_loss_pct` → `EXIT`, sell ratio `1.0`
  - `pnl_pct <= warn_loss_pct` → `REVIEW`, sell ratio `0.0`
  - `pnl_pct >= take_profit_pct` → `REDUCE`, sell ratio `0.5`
  - 그 외 → `HOLD`
- 기본 threshold: `--stop-loss-pct -0.07`, `--warn-loss-pct -0.03`, `--take-profit-pct 0.15`
- **절대 하지 않음**: SELL API, `order-cash` SELL, 시장가, 자동매도, 자동 반복, 자동 취소, KIS 주문 POST.

```bash
python main.py live-sync-account --broker kis --network --output-dir outputs
python main.py reconcile-live-account --broker kis --network --output-dir outputs
python main.py risk-check --broker kis --output-dir outputs
python main.py ops-dashboard --output-dir outputs
python main.py sell-plan --output-dir outputs
```

콘솔 예:

```text
DeepSignal sell plan
Status: REVIEW
Items: 1
JSON: outputs/sell_plan_20260516_210000.json
Markdown: outputs/SELL_PLAN.md
```

Markdown 예:

```text
# DeepSignal Sell Plan

## Status
- Overall: REVIEW

## Positions
| Symbol | Qty | Avg | Current | PnL % | Action | Suggested Sell |
|--------|-----|-----|---------|-------|--------|----------------|
| 005930 | 1 | 280000.00 | 270500.00 | -3.39% | REVIEW | 0 |

## Important
- This plan does NOT place SELL orders.
- Manual operator review required.
- live-approve SELL execution is not implemented.
```

### Alert-only notification center ([실전-16], `notify-alerts`)

최신 `risk_alert_*.json`, `ops_dashboard_*.json`, `sell_plan_*.json`, `reconcile_live_account_*.json`을 읽어 Telegram 또는 Discord로 위험 상태를 알립니다. 기본은 **dry-run**이며, `--send` 없이는 네트워크 호출을 하지 않습니다. `--include-maintenance`를 붙이면 `weekly_maintenance_*.json`과 `report_health_*.json`도 opt-in source로 읽습니다.

알림 조건:

- risk status가 `WARNING`, `STOP_LOSS_ALERT`, `TAKE_PROFIT_ALERT`, `MIXED_ALERT`
- ops dashboard status가 `WARNING`, `RISK_ALERT`, `RECONCILE_MISMATCH`
- sell plan status가 `REVIEW`, `REDUCE`, `EXIT`
- reconcile `success=false`
- `--include-maintenance` 지정 시 weekly maintenance `WEEKLY_MAINTENANCE_WARNING` / `WEEKLY_MAINTENANCE_CRITICAL`, report health `HEALTH_WARNING` / `HEALTH_NO_DATA` / `HEALTH_CRITICAL`
- `--include-ok` 지정 시 OK/정상 상태도 `INFO`로 포함

환경 변수:

```bash
DEEPSIGNAL_NOTIFY_TELEGRAM_BOT_TOKEN=
DEEPSIGNAL_NOTIFY_TELEGRAM_CHAT_ID=
DEEPSIGNAL_NOTIFY_DISCORD_WEBHOOK_URL=
DEEPSIGNAL_NOTIFY_DEFAULT_CHANNEL=telegram
```

사용 예:

```bash
python main.py notify-alerts --dry-run --output-dir outputs
python main.py notify-alerts --dry-run --include-maintenance --output-dir outputs
python main.py notify-alerts --channel telegram --send --output-dir outputs
python main.py notify-alerts --channel telegram --send --include-maintenance --output-dir outputs
python main.py notify-alerts --channel discord --send --output-dir outputs
```

audit log:

```text
outputs/notification_audit_YYYYMMDD_HHMMSS.json
```

audit에는 `dry_run`, `channel`, `messages`, `results`, `actual_order_attempted=false`, `실제_주문_없음=true`가 기록됩니다. maintenance 메시지는 `source="weekly_maintenance"` 또는 `source="report_health"`이며, `metadata.source_file`, `metadata.maintenance_status` 또는 `metadata.health_status`를 포함합니다.

maintenance alert 예:

```text
[DeepSignal WARNING]
Status: WEEKLY_MAINTENANCE_WARNING
Issues:
- cleanup candidates: 12
Next actions:
- Review WEEKLY_MAINTENANCE.md
This is alert-only. No orders were placed.
```

**중요:** `notify-alerts`는 알림 전용입니다. 주문 실행, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS `order-cash` POST를 수행하지 않습니다.

### Daily operations summary ([실전-17], `daily-ops-summary`)

하루 운영 산출물을 하나의 JSON/Markdown으로 묶습니다. `outputs/`의 오늘 날짜 파일을 우선 사용하고, 오늘 파일이 없으면 기본적으로 최신 파일로 fallback하며 warnings에 기록합니다.

입력:

- `live_account_snapshot_*.json`
- `reconcile_live_account_*.json`
- `risk_alert_*.json`
- `ops_dashboard_*.json`
- `sell_plan_*.json`
- `notification_audit_*.json`

상태 우선순위:

- reconcile `success=false` → `RECONCILE_MISMATCH`
- risk `STOP_LOSS_ALERT` / `TAKE_PROFIT_ALERT` / `MIXED_ALERT` → `RISK_ALERT`
- ops dashboard `RECONCILE_MISMATCH` / `RISK_ALERT` → 동일 반영
- sell plan `EXIT` → `RISK_ALERT`
- sell plan `REDUCE`, risk `WARNING`, sell plan `REVIEW` → `WARNING`
- 데이터 부족 → `NO_DATA`
- 그 외 → `OK`

```bash
python main.py daily-ops-summary --output-dir outputs
python main.py daily-ops-summary --date 2026-05-16 --output-dir outputs
python main.py daily-ops-summary --notify-dry-run --output-dir outputs
```

산출물:

```text
outputs/daily_ops_summary_YYYYMMDD_HHMMSS.json
outputs/DAILY_OPS_SUMMARY.md
```

`--notify-dry-run`은 요약 전에 `notify-alerts` dry-run audit을 생성해 포함합니다. 네트워크 호출은 하지 않습니다.

**중요:** `daily-ops-summary`는 조회/요약 전용입니다. 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS POST를 수행하지 않습니다.

### Static HTML risk dashboard ([실전-18], `html-dashboard`)

`outputs/`의 최신 운영 JSON을 읽어 브라우저에서 열 수 있는 단일 HTML 파일을 생성합니다. 웹서버를 실행하지 않고, 외부 CDN도 사용하지 않습니다.

입력:

- `daily_ops_summary_*.json`
- `ops_dashboard_*.json`
- `risk_alert_*.json`
- `sell_plan_*.json`
- `reconcile_live_account_*.json`
- `live_account_snapshot_*.json`
- `live_fill_summary_*.json`
- `notification_audit_*.json`

```bash
python main.py live-sync-account --broker kis --network --output-dir outputs
python main.py reconcile-live-account --broker kis --network --output-dir outputs
python main.py risk-check --broker kis --output-dir outputs
python main.py ops-dashboard --output-dir outputs
python main.py sell-plan --output-dir outputs
python main.py daily-ops-summary --output-dir outputs
python main.py html-dashboard --output-dir outputs
```

산출물:

```text
outputs/OPS_DASHBOARD.html
```

HTML에는 Overall/Risk/Reconcile/Sell Plan/Last Updated 카드, Account, Positions, Reconcile, Risk Alerts, Sell Plan, Recent Orders/Fills, Notifications, Next Actions 섹션이 포함됩니다. `--open`을 붙이면 생성 후 기본 브라우저로 열 수 있습니다.

**중요:** `html-dashboard`는 정적 로컬 파일 생성만 수행합니다. 웹서버, 네트워크 호출, 실주문, SELL 자동화, 시장가, 자동 반복, 자동 취소, KIS POST를 수행하지 않습니다.

### Report archive cleanup ([실전-20], `cleanup-reports`)

`outputs/`에 쌓이는 운영 JSON 리포트를 보존 정책에 따라 정리합니다. 기본은 **dry-run**이며, `--apply` 없이는 파일을 삭제하거나 이동하지 않습니다.

보존 정책:

- `--keep-days 14`: 최근 14일 안에 수정된 리포트 보존
- `--keep-latest 20`: 카테고리별 최신 20개 보존
- `--archive`: 삭제 대신 `--archive-dir`로 이동
- `--remove-appledouble`: macOS `._*` AppleDouble 메타파일 정리

```bash
python main.py cleanup-reports --output-dir outputs --dry-run
python main.py cleanup-reports --output-dir outputs --apply --keep-days 14 --keep-latest 20
python main.py cleanup-reports --output-dir outputs --apply --archive --archive-dir outputs/archive
python main.py cleanup-reports --output-dir outputs --apply --remove-appledouble
```

항상 보존:

- `OPS_DASHBOARD.html`, `OPS_DASHBOARD.md`, `DAILY_OPS_SUMMARY.md`, `RISK_ALERT.md`, `SELL_PLAN.md`
- `LIVE_ACCOUNT_SNAPSHOT.md`, `RECONCILE_LIVE_ACCOUNT.md`
- `.gitkeep`, `.kis_token_cache.json`

audit:

```text
outputs/report_cleanup_audit_YYYYMMDD_HHMMSS.json
```

audit에는 `dry_run`, `keep_days`, `keep_latest`, `archive`, `candidates`, `deleted`, `archived`, `kept`, `warnings`, `실제_주문_없음=true`, `network_called=false`가 기록됩니다.

**중요:** `cleanup-reports`는 `output_dir` 내부 리포트 파일만 대상으로 합니다. `.env`, DB, 소스 코드, 문서, 스크립트는 정리 대상이 아닙니다. 네트워크 호출, 실주문, SELL 자동화, KIS POST를 수행하지 않습니다.

### Dashboard archive index ([실전-21], `report-index`)

`outputs/`와 선택한 archive 디렉터리의 운영 리포트를 날짜/종류별로 정리한 정적 HTML/Markdown/JSON 인덱스를 생성합니다. 리포트 원문 전체를 복사하지 않고 status, count, 파일 링크 중심으로 요약합니다.

```bash
python main.py report-index --output-dir outputs
python main.py report-index --output-dir outputs --archive-dir outputs/archive
python main.py report-index --output-dir outputs --archive-dir outputs/archive --max-items 200
```

산출물:

```text
outputs/REPORT_INDEX.html
outputs/REPORT_INDEX.md
outputs/report_index_YYYYMMDD_HHMMSS.json
```

HTML/Markdown 구성:

- Summary: total reports, latest report date, risk alerts count, reconcile mismatch count
- Safety Audit: status, `SAFETY_AUDIT.md`, latest `safety_audit_*.json`, updated at, warning/blocked count
- By Date: 날짜별 reports, highest severity, links
- By Category: category별 count, latest, status
- Recent Reports: name, category, date, status, size, link

주간 운영 예:

```bash
python main.py cleanup-reports --output-dir outputs --dry-run
python main.py cleanup-reports --output-dir outputs --apply --archive --archive-dir outputs/archive
python main.py report-index --output-dir outputs --archive-dir outputs/archive
```

**중요:** `report-index`는 정적 로컬 인덱스 파일만 생성합니다. 웹서버, 네트워크 호출, 실주문, SELL 자동화, KIS POST를 수행하지 않습니다.

### One-command dry-run operations ([실전-22], `ops-dry-run`)

하루 운영 점검을 한 번에 실행하는 dry-run 전용 명령입니다. 기본 모드는 로컬 설정/DB/`outputs/` 기반으로만 동작하며, KIS 네트워크 조회는 `--network`를 명시했을 때만 포함합니다.

```bash
python main.py ops-dry-run --output-dir outputs
python main.py ops-dry-run --network --broker kis --output-dir outputs --archive-dir outputs/archive
```

기본 단계:

1. `trading-session-check` 동등 세션 확인
2. `kis-check` offline 설정 검증
3. `risk-check`
4. `ops-dashboard`
5. `sell-plan`
6. `daily-ops-summary`
7. `html-dashboard`
8. `report-index`

`--network` 단계:

- `kis-check --network` 동등 OAuth 확인
- `live-sync-account --broker kis --network` 동등 잔고/포지션 조회 및 DB 저장
- `reconcile-live-account --broker kis --network` 동등 브로커 vs DB 비교

산출물:

```text
outputs/ops_dry_run_YYYYMMDD_HHMMSS.json
outputs/OPS_DRY_RUN.md
```

`trading session closed`는 주말/장외 리포트 생성을 막지 않도록 warning으로만 기록합니다. reconcile mismatch나 risk alert는 최종 상태를 `OPS_DRY_RUN_WARNING`으로 올리고, 단계 실패는 `OPS_DRY_RUN_BLOCKED`로 반영합니다.

**중요:** `ops-dry-run`은 조회/점검/리포트 생성 전용입니다. `live-approve`를 호출하지 않고, SELL 자동화, 시장가, KIS `order-cash` POST, 실주문을 수행하지 않습니다. `--network`가 없으면 KIS OAuth/잔고조회/reconcile 조회도 수행하지 않습니다.

### Lightweight local viewer ([실전-23], `open-dashboard`)

생성된 로컬 운영 리포트 경로를 한 번에 보여주고, 선택한 HTML 파일만 기본 브라우저로 엽니다. 기본 실행은 파일 목록 출력만 수행하며 브라우저를 열지 않습니다.

```bash
python main.py open-dashboard --output-dir outputs
python main.py open-dashboard --output-dir outputs --open
python main.py open-dashboard --output-dir outputs --open-index
python main.py open-dashboard --output-dir outputs --open-all
```

기본 대상:

- `outputs/OPS_DASHBOARD.html`
- `outputs/REPORT_INDEX.html`
- `outputs/SAFETY_AUDIT.md`
- latest `outputs/safety_audit_*.json`
- `outputs/DAILY_OPS_SUMMARY.md`
- `outputs/OPS_DRY_RUN.md`
- `outputs/RISK_ALERT.md`
- `outputs/SELL_PLAN.md`

운영 예:

```bash
python main.py ops-dry-run --output-dir outputs
python main.py open-dashboard --output-dir outputs --open
```

`--open`은 `OPS_DASHBOARD.html`, `--open-index`는 `REPORT_INDEX.html`, `--open-all`은 존재하는 HTML 리포트만 엽니다. Markdown 파일은 경로 안내만 하고 자동으로 열지 않습니다.

**중요:** `open-dashboard`는 로컬 `file://` 리포트만 엽니다. 웹서버 실행, 외부 URL 열기, 네트워크 호출, 실주문, SELL 자동화, KIS POST를 수행하지 않습니다. `output_dir` 밖 경로는 열지 않습니다.

### Report health check ([실전-25], `report-health-check`)

운영 리포트, DB, token cache, `outputs/` 상태를 한 번에 진단합니다. 결과만 `outputs/report_health_YYYYMMDD_HHMMSS.json`과 `outputs/REPORT_HEALTH.md`로 남기며, 파일 정리·삭제·네트워크 조회·알림 전송·주문 실행을 하지 않습니다.

```bash
python main.py report-health-check --output-dir outputs
python main.py report-health-check --output-dir outputs --db-path data/deepsignal.db --max-age-hours 24 --max-output-files 500
```

확인 항목:

- 주요 리포트 존재: `OPS_DASHBOARD.html`, `REPORT_INDEX.html`, `DAILY_OPS_SUMMARY.md`, `RISK_ALERT.md`, `SELL_PLAN.md`, `OPS_DRY_RUN.md`
- 최신 JSON 나이: `live_account_snapshot_*.json`, `reconcile_live_account_*.json`, `risk_alert_*.json`, `ops_dashboard_*.json`, `sell_plan_*.json`, `daily_ops_summary_*.json`
- DB 최신 `real_account_snapshots`와 `real_positions` 로딩 가능 여부
- `outputs/` AppleDouble `._*`, stale dashboard, 파일 수 초과, `.kis_token_cache.json` 만료/만료 임박

`cleanup-reports`와의 차이: `report-health-check`는 진단만 하고 정리하지 않습니다. 실제 정리 후보 확인은 `cleanup-reports --dry-run`, 적용은 운영자가 audit을 확인한 뒤 `cleanup-reports --apply`로 별도 수행합니다.

예상 경고:

```text
DeepSignal report health check
Status: HEALTH_WARNING
Issues:
- WARNING outputs: AppleDouble files found
- WARNING reports: latest risk_alert_*.json is older than 24h
```

**중요:** `report-health-check`는 진단 전용입니다. cleanup 실행, 파일 삭제, 네트워크 호출, 알림 전송, 실주문, SELL 자동화, KIS POST를 수행하지 않습니다.

### Weekly maintenance dry-run ([실전-26], `weekly-maintenance`)

주간 운영 점검에서 반복하던 health check, cleanup dry-run, daily summary, HTML dashboard, report index 생성을 한 번에 실행합니다. 항상 dry-run 전용이며 `--apply`, `--archive`, `--network`, `--send` 옵션을 제공하지 않습니다.

```bash
python main.py weekly-maintenance --output-dir outputs
python main.py weekly-maintenance \
  --output-dir outputs \
  --archive-dir outputs/archive \
  --keep-days 14 \
  --keep-latest 20 \
  --max-age-hours 24 \
  --max-output-files 500
```

실행 단계:

1. `report-health-check`
2. `cleanup-reports --dry-run` 동등 실행
3. `daily-ops-summary`
4. `html-dashboard`
5. `report-index`
6. weekly maintenance summary 생성

산출물:

```text
outputs/weekly_maintenance_YYYYMMDD_HHMMSS.json
outputs/WEEKLY_MAINTENANCE.md
```

상태:

- `WEEKLY_MAINTENANCE_OK`: health/cleanup/리포트 생성이 정상
- `WEEKLY_MAINTENANCE_WARNING`: health warning 또는 cleanup 후보/단계 경고 존재
- `WEEKLY_MAINTENANCE_CRITICAL`: health critical 또는 단계 실패

실제 cleanup 적용은 자동으로 하지 않습니다. `WEEKLY_MAINTENANCE.md`와 cleanup audit을 확인한 뒤 운영자가 별도로 `cleanup-reports --apply`를 수동 실행해야 합니다.

주간 알림 dry-run:

```bash
python main.py weekly-maintenance --output-dir outputs --archive-dir outputs/archive
python main.py notify-alerts --dry-run --include-maintenance --output-dir outputs
```

실제 Telegram/Discord 전송은 maintenance audit을 확인한 뒤 `--send --include-maintenance`를 명시했을 때만 수행합니다.

**중요:** `weekly-maintenance`는 점검/리포트 생성 전용입니다. 파일 삭제, archive 이동, 네트워크 호출, 알림 전송, 실주문, SELL 자동화, KIS POST를 수행하지 않습니다.

### One-command weekly report bundle ([실전-28], `weekly-report-bundle`)

주간 운영 결과를 하나의 로컬 번들 폴더로 복사하고 `BUNDLE_INDEX.md` / `BUNDLE_INDEX.html`을 생성합니다. 기본 실행은 `weekly-maintenance`, `notify-alerts --dry-run --include-maintenance`, `report-index`, `html-dashboard`를 네트워크 없이 실행한 뒤 핵심 리포트만 복사합니다.

```bash
python main.py weekly-report-bundle --output-dir outputs
python main.py weekly-report-bundle --output-dir outputs --zip
python main.py weekly-report-bundle --output-dir outputs --open
```

산출물:

```text
outputs/weekly_bundles/weekly_bundle_YYYYMMDD_HHMMSS/
outputs/weekly_bundles/weekly_bundle_YYYYMMDD_HHMMSS/BUNDLE_INDEX.md
outputs/weekly_bundles/weekly_bundle_YYYYMMDD_HHMMSS/BUNDLE_INDEX.html
```

`--zip`을 붙였을 때만 아래 파일도 생성합니다.

```text
outputs/weekly_bundles/weekly_bundle_YYYYMMDD_HHMMSS.zip
```

번들 대상:

- `WEEKLY_MAINTENANCE.md`, 최신 `weekly_maintenance_*.json`
- `REPORT_HEALTH.md`, 최신 `report_health_*.json`
- `REPORT_INDEX.html`, `REPORT_INDEX.md`, 최신 `report_index_*.json`
- `OPS_DASHBOARD.html`, `DAILY_OPS_SUMMARY.md`, `RISK_ALERT.md`, `SELL_PLAN.md`
- 최신 `notification_audit_*.json`, 최신 cleanup audit
- `OPS_DRY_RUN.md`가 있으면 포함

안전 제외:

- `.env`
- `.kis_token_cache.json`
- `data/*.db` / SQLite DB
- 소스 코드
- API key, app secret, token cache

`weekly-maintenance`와의 차이: `weekly-maintenance`는 점검 리포트를 생성하고, `weekly-report-bundle`은 그 결과와 관련 HTML/Markdown/JSON을 읽기 편한 폴더로 복사해 보관합니다.

**중요:** `weekly-report-bundle`은 리포트 생성/복사/번들링만 수행합니다. 파일 삭제, archive 이동, 네트워크 호출, 알림 실제 전송, 실주문, SELL 자동화, KIS POST를 수행하지 않습니다.

### Scheduled reminder / checklist only ([실전-29], `generate-checklists`)

운영자가 daily/weekly 절차를 기억하지 않아도 되도록 Markdown 체크리스트를 생성합니다. 실제 스케줄러가 아니며 cron, launchd, plist, 자동 실행, 네트워크 호출, 실주문, SELL 자동화, KIS POST를 만들거나 수행하지 않습니다.

```bash
python main.py generate-checklists --output-dir outputs/checklists
```

산출물:

```text
outputs/checklists/DAILY_CHECKLIST.md
outputs/checklists/PRE_MARKET_CHECKLIST.md
outputs/checklists/POST_TRADE_CHECKLIST.md
outputs/checklists/WEEKLY_MAINTENANCE_CHECKLIST.md
outputs/checklists/SAFETY_RULES.md
```

**중요:** 체크리스트의 명령은 운영자가 참고해 수동으로 실행하기 위한 템플릿입니다. `live-approve --execute` 자동화, `--final-confirm` 자동 주입, `.env` 커밋, SELL 자동화, 시장가, KIS POST 직접 호출은 금지합니다.

### Safety audit command ([실전-30], `safety-audit`)

실주문 전 또는 주간 점검 전 로컬 산출물과 선택한 DB를 읽기 전용으로 감사합니다. 체크리스트 존재, `SAFETY_RULES.md` 필수 문구, 주요 운영 리포트, 최신 reconcile/account/risk/fill/audit 파일, pre-trade readiness, partial fill, stale snapshot, final confirmation 자동화 의심 흔적을 확인합니다.

```bash
python main.py safety-audit --output-dir outputs
python main.py safety-audit --output-dir outputs --db-path data/deepsignal.db
python main.py safety-audit --output-dir outputs --strict
```

산출물:

```text
outputs/safety_audit_YYYYMMDD_HHMMSS.json
outputs/SAFETY_AUDIT.md
```

상태와 종료 코드:

- `SAFETY_AUDIT_OK`: 종료 코드 0
- `SAFETY_AUDIT_WARNING`: 종료 코드 0
- `SAFETY_AUDIT_BLOCKED`: 종료 코드 1

`--strict`를 붙이면 WARNING도 BLOCKED로 승격합니다.

**중요:** `safety-audit`는 로컬 리포트 생성 전용입니다. 네트워크 호출, KIS POST, `live-approve` 호출, `--execute` 호출, SELL 자동화, 시장가 주문 기능, cron/launchd/plist/alias/shell script 생성, cleanup apply, archive 이동, 파일 삭제를 수행하지 않습니다. `.env` 값, 토큰, app secret, 계좌번호 원문도 출력하지 않습니다.

### Safety audit dashboard link ([실전-31])

`safety-audit` 실행 후 `report-index`, `html-dashboard`, `open-dashboard`에서 Safety Audit 결과를 로컬 링크로 확인할 수 있습니다.

```bash
python main.py safety-audit --output-dir outputs
python main.py report-index --output-dir outputs --archive-dir outputs/archive
python main.py html-dashboard --output-dir outputs
python main.py open-dashboard --output-dir outputs
```

표시 정보:

- `SAFETY_AUDIT_OK` / `SAFETY_AUDIT_WARNING` / `SAFETY_AUDIT_BLOCKED`
- latest audit time
- `SAFETY_AUDIT.md` 링크
- latest `safety_audit_*.json` 링크
- warning/blocked count

Safety Audit 파일이 없으면 dashboard/index에는 `NOT_AVAILABLE` 또는 `Safety audit has not been generated yet`로 표시됩니다. `SAFETY_AUDIT_OK`는 실주문 허가가 아니며, WARNING/BLOCKED는 수동 점검이 필요합니다.

### Dashboard archive viewer ([실전-32]~[실전-37], `archive-viewer`)

`outputs/`와 `outputs/archive/` 아래의 과거 운영 리포트를 읽기 전용 로컬 HTML로 탐색합니다. Safety Audit, weekly maintenance, report health, risk, reconcile, live account snapshot, live approval audit, fill summary, cleanup audit, weekly bundle index 등을 metadata 중심으로 표시합니다.

```bash
python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive
python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive --limit 100
python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive --trend-days 14
python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive --no-csv
python main.py archive-viewer --output-dir outputs --archive-dir outputs/archive --no-summary-md
python main.py open-dashboard --output-dir outputs --open-archive
```

산출물:

```text
outputs/ARCHIVE_VIEWER.html
outputs/ARCHIVE_VIEWER.csv
outputs/ARCHIVE_VIEWER_SUMMARY.md
outputs/ARCHIVE_VIEWER_PRESETS.json
outputs/archive_viewer_YYYYMMDD_HHMMSS.json
```

`report-index`는 Archive Viewer 섹션에 `ARCHIVE_VIEWER.html`, `ARCHIVE_VIEWER.csv`, `ARCHIVE_VIEWER_SUMMARY.md`, 최신 `archive_viewer_*.json`, total reports, updated time을 표시합니다. `open-dashboard`는 해당 로컬 파일 경로를 함께 보여주며, `--open-archive`는 HTML만 엽니다.

HTML viewer는 JavaScript가 꺼져 있어도 기본 표를 표시합니다. JavaScript가 켜져 있으면 report type, status, severity, text search, date range, Only warnings/errors, Latest only 필터와 Modified Time/Type/Status/Severity/Size 정렬을 사용할 수 있습니다. 기본 정렬은 최신 Modified Time DESC입니다.

`Needs Attention` 섹션은 warning 이상 severity, WARNING/BLOCKED/ERROR/FAILED 계열 status, reconcile mismatch, partial fill open, stale snapshot, safety audit blocked 항목을 빠르게 링크합니다. 이는 운영자 확인 신호이며 실주문 허가나 자동 복구가 아닙니다.

`archive_viewer_*.json`은 `summary`, `filters_available`, `entries`, `needs_attention`, `latest_by_type`를 포함합니다. JSON export도 `.env` 값, token, app secret, account number, KIS raw 원문, DB 내용은 저장하지 않습니다.

[실전-34]부터 `ARCHIVE_VIEWER.html`은 운영자가 읽기 쉬운 한국어 UI label을 제공합니다. 예를 들어 `safety_audit`는 `안전 점검`, `SAFETY_AUDIT_WARNING`은 `안전 점검 경고`, `warning`은 `경고`로 표시됩니다. 내부 JSON export와 `data-status`/`data-type` 같은 machine-readable 값은 기존 영어 raw 값을 유지하며, 화면 표시용 label만 한국어로 변환합니다.

[실전-35]부터 print-friendly CSS가 포함되어 브라우저 인쇄/저장 시 filter UI를 숨기고 summary, needs attention, table이 중심이 되도록 정리합니다. CSV export는 report type/status/severity label과 경로 등 metadata만 저장하고, Markdown summary는 운영 요약·최근 상태·유형별 최신 리포트·주의 필요 항목을 로컬 상대 링크로 정리합니다. `archive_viewer_*.json`에는 `export_files`가 추가되어 생성된 HTML/CSV/Markdown summary 파일명을 기록합니다.

[실전-36]부터 Saved Filter Presets를 제공합니다. `ARCHIVE_VIEWER_PRESETS.json`은 로컬 정적 JSON이며, HTML은 이 프리셋을 inline JS로 적용합니다. 외부 네트워크, 서버, localStorage, DB 조회는 사용하지 않습니다. 기본 프리셋은 `주의 필요 항목`, `최신 리포트만`, `안전 점검만`, `리스크/정합성`, `실거래 감사`입니다.

[실전-37]부터 Archive Trend Analytics를 제공합니다. `entries` metadata를 기반으로 `by_day`, `by_report_type`, `by_severity`, `by_status`, 최근 경고/차단 추세, 유형별 주의 항목, 반복 문제 유형을 계산해 HTML의 `운영 추세`, Markdown summary, `archive_viewer_*.json`의 `trend_analytics`에 표시합니다. `--trend-days`는 기본 `7`이며 반복 문제 유형과 trend window 계산에 사용합니다.

[실전-50]부터 Archive Viewer Freshness Source Column을 제공합니다. 각 리포트에 **생성 시각**과 **기준 소스**(`JSON generated_at`, `Markdown 헤더`, `파일 수정시간 fallback`, `알 수 없음`)를 표시합니다. JSON은 `generated_at`/`created_at`/`updated_at`/`timestamp` metadata만 읽고, Markdown은 상단 timestamp block만 읽습니다. `generated_at` 기준이 가장 신뢰도가 높으며, mtime fallback은 구버전 산출물·복사 파일일 수 있으니 Daily AI workflow 운영 시 JSON `generated_at` 확인을 권장합니다. HTML/CSV/JSON/Markdown summary와 `report-index`·`open-dashboard`에 freshness source summary가 포함됩니다.

**중요:** Trend Analytics는 과거 로컬 리포트 metadata 통계일 뿐 실계좌 상태 재조회가 아닙니다. `archive-viewer`는 네트워크 호출, fetch 기반 외부/로컬 파일 재호출, KIS POST, `live-approve`, `--execute`, SELL 자동화, 시장가 주문 기능, cleanup apply, archive 이동, 파일 삭제, DB 파일 내용 읽기, source code 분석, 리포트 원문 전체 분석/export를 수행하지 않습니다.

### AI_CONTEXT bootstrap (`init-context`)

여러 AI 프로젝트를 동시에 운영할 때 Cursor/GPT/Claude가 새 채팅에서도 이어서 작업할 수 있도록 표준 `AI_CONTEXT/` 구조를 생성합니다. 기존 파일은 overwrite하지 않고, 없는 Markdown만 생성합니다.

```bash
python main.py init-context
python main.py init-context --project ./projects/deepsignal
python main.py init-context --project ./projects --all-projects
```

생성 대상:

```text
AI_CONTEXT/
├── PROJECT_CONTEXT.md
├── CURRENT_STATUS.md
├── TODO.md
├── KNOWN_ISSUES.md
├── RULES.md
├── PROMPT_HISTORY.md
└── RESULT_HISTORY.md
```

`init-context`는 로컬 README, dependency 파일, 주요 디렉토리, 테스트/docs 존재 여부만 읽어 초기 metadata를 추론합니다. 네트워크 호출, LLM API 호출, git 변경, shell 실행, destructive operation은 수행하지 않습니다.

### KIS Broker 준비 ([실전-3]~[실전-5], `kis-check`·조회)

한국투자증권 **KIS Open API**용 어댑터. **`kis-check`**·**`live-approve`(비 `--execute`)** 에서는 **`order-cash`를 호출하지 않는다.** **[실전-4]** `live-approve --execute` 가드 통과 시에만 **`order-cash` POST**. **[실전-5]** `live-order-status --network` / `live-sync-account --network` / **`reconcile-live-account --network`** 에서만 **조회용 GET** (`inquire-daily-ccld`, `inquire-balance`; reconcile은 포지션 목록 비교용).

KIS 잔고조회 포지션 파싱 정책:

- 보유 종목은 `output1`의 `pdno`가 6자리 숫자인 행만 사용합니다.
- 수량은 `hldg_qty`를 우선 사용하고, 없을 때만 `ord_psbl_qty`를 fallback으로 사용합니다.
- `quantity <= 0` 행은 실보유 포지션에서 제외합니다.
- `raw_json`은 저장하되, 디버그 출력은 키 목록/행 수 중심으로 제한합니다.

**필수 환경 변수** (`.env` 또는 OS; **값 예시는 실제 키를 넣지 말 것**):

| 변수 | 설명 |
|------|------|
| `KIS_APP_KEY` | 앱키 |
| `KIS_APP_SECRET` | 앱시크릿 |
| `KIS_ACCOUNT_NO` | 계좌번호 **CANO 8자리** (주문 페이로드용) |
| `KIS_ACCOUNT_PRODUCT_CODE` | **ACNT_PRDT_CD** 2자리 |
| `KIS_HTS_ID` | 선택 (HTS ID) |
| `KIS_ENV` | **`paper`**(기본 권장) 또는 **`live`** |

```bash
python main.py kis-check
```

- 기본: **`KIS_*` 검증만**(HTTP 없음). **`KIS_ENV=live`** 이면 실계좌 호스트 안내 경고 출력.
- **`--network`**: **OAuth 토큰** HTTPS만 수행. **주문 API(`order-cash`)는 호출하지 않음**.
- OAuth access token은 기본적으로 `outputs/.kis_token_cache.json`에 캐시됩니다. 이 파일에는 `access_token`, 만료시각, `KIS_ENV`, app key hash만 저장하며 **app secret·계좌번호·app key 원문은 저장하지 않습니다.** `outputs/` 및 토큰 캐시는 버전 관리 대상이 아닙니다.
- `kis-check --network` 직후 `live-sync-account --network` / `reconcile-live-account --network`를 이어서 실행할 때는 이 캐시를 사용해 `tokenP` 반복 호출을 줄입니다. 토큰 문제가 의심될 때만 캐시 파일을 삭제하고 다시 발급하세요.

`.env.example`에 동일 키 이름(주석)을 참고한다.

```bash
python main.py run-daily --paper-rebalance --commission-rate 0.001 --slippage-rate 0.0005
```

- **`--paper-rebalance`**: 모의 단계가 켜져 있을 때(`--no-paper` 아님) 심볼 루프의 **`paper-step`은 건너뛰고**, 루프 **이후** **`paper-rebalance` 한 번**만 실행합니다. 위 비용 옵션은 **`run-daily`에서도 동일**하게 `paper-rebalance` 단계에 전달됩니다. **`--no-paper`** 이면 리밸런싱도 실행하지 않습니다.

```bash
python main.py run-daily
```

- **한 번에** 아래 순서를 실행합니다: **`collect-news`** → **`collect-market`** → **`collect-macro`** → (심볼 목록 순서대로) **`score-symbol`** → **`backtest-symbol`** → **기본은 각 심볼 `paper-step`**. **`--paper-rebalance`** 가 있으면 루프 안 `paper-step`은 생략하고, **마지막에 `paper-rebalance` 1회**(`--no-paper`면 모의 전체 생략).
- 기본 심볼 목록은 `.env`의 **`MARKET_SYMBOLS`**(미설정 시 기본 6종)입니다. **`--symbols AAPL,NVDA`** 를 주면 **그 목록이 우선**하며, `collect-market` 수집 대상도 동일하게 맞춥니다.
- `collect-news` / `collect-market` / **`collect-macro`** 단계는 **RSS·yfinance 네트워크**가 필요합니다(각각 **`--skip-news`**, **`--skip-market`**, **`--skip-macro`** 로 생략 가능). **브로커·실주문 API는 호출하지 않습니다.**
- 종료 시 **`DeepSignal daily pipeline finished`** 와 함께 **`Success`**, **`Symbols`**, **단계별 `Steps:`** 요약이 출력됩니다. 예외가 난 단계는 **`failed`** 로 남고 가능한 다음 단계는 계속 시도합니다(데이터 부족 등은 **`partial_failed`**).
- **`score-symbol`** 단계는 내부적으로 **`score_symbol_to_db`** 를 호출하며, **`--log-json`** 시 각 심볼의 `score:SYM` 단계 `raw`에 **`outcome`**, **`news_score`**, **`news_count`**, **`technical_score`**, **`macro_score`**, **`market_regime`**, **`final_score`** 등이 기록될 수 있고, 루트에 **`macro`** 스냅샷이 추가될 수 있습니다.
- **종료 코드**: … **`reconcile-live-account`** 는 브로커 vs DB **`real_positions`** 가 완전히 일치할 때만 **0**, 불일치 시 **1**입니다. **`live-order-guard-check`** 는 위험 없을 때 **0**, **BLOCKED** 시 **1**입니다. **`live-approve --execute`** 가 **`LIVE_ORDER_BLOCKED_BY_GUARD`** 이면 **1** (KIS POST 없음). …

#### 실패 알림 v1 (선택, 기본 비활성)

- `.env`에 **`NOTIFY_ON_FAILURE=true`** 이고 **`WEBHOOK_URL`** 이 설정되어 있으면, **`run-daily`가 실패(`Success: False`)한 직후** HTTP POST(JSON)로 알림을 한 번 보냅니다. **`WEBHOOK_URL`·토큰은 .env에만** 두고 코드에 넣지 않습니다. **`NOTIFY_TIMEOUT_SECONDS`**(기본 5, 1~300초)로 타임아웃을 조절합니다.
- 페이로드 형식은 **`{"title","message","detail"}`** 이며 `detail`에 `symbols`, `summary`, `errors`(일부), **`log_json_path`**(`--log-json` 사용 시) 등이 들어갑니다. **Discord·Slack 기본 웹훅 스키마와 다를 수 있으므로**, 해당 서비스를 쓰려면 중간 프록시를 두거나 **커스텀 HTTP 수신기**에서 변환하는 방식을 권장합니다.
- **알림 전송 실패(HTTP 오류·타임아웃)는 `run-daily`의 종료 코드를 바꾸지 않습니다.** (파이프라인 실패 시 여전히 **1**.)
- **실전 주문·브로커와 무관**한 부가 기능입니다.

| 옵션 | 의미 |
|------|------|
| `--skip-news` | RSS 뉴스 수집 생략 |
| `--skip-market` | yfinance 시장 수집 생략 |
| `--skip-macro` | 거시 지표 수집(`collect-macro`) 생략 |
| `--symbols SYM,...` | 처리·수집 심볼 목록(쉼표 구분), `MARKET_SYMBOLS` 대신 사용 |
| `--no-backtest` | 종목별 `backtest-symbol` 생략 |
| `--no-paper` | 종목별 `paper-step` 및 `paper-rebalance` 생략 |
| `--paper-rebalance` | 루프 후 포트폴리오 `paper-rebalance` 1회(루프 내 `paper-step` 생략) |
| `--commission-rate` | 모의 리밸런스 수수료율(소수, 기본 0.001) |
| `--slippage-rate` | 모의 리밸런스 슬리피지율(소수, 기본 0.0005) |
| `--min-trade-value` | 목표·현재 평가액 차이가 이 값(USD) 미만이면 거래 생략(기본 10) |
| `--rebalance-threshold` | 위 차이가 `equity×이 값` 미만이면 생략(기본 0.01) |
| `--log-json` | 실행 요약 JSON 파일 저장 |

```bash
python main.py run-daily --skip-news --symbols AAPL,NVDA --no-backtest --log-json
```

- 개별 단계와 **동일한 DB 쓰기 규칙**(`INSERT OR IGNORE` 등)을 따릅니다. **실전 자동매매가 아니며**, OS 스케줄러에 묶기 전 **로컬에서 수동 검증**을 권장합니다.

#### Windows 배치·작업 스케줄러

- **`scripts/run_daily.bat`**: 프로젝트 루트로 이동한 뒤, 있으면 **`.venv\Scripts\python.exe`**, 없으면 **`python`** 으로 `main.py run-daily --log-json`을 실행합니다. 표준 출력·에러는 **`logs/run_daily_console.log`** 에 **추가(append)** 되며, 실행 직전에 `logs` 폴더가 없으면 만듭니다. 실패 시 **`ERRORLEVEL`** 을 한 줄 출력하고 동일 코드로 종료합니다.
- **`scripts/run_dashboard.bat`**: 동일하게 루트·가상환경 우선 후 **`python main.py dashboard`** (조회 전용 GUI).
- **작업 스케줄러 등록(요약)**: “작업 만들기” → **트리거**(예: 매일 원하는 시각) → **동작** “프로그램 시작”에 `cmd.exe`, 인수 예: `/c "D:\경로\Deepsignal\scripts\run_daily.bat"` (경로는 본인 PC의 **절대 경로**로). 끝내기 설정에서 **실패 시 다시 시작** 등은 운영 정책에 맞게 선택합니다. 성공/실패는 **`ERRORLEVEL`** 및 **`logs/run_daily_console.log`** 로 확인합니다.

### 리포트 CLI v1 (`show-*`)

```bash
python main.py show-signals
python main.py show-backtests
python main.py show-paper
```

- SQLite에 쌓인 **`signals`**, **`backtest_results`**, **`paper_*`**를 **콘솔 ASCII 표**로 요약합니다. 외부 패키지 없이 표준 라이브러리만 사용합니다.
- `show-signals`: 최신 **20건** — `symbol`, `signal_date`, `action`, **`technical_score`**, **`news_score`**, `final_score`, `confidence`, `reason` (점수·신뢰도는 소수 둘째 자리, 없으면 `-`; **`macro_score`는 DB에 있으나 CLI 표에서는 생략**).
- `show-backtests`: 최신 **20건** (`symbol`, 기간, `final_value`, 수익률, 거래 수, 승률, MDD).
- `show-paper`: 최신 **`paper_account_snapshots` 1건**, **`paper_positions`** 목록, **`paper_trades`** 최신 20건.
- 데이터가 없으면 **안내 문구**만 출력합니다. **조회 전용**이며 파일보내기·차트는 포함하지 않습니다.

### 대시보드 v1 (`tkinter`, 로컬)

```bash
python main.py dashboard
```

- `DB_PATH`(또는 기본 `data/deepsignal.db`)를 **초기화·적용**한 뒤 **로컬 창**을 띄웁니다. **외부 서버 없음.**
- **Signals / Backtests / Paper** 탭과 **Refresh**로 `show-*`와 동일 범위의 데이터를 **읽기만** 표시합니다. Signals 탭에는 **`technical_score`**, **`news_score`**, **`final_score`** 등이 컬럼으로 표시됩니다(값 없음은 `-`, 소수 둘째 자리).
- **실제 주문·브로커 연동·collect/score/backtest/paper-step 실행 기능은 없습니다.**

## 테스트

```bash
pytest
```

## 검증 (로컬)

```bash
pip install -r requirements.txt
python main.py
python main.py analyze-news AAPL
python main.py analyze-technical AAPL
python main.py score-symbol AAPL
python main.py backtest-symbol AAPL
python main.py backtest-symbol AAPL --include-news
python main.py paper-step AAPL
python main.py show-signals
python main.py show-backtests
python main.py show-paper
python main.py dashboard
python main.py run-daily
python main.py run-daily --skip-news --symbols AAPL,NVDA --no-backtest --log-json
python main.py run-daily --skip-news --symbols AAPL --no-backtest --no-paper --log-json
python -m compileall .
pytest
```

## 주의사항

- **아직 실전 자동매매가 아닙니다.** 브로커 주문은 `live_trading/broker_interface.py`에 추상 인터페이스만 있으며, 백테스트·모의투자 검증 후에만 구현합니다.
- 백테스트 v1은 **수수료·슬리피지·유동성·공매도 제약 등을 반영하지 않습니다.** 결과는 참고용입니다.
- 모의투자 v1은 **실제 주문이 아니며**, 최신 일봉 종가 기준 **단순 가상 체결**입니다. 수수료·슬리피지·실시간 호가 미반영입니다.
- 대시보드 v1은 **조회 전용**이며, **주문·데이터 수집·점수화 트리거를 제공하지 않습니다.**
- `run-daily`는 **뉴스·시장·거시 수집에 인터넷이 필요**(단, `--skip-news` / `--skip-market` / **`--skip-macro`** 로 생략 가능)하며, **실주문·브로커 API는 호출하지 않습니다.** OS 스케줄러에 등록해 자동 실행할 때는 실패·쿼터·DB 잠금·`logs` 폴더 용량(JSON·콘솔 로그)을 고려합니다.
- 백테스트 없이 실전 주문으로 연결하지 않습니다 (`AI_CONTEXT/RULES.md`).

## 문서

프로젝트 방향·할 일·로드맵: `AI_CONTEXT/` 디렉터리.

AI_CONTEXT 표준 구조와 Overmind/GPT/Cursor workflow 활용 방식: `docs/AI_CONTEXT_WORKFLOW.md`.
